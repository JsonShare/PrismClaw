"""Scheduled execution — a cron trigger for an ordinary chat turn.

定时只是「触发方式」，不是另一套执行 mode。本模块只负责调度状态机：
run 状态记录、状态通知、连续失败熔断。触发幂等由 store 的确定性 run.id
提供，本层不再判重、不设跨 worker 执行锁、不猜测执行时长。

实际执行完全复用 app/web/chat/chat_runner._run_chat → run_agent_stream
主链路（由 app 层通过 run_chat 回调注入，engine 层不反向依赖 app/web）：
- 事件走同一套 WS 协议。虚拟 socket 复用主链路的 fanout 投递到该用户的
  活跃连接 —— 用户打开会话就实时显示，没打开就静默；「没有界面」不算
  发送失败，不会触发 WS 故障重试。
- 消息 / 执行时间线 / 交付物元数据由主链路统一落库，因此刷新后仍能看到
  完整的「任务描述 → 执行过程 → 执行结果 → 交付物」。
- 调度层不设执行超时：普通 chat 能跑多久，定时任务就能跑多久。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from prismclaw.schema.scheduler.scheduler import SchedulerDispatchJob
from prismclaw.schema.scheduler.scheduler import SchedulerStoreProtocol as SchedulerStore
from prismclaw.schema.session.ids import build_web_session_key

logger = logging.getLogger(__name__)

# app 层注入：async (*, user_id, conversation_id, content, workspace_id) -> None
RunChatFn = Callable[..., Awaitable[None]]


class ScheduledRunExecutor:
    """Runs one already-claimed fire as an ordinary chat turn.

    Trigger idempotency, dispatch and crash semantics are owned by the
    store/queue; this class only records the run's state and result.
    """

    def __init__(
        self,
        *,
        app: Any,  # FastAPI application instance
        store: SchedulerStore,
        settings: Any,  # SchedulerSettings
        broadcast: Any = None,  # async (user_id, event_type, data) -> None
        run_chat: RunChatFn | None = None,
    ) -> None:
        self._app = app
        self._store = store
        self._settings = settings
        self._broadcast = broadcast
        self._run_chat = run_chat

    async def execute(self, job: SchedulerDispatchJob) -> None:
        """Run one already-claimed fire.

        ``store.add_run`` (deterministic run id + ON CONFLICT DO NOTHING)
        and single-delivery dispatch already guarantee uniqueness, so this
        layer neither re-checks prior runs nor takes an execution lock.
        """
        await self._execute_inner(job)

    async def _execute_inner(self, job: SchedulerDispatchJob) -> None:
        """Core scheduling state machine: statuses, notifications, breaker."""
        started_at = time.time()
        await self._store.update_run(job.run_id, status="running", started_at=started_at)
        await self._store.update_status(job.task_id, "running")
        await self._notify(job.user_id, "scheduled_task.started", {
            "task_id": job.task_id,
            "run_id": job.run_id,
        })

        session_id: str | None = None
        error: str | None = None
        status = "success"

        try:
            session_id = await self._create_session(job)
            if self._run_chat is None:
                raise RuntimeError("run_chat not injected; scheduler cannot execute")
            # 与普通 chat.send 完全同一条执行链路；conversation_id 直接用
            # 已创建的 session_id，_run_chat 会复用这个会话（web: 前缀）。
            # 调度层不套执行超时：普通 chat 能跑多久，定时任务就能跑多久。
            await self._run_chat(
                user_id=job.user_id,
                conversation_id=session_id,
                content=job.instruction,
                workspace_id=job.workspace_id or "default",
            )
        except Exception as exc:
            status = "failed"
            error = str(exc)[:500]
            logger.exception(
                "scheduler.execute.error",
                extra={"run_id": job.run_id, "task_id": job.task_id},
            )

        finished_at = time.time()
        duration = finished_at - started_at

        await self._store.update_run(
            job.run_id,
            status=status,
            finished_at=finished_at,
            session_id=session_id,
            error=error,
            duration_s=round(duration, 2),
        )
        if session_id:
            await self._store.set_conversation(job.task_id, session_id)

        # Failure tracking + circuit breaker
        if status == "failed":
            task = await self._store.get_task(job.task_id)
            if task:
                failures = task.consecutive_failures + 1
                if failures >= self._settings.max_consecutive_failures:
                    await self._store.update_status(
                        job.task_id, "disabled", consecutive_failures=failures,
                    )
                    # Also disable in the persisted task
                    task.enabled = False
                    await self._store.put_task(task)
                    await self._notify(job.user_id, "scheduled_task.disabled", {
                        "task_id": job.task_id,
                        "reason": f"{failures} consecutive failures",
                    })
                    logger.warning(
                        "scheduler.task.circuit_break",
                        extra={"task_id": job.task_id, "failures": failures},
                    )
                else:
                    await self._store.update_status(
                        job.task_id, status, consecutive_failures=failures,
                    )
        else:
            await self._store.update_status(job.task_id, "success", consecutive_failures=0)

        await self._notify(job.user_id, "scheduled_task.completed", {
            "task_id": job.task_id,
            "run_id": job.run_id,
            "status": status,
            "session_id": session_id,
            "duration_s": round(duration, 2),
            "error": error,
        })

    # ── Internal ───────────────────────────────────────────────────────

    async def _create_session(self, job: SchedulerDispatchJob) -> str:
        """Create the session this run writes into (title = task name)."""
        session_store = self._app.state.runner_deps.session_store
        if session_store is None:
            raise RuntimeError("session_store not available; cannot create execution session")
        wid = job.workspace_id or "default"
        session_key = build_web_session_key(job.user_id, wid)
        tree = await session_store.create_new_session(
            session_key=session_key,
            workspace_id=wid,
            agent_id="default",
            title=job.task_name,
        )
        return tree.header.id

    async def _notify(self, user_id: str, event_type: str, data: dict) -> None:
        if self._broadcast:
            try:
                await self._broadcast(user_id, event_type, data)
            except Exception:
                logger.debug("scheduler.notify.failed", extra={"event": event_type})
