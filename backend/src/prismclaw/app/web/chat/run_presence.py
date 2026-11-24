"""跨 worker 的运行态在场证明（presence）。

运行态（``RunControl`` / 内存投影）只存在于 owner 进程；任何其他 worker 都
无法回答"这一轮是否还在写"，此前会一律返回 idle，把进行中的一轮当成定稿。
这里用 Redis 提供一层**可丢的提示**：

- owner 在 run 生命周期内每 3s 刷新 ``prismclaw:run:{conversation_id}``（TTL 10s），
  收尾时删除；
- 任意 worker 读不到键即 ``idle``，读到即 ``active``；Redis 异常返回 ``unknown``
  （保守，客户端不 seal）；
- 跨 worker 中止走 Pub/Sub：非 owner 连接上没有本地 RunControl，直接发布中止
  请求，由 owner 的常驻监听转成 ``rc.stop()``（此前停止按钮静默无效）。

键与频道都**不是事实源**：丢失最多让非 owner worker 短暂误判，下一次心跳或
10s TTL 即恢复；消息内容仍以事件日志与 seq 为准。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

RUN_KEY_PREFIX = "prismclaw:run:"
ABORT_CHANNEL = "prismclaw:run:abort"
RUN_TTL_SECONDS = 10
HEARTBEAT_INTERVAL_SECONDS = 3.0
# 共享 Redis 客户端 socket_timeout=None 且带指数退避重试：Redis 故障时单条
# 命令可能挂很久。presence 只是提示，任何读写都必须有硬超时，绝不能拖慢
# 请求路径或 run 启动。
_OP_TIMEOUT_SECONDS = 1.0
_READ_TIMEOUT_SECONDS = 0.5
_SCAN_TIMEOUT_SECONDS = 2.0

RunState = Literal["active", "idle", "unknown"]


class RunPresence:
    def __init__(
        self,
        redis_client: Redis,
        *,
        on_abort: Callable[[str], None] | None = None,
    ) -> None:
        self._redis = redis_client
        self._on_abort = on_abort
        self._pubsub: Any = None
        self._listener: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()

    # ---- 在场证明 ---------------------------------------------------------
    @staticmethod
    def _key(conversation_id: str) -> str:
        return f"{RUN_KEY_PREFIX}{conversation_id}"

    def set_on_abort(self, on_abort: Callable[[str], None] | None) -> None:
        """热更新中止回调（插件热重载会重建 WebDeps，复用同一个 presence）。"""
        self._on_abort = on_abort

    async def touch(self, conversation_id: str) -> None:
        """owner 心跳：刷新 TTL，并确保中止监听已启动。"""
        if self._redis is None:
            return
        await self.ensure_started()
        try:
            await asyncio.wait_for(
                self._redis.set(self._key(conversation_id), "active", ex=RUN_TTL_SECONDS),
                timeout=_OP_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.debug("run_presence.touch_failed cid=%s", conversation_id, exc_info=True)

    async def clear(self, conversation_id: str) -> None:
        if self._redis is None:
            return
        try:
            await asyncio.wait_for(
                self._redis.delete(self._key(conversation_id)),
                timeout=_OP_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.debug("run_presence.clear_failed cid=%s", conversation_id, exc_info=True)

    async def read(self, conversation_id: str) -> RunState:
        if self._redis is None:
            return "idle"
        try:
            value = await asyncio.wait_for(
                self._redis.get(self._key(conversation_id)),
                timeout=_READ_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.debug("run_presence.read_failed cid=%s", conversation_id, exc_info=True)
            return "unknown"
        return "active" if value else "idle"

    async def list_active(self, conversation_id_prefix: str) -> list[str]:
        """该前缀下仍在运行的会话（含其他 worker 上的）。

        运行中的会话数量级很小，SCAN 的成本可以接受；只作为
        ``/conversations/running`` 的补充候选集，不参与正确性判定。
        """
        if self._redis is None:
            return []
        pattern = f"{RUN_KEY_PREFIX}{conversation_id_prefix}*"
        try:
            return await asyncio.wait_for(self._scan_active(pattern), timeout=_SCAN_TIMEOUT_SECONDS)
        except Exception:
            logger.debug("run_presence.scan_failed", exc_info=True)
            return []

    async def _scan_active(self, pattern: str) -> list[str]:
        found: list[str] = []
        async for key in self._redis.scan_iter(match=pattern, count=200):
            if isinstance(key, str) and key.startswith(RUN_KEY_PREFIX):
                found.append(key[len(RUN_KEY_PREFIX) :])
        return found

    async def heartbeat(self, conversation_id: str) -> None:
        """运行期间的周期性刷新；随 run 任务一起取消。"""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            await self.touch(conversation_id)

    # ---- 跨 worker 中止 ---------------------------------------------------
    async def request_abort(self, conversation_id: str) -> None:
        if self._redis is None:
            return
        await self.ensure_started()
        try:
            await asyncio.wait_for(
                self._redis.publish(ABORT_CHANNEL, conversation_id),
                timeout=_OP_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.warning(
                "run_presence.abort_publish_failed cid=%s", conversation_id, exc_info=True
            )

    # ---- 常驻订阅 ---------------------------------------------------------
    async def ensure_started(self) -> None:
        """幂等启动中止监听；Redis 不可用时不阻塞调用方。"""
        if self._redis is None:
            return
        async with self._start_lock:
            if self._listener is not None and not self._listener.done():
                return
            self._listener = asyncio.create_task(self._listen_forever(), name="run-presence-abort")

    async def _listen_forever(self) -> None:
        backoff = 1.0
        while True:
            try:
                pubsub = self._redis.pubsub()
                await pubsub.subscribe(ABORT_CHANNEL)
                self._pubsub = pubsub
                backoff = 1.0
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    conversation_id = message.get("data")
                    if not isinstance(conversation_id, str) or not conversation_id:
                        continue
                    try:
                        if self._on_abort is not None:
                            self._on_abort(conversation_id)
                    except Exception:
                        logger.warning(
                            "run_presence.abort_handler_failed cid=%s",
                            conversation_id,
                            exc_info=True,
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "run_presence.listener_failed; retrying in %.1fs", backoff, exc_info=True
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
            finally:
                pubsub, self._pubsub = self._pubsub, None
                if pubsub is not None:
                    try:
                        await pubsub.unsubscribe(ABORT_CHANNEL)
                        await pubsub.close()
                    except Exception:
                        logger.debug("run_presence.pubsub_close_failed", exc_info=True)

    async def stop(self) -> None:
        task, self._listener = self._listener, None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("run_presence.listener_stop_failed", exc_info=True)
