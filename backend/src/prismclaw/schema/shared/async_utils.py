"""Zero-dependency async primitives for PrismClaw.

This module provides low-level asyncio helpers that are used across the
engine layer.  Everything here depends *only* on the Python standard
library (asyncio, typing, collections.abc) plus
``prismclaw.types.shared`` (which itself has no dependency on this
module, so no circular-import risk).

Consumers should import from here directly; the original locations
(``engine.agent.run.abort_race`` and ``engine.foundation.runtime_util``)
re-export for backward compatibility.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable
from typing import TypeVar

from prismclaw.schema.shared.exceptions import AgentAbortedError, AgentTimeoutError

T = TypeVar("T")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abort-race helpers (originally engine.agent.run.abort_race)
# ---------------------------------------------------------------------------


class AbortRaceError(RuntimeError):
    """Raised when an awaitable was cancelled because ``abort_event`` set
    before the awaitable completed. We don't reuse ``CancelledError``
    because the awaitable *itself* is still running — we cancelled it
    from outside — so a regular cancellation is technically wrong; this
    exception signals "I deliberately killed you because the user
    pressed Stop"."""

    def __init__(self, reason: str = "abort_event set") -> None:
        super().__init__(reason)
        self.reason = reason


async def await_or_abort(
    awaitable: Awaitable[T],
    abort_event: asyncio.Event | None,
    *,
    timeout: float | None = None,
) -> T:
    """Run ``awaitable`` racing against ``abort_event`` and (optionally)
    a wall-clock ``timeout``.

    Behaviour:

    * If ``awaitable`` completes first, return its result.
    * If ``abort_event`` sets first, cancel the awaitable task and raise
      :class:`AbortRaceError`.
    * If ``timeout`` elapses first, cancel and raise
      :class:`asyncio.TimeoutError`.

    The helper is safe to call with ``abort_event=None`` — it degrades
    to a (slightly heavier) ``asyncio.wait_for`` replacement that does
    not suffer from the "Task was destroyed but it is pending" bug
    because the inner task is always awaited.
    """
    inner = asyncio.ensure_future(awaitable)
    if abort_event is None and timeout is None:
        # No race — just wait. Using create_task + result keeps the
        # contract identical to the racing path, which makes the test
        # simpler and the code easier to reason about.
        try:
            return await inner
        finally:
            if not inner.done():
                inner.cancel()
                try:
                    await inner
                except (asyncio.CancelledError, BaseException):  # noqa: BLE001
                    pass

    waiters: list[asyncio.Future[object]] = [inner]
    if abort_event is not None:
        waiters.append(asyncio.ensure_future(abort_event.wait()))
    if timeout is not None:
        waiters.append(asyncio.ensure_future(asyncio.sleep(timeout)))

    try:
        done, pending = await asyncio.wait(
            waiters, return_when=asyncio.FIRST_COMPLETED
        )
    except BaseException:
        # The outer coroutine itself was cancelled (e.g. task.cancel()).
        # Propagate the cancellation to the inner task and re-raise.
        for w in waiters:
            if not w.done():
                w.cancel()
        raise

    for pending_task in pending:
        pending_task.cancel()
        try:
            await pending_task
        except (asyncio.CancelledError, BaseException):  # noqa: BLE001
            pass

    if inner in done:
        # Inner completed first. The other tasks were *cancelled* (if
        # they were Future-style), so any exception they raised is
        # CancelledError — safe to ignore.
        if inner.cancelled():
            # Cancelled *before* it could return — this happens when
            # asyncio.wait_for-style outer cancellation reaches us.
            raise asyncio.CancelledError("await_or_abort: inner cancelled")
        return inner.result()

    # Inner did not finish first → either abort or timeout won.
    if abort_event is not None and abort_event.is_set():
        raise AbortRaceError("abort_event set before awaitable completed")
    if timeout is not None:
        raise asyncio.TimeoutError(
            f"await_or_abort: timeout of {timeout}s exceeded"
        )
    # Should be unreachable, but keep the contract explicit.
    raise RuntimeError("await_or_abort: no waiter satisfied")


# ---------------------------------------------------------------------------
# Idle-timeout iterator (originally engine.foundation.runtime_util)
# ---------------------------------------------------------------------------


async def iterate_with_idle_timeout(
    source: AsyncIterator[T],
    *,
    idle_seconds: float,
    abort_event: asyncio.Event | None = None,
    kind: str = "idle",
    drain_grace_seconds: float = 2.0,
) -> AsyncIterator[T]:
    if idle_seconds <= 0 and abort_event is None:
        async for item in source:
            yield item
        return

    # idle_seconds <= 0 的语义是「不设 idle 上限」，而不是「立即超时」。
    # 但 abort_event 仍需监听（中止/取消传播），因此不能在此直接 passthrough：
    # 用 wait_timeout=None 让 asyncio.wait 无限期等待任一 waiter 完成，
    # 下面的「真超时」分支在 wait_timeout=None 时天然不可达。
    wait_timeout: float | None = idle_seconds if idle_seconds > 0 else None

    aiter = source.__aiter__()

    while True:
        next_task: asyncio.Task[T] = asyncio.ensure_future(aiter.__anext__())
        abort_task: asyncio.Task[bool] | None = None

        try:
            waiters: list[asyncio.Task[object]] = [next_task]
            if abort_event is not None:
                abort_task = asyncio.ensure_future(abort_event.wait())
                waiters.append(abort_task)

            done, _pending = await asyncio.wait(
                waiters,
                timeout=wait_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if next_task in done:
                try:
                    value = next_task.result()
                except StopAsyncIteration:
                    return
                yield value
                continue

            if abort_task is not None and abort_task in done:
                raise AgentAbortedError(kind=kind)

            # === IDLE TIMEOUT: enter drain phase ===
            # The stream may have more data arriving late (race condition
            # between idle timeout and network delivery). Give a grace window
            # for the pending next_task to complete.
            if drain_grace_seconds > 0:
                logger.debug(
                    "idle timeout fired, drain phase for %.1fs (kind=%s)",
                    drain_grace_seconds, kind,
                )
                drain_done, _ = await asyncio.wait(
                    [next_task],
                    timeout=drain_grace_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if next_task in drain_done:
                    try:
                        value = next_task.result()
                    except StopAsyncIteration:
                        return
                    logger.debug("drain succeeded: caught late chunk")
                    yield value
                    continue

            # Both idle and drain timed out — genuine timeout
            next_task.cancel()
            try:
                await next_task
            except (asyncio.CancelledError, BaseException):
                pass
            raise AgentTimeoutError(kind=kind, limit_seconds=idle_seconds)

        except asyncio.CancelledError:
            next_task.cancel()
            raise
        finally:
            if abort_task is not None:
                abort_task.cancel()
