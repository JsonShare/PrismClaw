from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TypeVar

from prismclaw.schema.shared.exceptions import (
    AgentAbortedError as AgentAbortedError,
)
from prismclaw.schema.shared.exceptions import (
    AgentTimeoutError as AgentTimeoutError,
)

T = TypeVar("T")
logger = logging.getLogger(__name__)


async def run_with_timeout[T](
    coro: Awaitable[T],
    *,
    timeout_s: float,
    abort_event: asyncio.Event | None = None,
    kind: str = "run",
) -> T:
    if timeout_s <= 0 and abort_event is None:
        return await coro

    main_task = asyncio.ensure_future(coro)
    waiters: list[asyncio.Task[object]] = [main_task]

    abort_task: asyncio.Task[bool] | None = None
    if abort_event is not None:
        abort_task = asyncio.ensure_future(abort_event.wait())
        waiters.append(abort_task)

    try:
        timeout_value: float | None = timeout_s if timeout_s > 0 else None
        done, _pending = await asyncio.wait(
            waiters,
            timeout=timeout_value,
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        main_task.cancel()
        if abort_task is not None:
            abort_task.cancel()
        raise

    if main_task in done:
        if abort_task is not None:
            abort_task.cancel()
        return main_task.result()

    main_task.cancel()
    with contextlib.suppress(asyncio.CancelledError, BaseException):
        await main_task

    if abort_task is not None and abort_task in done:
        raise AgentAbortedError(kind=kind)

    raise AgentTimeoutError(kind=kind, limit_seconds=timeout_s)


async def iterate_with_deadline[T](
    source: AsyncIterator[T],
    *,
    deadline_s: float,
    abort_event: asyncio.Event | None = None,
    kind: str = "run",
) -> AsyncIterator[T]:
    if deadline_s <= 0 and abort_event is None:
        async for item in source:
            yield item
        return

    aiter = source.__aiter__()
    end_time = time.monotonic() + deadline_s if deadline_s > 0 else None

    while True:
        next_task: asyncio.Task[T] = asyncio.ensure_future(aiter.__anext__())
        waiters: list[asyncio.Task[object]] = [next_task]
        abort_task: asyncio.Task[bool] | None = None
        if abort_event is not None:
            abort_task = asyncio.ensure_future(abort_event.wait())
            waiters.append(abort_task)

        if end_time is not None:
            remaining = end_time - time.monotonic()
            timeout_value: float | None = max(remaining, 0.0)
        else:
            timeout_value = None

        try:
            done, _pending = await asyncio.wait(
                waiters,
                timeout=timeout_value,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            next_task.cancel()
            if abort_task is not None:
                abort_task.cancel()
            raise

        if next_task in done:
            if abort_task is not None:
                abort_task.cancel()
            try:
                value = next_task.result()
            except StopAsyncIteration:
                return
            yield value
            continue

        next_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, BaseException):
            await next_task

        if abort_task is not None and abort_task in done:
            raise AgentAbortedError(kind=kind)

        elapsed = time.monotonic() - (end_time - deadline_s) if end_time is not None else deadline_s
        raise AgentTimeoutError(kind=kind, limit_seconds=elapsed)


async def race_abort[T](
    coro: Awaitable[T], abort_event: asyncio.Event, *, kind: str = "aborted"
) -> T:
    return await run_with_timeout(coro, timeout_s=0.0, abort_event=abort_event, kind=kind)


def is_abort_set(abort_event: asyncio.Event | None) -> bool:
    return abort_event is not None and abort_event.is_set()


def remaining_run_seconds(run_deadline_s: float, run_started: float) -> float | None:
    if run_deadline_s <= 0:
        return None
    remaining = run_deadline_s - (time.monotonic() - run_started)
    return remaining


async def with_safety_timeout[T](
    fn: Callable[[], Awaitable[T]],
    *,
    timeout_s: float,
    abort_event: asyncio.Event | None = None,
    on_cancel: Callable[[], None] | None = None,
    kind: str = "compaction",
) -> T:
    try:
        return await run_with_timeout(fn(), timeout_s=timeout_s, abort_event=abort_event, kind=kind)
    except (AgentTimeoutError, AgentAbortedError):
        if on_cancel is not None:
            with contextlib.suppress(Exception):
                on_cancel()
        raise
