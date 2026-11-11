"""状态事件发射 — 域无关的 store mixin。

任何带长任务状态写点的域 store（legal / medical / 自建 agent）继承此 mixin
即自动获得「写点唯一化 + fire-and-forget + 子任务过滤」能力。域层只负责实现
自己的 SQL，事件发射的全部通用逻辑由 mixin 接管。

写点唯一化：store 在真实状态写点（UPDATE ... RETURNING）提交成功后调
self._fire_status_events(rows)，mixin 负责异步派发。周期心跳写点
（不改变 status 的快照刷新）不应调用。

新域接入零成本：
1. Store 继承 StatusEmitterMixin
2. UPDATE 写点加 RETURNING 子句，提交后 self._fire_status_events(rows)
3. （可选）覆盖 _emit_row / _build_status_event 适配域专属规则
4. Bundle 声明 task_store = 你的 store 实例 → DomainRegistry 自动完成 emitter 接线
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


class StatusEmitterMixin:
    """域 store 的状态事件发射 mixin。

    子类构造器（或 mixin 自身）必须初始化：
        self._status_listener: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self._status_fire_tasks: set[asyncio.Task[None]] = set()

    （mixin 自带 __init__，子类 super() 即可）
    """

    def __init__(self) -> None:
        self._status_listener: Callable[[dict[str, Any]], Awaitable[None]] | None = None
        self._status_fire_tasks: set[asyncio.Task[None]] = set()

    # ── 注入（框架层 / Bundle 调） ──────────────────────────

    def set_status_listener(
        self, cb: Callable[[dict[str, Any]], Awaitable[None]] | None,
    ) -> None:
        """注入任务状态变更监听器（web 层 emitter 接线用）。

        监听器为 None 时 _fire_status_events 直接短路返回，写路径零开销。
        """
        self._status_listener = cb

    # ── 写点调（store 真实状态写点提交后） ─────────────────

    def _fire_status_events(self, rows: list[dict[str, Any]]) -> None:
        """提交成功后派发状态事件（fire-and-forget，绝不阻塞写路径）。

        每行一个 asyncio.create_task，Task 存入 _status_fire_tasks 持强引用防 GC，
        done 后 discard。子类可覆盖 _emit_row 做子任务过滤。
        """
        cb = self._status_listener
        if cb is None:
            return
        for row in rows:
            if not self._emit_row(row):
                continue
            task = asyncio.create_task(self._dispatch_status_event(cb, row))
            self._status_fire_tasks.add(task)
            task.add_done_callback(self._status_fire_tasks.discard)

    # ── 子类可覆盖（适配域专属规则） ────────────────────────

    def _emit_row(self, row: dict[str, Any]) -> bool:
        """判定该 row 是否应该发帧。子类覆盖实现子任务过滤等域专属规则。

        默认全部发帧；子类可覆盖为如 parent_id IS None 的过滤。
        """
        return True

    def _build_status_event(self, row: dict[str, Any]) -> dict[str, Any]:
        """从 store 返回的 row 构建事件 payload。子类覆盖适配表结构差异。

        默认取 task_id / status / updated_at；子类可覆盖补充
        workspace_id / stage / progress 等域专属字段。
        """
        updated_at = row.get("updated_at")
        return {
            "task_id": int(row.get("id", row.get("task_id"))),
            "status": row["status"],
            "updated_at": updated_at.isoformat() if isinstance(updated_at, datetime) else None,
        }

    # ── 内部 ──────────────────────────────────────────────

    async def _dispatch_status_event(
        self,
        cb: Callable[[dict[str, Any]], Awaitable[None]],
        row: dict[str, Any],
    ) -> None:
        """构建事件 payload 并投递。全程吞异常记 warning，保护写路径。

        CancelledError 是 BaseException，不会被 except Exception 吞掉。
        """
        try:
            event = self._build_status_event(row)
            await cb(event)
        except Exception:
            logger.warning(
                "域任务状态事件投递失败: task_id=%s",
                row.get("id", row.get("task_id")),
                exc_info=True,
            )
