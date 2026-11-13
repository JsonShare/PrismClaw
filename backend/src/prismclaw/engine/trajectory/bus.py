"""Core event bus — **core-domained, trajectory-agnostic**.

Implements the PrismClaw variant of Session.append → firehose → Projection
architecture (see docs/trajectory-upgrade-plan-v2-decoupled.md §3.1 / §3.3).

Core publisher (runner.py, context_helpers.py, turn_preparation.py) imports
ONLY from `prismclaw.engine.trajectory.bus` and `prismclaw.engine.trajectory.bus_events`. It does **NOT**
import `prismclaw.engine.trajectory.*` — trajectory is a downstream subscriber,
not a core dependency.

Design principles (all enforced by tests):
1. **Subscriber failure = swallowed**. Any exception raised by a sync or async
   handler is `logger.warning`'d and dropped. Core publish() call NEVER raises.
   It's a firehose; subscribers must never block baseline.
2. **Publish = fire-and-forget for sync publishers**. `publish(event)` returns
   once all sync handlers complete; async handlers are scheduled via
   `asyncio.create_task()` and awaited only when `SessionBus.wait_drained()`
   is called (explicit drain point = `run_agent_stream` teardown / app lifespan
   shutdown).
3. **Ordering**. Within one event type, handlers run in registration order.
   No lock — bus is assumed single-task-owned per session (the agent runner
   loop is serialised per RunRequest). For multi-event parallel safety we
   暂不启用严格的 `frozen event` 约束；apply() 里会拷贝用到的
   payload 引用以避免突变 bug。
4. **No backpressure**. Handlers are not awaited inline for sync publishers
   unless the caller explicitly calls `await publish_async(event)`.
5. **AsyncEvent** is used for sync+async subscriptions; the same handler can
   be registered twice (sync and async) if needed but we expose a single
   typed `subscribe(T, handler)` dispatch that inspects async-ness.

Usage (core publisher):
```python
    from prismclaw.engine.trajectory.bus import SessionBus, current_bus
    from prismclaw.engine.trajectory.bus_events import ProjectorOutputDone

    bus: SessionBus = current_bus()  # ContextVar (per-session)
    bus.publish(ProjectorOutputDone(
        session_id=..., iteration=...,
        messages=..., roles=("user","assistant"),
        head=(), tail=(),
    ))
```

Usage (subscriber, e.g. Trajectory module):
```python
    from prismclaw.engine.trajectory.bus import SessionBus
    from prismclaw.engine.trajectory.bus_events import PackerFinalDone

    bus: SessionBus = get_bus_from_context()
    bus.subscribe(PackerFinalDone, projection._on_packer_final)
```

Unloading is explicit (`unsubscribe_all()`) for lifespan shutdown.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Type, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")
# Handler signature: receives the event object, returns nothing (or awaitable).
SyncHandler = Callable[[Any], None]
AsyncHandler = Callable[[Any], Awaitable[None]]
Handler = SyncHandler | AsyncHandler

# Public context-var key for passing the SessionBus around the same way we
# pass TrajectoryAggregator today. A No-op bus (publish is O(1) NOP) is the
# default so callers in OFF mode don't even allocate.
_current_bus: ContextVar[SessionBus | None] = ContextVar(
    "prismclaw.engine.bus.current_bus", default=None,
)


def current_bus() -> "SessionBus":
    """Return the current session bus.

    Falls back to a thread/context-local no-op singleton so callers never
    need to do `if bus is None` guards anywhere. The no-op bus has 0
    subscribers; publish() is O(1) (one dict lookup + return).
    """
    b = _current_bus.get()
    if b is None:
        return NOOP_BUS
    return b


@dataclass
class _BusToken:
    """Opaque token returned by `ContextManager __enter__`.

    Exposed so `AsyncExitStack.enter_context()` (which expects the object
    returned by __enter__ to itself be valid inside __exit__ via identity)
    works correctly when resetting the ContextVar.
    """

    reset_value: Any
    bus: "SessionBus"


class SessionBus:
    """Typed publish/subscribe event bus with failure isolation.

    Instance attributes:
      _sync_handlers:  event_type_cls → list[sync callables]
      _async_handlers: event_type_cls → list[async callables]
      _in_flight:      set[asyncio.Task] — tasks created by publish().
                       Cleared when tasks resolve OR on wait_drained().
    """

    __slots__ = ("_sync_handlers", "_async_handlers", "_reducers", "_in_flight", "_is_noop")

    def __init__(self, *, is_noop: bool = False) -> None:
        # Use object.__new__ level of type as key — plain `type(Event)`.
        self._sync_handlers: dict[type, list[SyncHandler]] = defaultdict(list)
        self._async_handlers: dict[type, list[AsyncHandler]] = defaultdict(list)
        self._reducers: dict[type, list[SyncHandler]] = defaultdict(list)
        self._in_flight: set[asyncio.Task[None]] = set()
        self._is_noop: bool = is_noop

    # ------------------------------------------------------------------ API

    def subscribe(self, event_type: Type[T], handler: Handler) -> None:
        """Register `handler` for events of `event_type`.

        Async handlers (`async def fn(ev)`) get scheduled via create_task on
        `publish()`; sync handlers run inline (in-process, synchronous with
        the publisher's `publish()` call). Exceptions = swallowed, WARN log.
        """
        if self._is_noop:
            return
        import inspect

        if inspect.iscoroutinefunction(handler):
            self._async_handlers[event_type].append(handler)  # type: ignore[arg-type]
        else:
            self._sync_handlers[event_type].append(handler)  # type: ignore[arg-type]

    def register_reducer(self, event_type: type, reducer: SyncHandler) -> None:
        """Register a **reducer** callback for events of `event_type`.

        Reducers run **before** handlers on every `publish()` call.
        They are always synchronous and should modify only the subscriber's
        own instance state (not shared mutable state on the Bus).

        Reducer failure = swallowed + WARN log.  A failing reducer does NOT
        block subsequent reducers or handlers.
        """
        if self._is_noop:
            return
        self._reducers[event_type].append(reducer)

    def unsubscribe(self, event_type: Type[T], handler: Handler) -> bool:
        """Remove handler. Returns True if actually removed."""
        if self._is_noop:
            return False
        removed = False
        lst = self._sync_handlers.get(event_type) or []
        if handler in lst:
            lst.remove(handler)
            removed = True
        alst = self._async_handlers.get(event_type) or []
        if handler in alst:
            alst.remove(handler)
            removed = True
        return removed

    def unsubscribe_all(self) -> None:
        """Clear all handlers and reducers (lifecycle shutdown / plugin unload)."""
        self._sync_handlers.clear()
        self._async_handlers.clear()
        self._reducers.clear()
        # Cancel in-flight tasks cleanly on shutdown so they don't hold
        # onto references to a bus whose projection Redis client died.
        for t in list(self._in_flight):
            if not t.done():
                t.cancel()
        self._in_flight.clear()

    def publish(self, event: object) -> None:
        """Fire `event` to all registered handlers. NEVER RAISES.

        Execution order: reducers → sync handlers → async handlers.

        - Reducers run inline first (deterministic order, registration order).
        - Sync handlers run inline (deterministic order, registration order).
        - Async handlers are scheduled via `create_task(coro)` and tracked.
        """
        if self._is_noop:
            return
        et = type(event)
        # --- reducers — inline, fail-safe, BEFORE handlers ---
        for reducer in self._reducers.get(et) or ():
            try:
                reducer(event)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "SessionBus.reducer failed event=%s reducer=%s: %s",
                    et.__name__, getattr(reducer, "__qualname__", repr(reducer)), exc,
                )
        # --- sync path — inline, fail-safe ---
        syncs = self._sync_handlers.get(et) or ()
        asyncs = self._async_handlers.get(et) or ()
        if not syncs and not asyncs:
            return
        # --- sync path — inline, fail-safe ---
        for fn in syncs:
            try:
                fn(event)
            except Exception as exc:  # noqa: BLE001  -- deliberate swallow
                logger.warning(
                    "SessionBus.sync_handler failed event=%s handler=%s: %s",
                    et.__name__, getattr(fn, "__qualname__", repr(fn)), exc,
                )
        # --- async path — schedule tasks, hold in _in_flight ---
        if asyncs:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # No event loop (e.g. unit tests run sync). Log + skip.
                logger.warning(
                    "SessionBus.publish called without running event loop; "
                    "%d async handler(s) dropped for event=%s",
                    len(asyncs), et.__name__,
                )
                return
            for fn in asyncs:
                coro = self._wrap_async_handler(fn, event, et.__name__)
                task = loop.create_task(coro)
                self._in_flight.add(task)
                task.add_done_callback(self._in_flight.discard)

    async def publish_async(self, event: object) -> None:
        """Publish and AWAIT async handlers. Used at explicit drain points.

        Execution order: reducers → sync handlers → async handlers (awaited).

        Sync handlers still run inline immediately. Async handlers are
        awaited in parallel via `gather(return_exceptions=True)`.
        """
        if self._is_noop:
            return
        et = type(event)
        # --- reducers — inline, fail-safe, BEFORE handlers ---
        for reducer in self._reducers.get(et) or ():
            try:
                reducer(event)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "SessionBus.publish_async reducer failed event=%s reducer=%s: %s",
                    et.__name__, getattr(reducer, "__qualname__", repr(reducer)), exc,
                )
        # --- sync + async handlers ---
        syncs = self._sync_handlers.get(et) or ()
        asyncs = self._async_handlers.get(et) or ()
        for fn in syncs:
            try:
                fn(event)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "SessionBus.publish_async sync_handler failed event=%s "
                    "handler=%s: %s",
                    et.__name__, getattr(fn, "__qualname__", repr(fn)), exc,
                )
        if asyncs:
            await asyncio.gather(
                *(self._wrap_async_handler(fn, event, et.__name__) for fn in asyncs),
                return_exceptions=True,
            )

    async def wait_drained(self, *, timeout_s: float | None = 5.0) -> int:
        """Await all in-flight async tasks.

        Returns the number of tasks awaited. On timeout, un-finished tasks
        are cancelled and removed from _in_flight (no leaked references,
        but some audit data may be dropped — acceptable for a diagnostic
        firehose, not acceptable for core logic which must never depend on
        this bus).
        """
        if not self._in_flight:
            return 0
        tasks = list(self._in_flight)
        self._in_flight.difference_update(tasks)
        done, pending = await asyncio.wait(tasks, timeout=timeout_s)
        n = len(done)
        if pending:
            logger.warning(
                "SessionBus.wait_drained timed out after %.2fs; "
                "%d task(s) cancelled (audit firehose — acceptable data loss).",
                timeout_s if timeout_s is not None else float("inf"),
                len(pending),
            )
            for pt in pending:
                pt.cancel()
        return n

    # ------------------------------------------------------------------ internals

    @staticmethod
    async def _wrap_async_handler(
        fn: AsyncHandler, event: object, evname: str
    ) -> None:
        try:
            await fn(event)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "SessionBus.async_handler failed event=%s handler=%s: %s",
                evname, getattr(fn, "__qualname__", repr(fn)), exc,
            )

    # ContextVar integration — mirror bind_aggregator's API.
    def bind(self) -> "_BusCtxManager":
        """Return a context manager that sets `_current_bus` to `self`.

        Usage:
            with bus.bind():
                session_bus_ctx.get() is bus  # True
            # token auto-reset on __exit__.
        """
        return _BusCtxManager(self)


@dataclass
class _BusCtxManager:
    bus: SessionBus
    _token: Any = field(default=None, repr=False)

    def __enter__(self) -> SessionBus:
        self._token = _current_bus.set(self.bus)
        return self.bus

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._token is not None:
            try:
                _current_bus.reset(self._token)
            except ValueError:
                # ContextVar.reset(token) 要求 token 必须在同一个 async context
                # 里创建 — 当这个 ctx manager 被跨 context 关闭（典型场景：
                # async generator 提前抛 GeneratorExit，__aexit__ 在不同的
                # context 里执行）时 token 无法 reset，让 context 自己销毁
                # 时自动清理 ContextVar 即可，这里静默忽略。
                pass
            self._token = None


# Singleton no-op bus. Instance methods short-circuit via _is_noop=True.
# Safe to share across threads/loops because subscribe/publish on NOOP_BUS
# never writes shared state.
NOOP_BUS: SessionBus = SessionBus(is_noop=True)
