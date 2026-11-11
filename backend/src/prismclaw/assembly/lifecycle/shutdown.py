from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


async def _drain_remaining_pending_tasks(*, grace_s: float = 0.2) -> tuple[list[str], int]:
    """Best-effort drain of tasks that were not registered with any manager.

    返回 ``(unowned_task_names, cancelled_count)``——名字必须回传并落日志，
    否则「谁在停机时还活着」永远只能靠 CPython 的
    ``Task was destroyed but it is pending!`` 警告猜。

    取消仍会发生（这些任务不属于任何 owner，没有协作式停机协议），但
    触发前先记录归属线索：本函数是**审计网**，不是常规停机路径。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return [], 0
    try:
        all_tasks = asyncio.all_tasks(loop)
    except Exception:
        return [], 0
    current = asyncio.current_task(loop)
    pending = [t for t in all_tasks if t is not current and not t.done()]
    if not pending:
        return [], 0
    names = sorted({t.get_name() for t in pending})
    logger.warning(
        "shutdown.drain.unowned_tasks count=%d names=%s",
        len(pending),
        names,
    )
    for t in pending:
        try:
            t.cancel(msg="shutdown: draining leftover pending tasks")
        except Exception:
            pass
    try:
        await asyncio.wait(pending, timeout=grace_s, return_when=asyncio.ALL_COMPLETED)
    except Exception:
        pass
    cancelled = 0
    timed_out = 0
    for t in pending:
        if not t.done():
            # leave it — the loop will probably reap it soon, but we've at
            # least sent cancellation which silences most of the noisy log.
            timed_out += 1
            continue
        try:
            t.result()
        except asyncio.CancelledError:
            cancelled += 1
        except Exception:
            pass
    if timed_out:
        logger.warning(
            "shutdown.drain.unfinished count=%d (loop will reap them)", timed_out,
        )
    return names, cancelled


async def shutdown_app_runtime(
    app: Any,
    *,
    close_client_fn: Callable[[Any], Awaitable[None]],
) -> None:
    state = getattr(app, "state", None)
    if state is None:
        raise RuntimeError("app.state is not available")

    # 停机闩锁必须在任何取消发生之前置位：hook/执行层据此把停机期取消
    # 归类为预期行为（CANCELLED），而不是「Hook execution failed」故障。
    from prismclaw.infra.lifecycle_state import mark_shutting_down

    mark_shutting_down()

    worker_registry = getattr(state, "worker_registry", None)
    if worker_registry is not None:
        await worker_registry.deregister()

    # FIRST: release all async Event waiters (tool approvals, WS heartbeat,
    # fire-and-forget tasks that were spawned outside TaskManager).  We must
    # do this *before* we cancel TaskManager-owned tasks or the approval
    # waiter frame (which is the classic source of
    # ``Task was destroyed but it is pending coro=<Event.wait()>``) will be
    # cancelled while still not-set, and CPython will log the warning even
    # though cancellation was issued.
    _shutdown_web_globals_fn = getattr(state, "shutdown_web_globals_fn", None)
    if _shutdown_web_globals_fn is not None:
        try:
            await _shutdown_web_globals_fn(
                grace_s=0.3,
                close_client_fn=close_client_fn,
                app_state=state,
            )
        except Exception:
            logger.exception("web_channel_runtime shutdown failed")

    mcp_manager = getattr(state, "mcp_manager", None)
    if mcp_manager is not None:
        try:
            await mcp_manager.shutdown()
        except Exception:
            logger.exception("mcp_manager.shutdown failed")

    task_manager = getattr(state, "task_manager", None)
    if task_manager is not None:
        report = await task_manager.shutdown()
        logger.info(
            "shutdown drain complete: completed=%d cancelled=%d timed_out=%d failed=%d duration=%.2fs",
            report.completed,
            report.cancelled,
            report.timed_out,
            report.failed,
            report.total_duration_s,
        )

    memory_store = getattr(state, "memory_store", None)
    if memory_store is not None:
        try:
            await memory_store.close()
        except Exception:
            logger.warning("memory_store close failed", exc_info=True)

    # SAFETY NET: any stray pending tasks that were registered with neither
    # the web runtime, mcp manager, nor the task manager (for instance tasks
    # created via bare ``asyncio.create_task`` in storage migrations, one-off
    # background utils, etc.) get a short cancel+wait so they do not log as
    # ``Task was destroyed but it is pending!``.
    try:
        n_unowned, n_cancelled = await _drain_remaining_pending_tasks(grace_s=0.2)
        if n_unowned:
            logger.info(
                "post-shutdown drain swept %d leftover tasks (cancelled=%d) names=%s",
                len(n_unowned),
                n_cancelled,
                n_unowned,
            )
    except Exception:
        logger.debug("leftover task drain failed", exc_info=True)

    await close_client_fn(getattr(state, "redis_client", None))
