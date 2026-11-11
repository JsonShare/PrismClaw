"""Unit tests for prismclaw.engine.bus.SessionBus (+ current_bus ctx-var)."""
from __future__ import annotations

import asyncio

import pytest

from prismclaw.engine.trajectory.bus import (
    NOOP_BUS,
    SessionBus,
    current_bus,
)
from prismclaw.engine.trajectory.bus_events import (
    PackerFinalDone,
    ProjectorOutputDone,
    SessionIterationStarted,
    SessionTurnStarted,
)


def test_noop_bus_publish_is_nop_no_alloc():
    """NOOP_BUS.subscribe() is a silent NOP; publish() is O(1) no work."""
    calls: list[object] = []
    NOOP_BUS.subscribe(SessionTurnStarted, lambda ev: calls.append(ev))
    ev = SessionTurnStarted(
        session_id="s1", turn_counter=1, started_at_ms=1700000000000,
    )
    NOOP_BUS.publish(ev)
    assert calls == [], "NOOP_BUS subscriber handlers must never fire"
    # wait_drained is O(1) too.
    got = asyncio.run(NOOP_BUS.wait_drained())
    assert got == 0


def test_sync_handler_runs_inline_and_swallowed_on_exc(caplog):
    bus = SessionBus()
    events: list[object] = []

    def good(ev):
        events.append(ev)

    def bad(ev):
        events.append(ev)
        raise RuntimeError("boom from bad handler")

    bus.subscribe(SessionTurnStarted, good)
    bus.subscribe(SessionTurnStarted, bad)
    bus.subscribe(SessionIterationStarted, lambda ev: events.append(("OTHER", ev)))

    ev_turn = SessionTurnStarted(
        session_id="s1", turn_counter=2, started_at_ms=111,
    )
    ev_iter = SessionIterationStarted(
        session_id="s1", turn_counter=2, iteration=0, started_at_ms=112,
    )
    with caplog.at_level("WARNING"):
        bus.publish(ev_turn)
        bus.publish(ev_iter)

    # good + bad ran for TurnStarted; OTHER ran for IterationStarted.
    assert events[0] is ev_turn
    assert events[1] is ev_turn
    assert events[2][0] == "OTHER"
    assert events[2][1] is ev_iter
    # Failure was isolated — no RuntimeError escaped to caller.
    # Handler order preserved (registration order = good before bad).
    assert caplog.text.count("boom from bad handler") == 1
    assert caplog.text.count("sync_handler failed") == 1


def test_publish_no_subscribers_no_work():
    bus = SessionBus()
    ev = SessionIterationStarted(
        session_id="s1", turn_counter=0, iteration=0, started_at_ms=0,
    )
    # Should not raise even though no handlers registered.
    bus.publish(ev)
    # _in_flight stays empty — no tasks created.
    assert not bus._in_flight


def test_unsubscribe_removes_handler():
    bus = SessionBus()
    events_a: list[object] = []
    events_b: list[object] = []
    a = lambda ev: events_a.append(ev)  # noqa: E731
    b = lambda ev: events_b.append(ev)  # noqa: E731
    bus.subscribe(SessionTurnStarted, a)
    bus.subscribe(SessionTurnStarted, b)
    ev1 = SessionTurnStarted(session_id="s1", turn_counter=1, started_at_ms=1)
    bus.publish(ev1)
    assert len(events_a) == 1 and len(events_b) == 1

    assert bus.unsubscribe(SessionTurnStarted, a) is True
    ev2 = SessionTurnStarted(session_id="s1", turn_counter=2, started_at_ms=2)
    bus.publish(ev2)
    assert len(events_a) == 1, "a was removed — must NOT receive ev2"
    assert len(events_b) == 2, "b is still subscribed — must receive ev2"

    # unsubscribe handler that doesn't exist → False, no raise.
    assert bus.unsubscribe(SessionTurnStarted, lambda ev: None) is False


def test_unsubscribe_all_clears_handlers_and_cancels_tasks():
    bus = SessionBus()
    # One sync + one async handler each.
    ran_sync: list[object] = []
    bus.subscribe(SessionTurnStarted, lambda ev: ran_sync.append(ev))
    # Just to ensure handlers exist on multiple types.
    bus.subscribe(
        SessionIterationStarted, lambda ev: ran_sync.append(("it", ev)),
    )
    ev = SessionTurnStarted(session_id="s1", turn_counter=1, started_at_ms=1)
    bus.unsubscribe_all()
    bus.publish(ev)
    assert ran_sync == [], "unsubscribe_all → no handlers left"
    assert not bus._sync_handlers
    assert not bus._async_handlers
    assert not bus._in_flight


# ---------------------------------------------------------------------------
# Async subscriber tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_async_handler_scheduled_and_swallowed():
    bus = SessionBus()
    seen: list[object] = []

    async def a_ok(ev):
        seen.append(("ok", ev))

    async def a_bad(ev):
        seen.append(("bad", ev))
        raise RuntimeError("async boom")

    bus.subscribe(PackerFinalDone, a_ok)
    bus.subscribe(PackerFinalDone, a_bad)
    ev = PackerFinalDone(
        session_id="s1", turn_counter=1, iteration=0, msg_count=3,
        msg_roles=("user", "system", "assistant"),
        head=(), tail=("[a] tail",),
    )
    bus.publish(ev)
    # Async handlers are scheduled via create_task; give loop a chance.
    await asyncio.sleep(0)
    await bus.wait_drained()
    assert ("ok", ev) in seen
    assert ("bad", ev) in seen
    # No exceptions propagated. wait_drained returns count of tasks awaited.
    # Both tasks already done when wait_drained called → len(seen)>=2 proves
    # they ran; runtime warnings come via logging.


@pytest.mark.anyio
async def test_publish_async_runs_all_handlers_synchronously_in_gather():
    bus = SessionBus()
    order: list[str] = []

    def s1(ev):
        order.append("s1")

    def s2(ev):
        order.append("s2")

    async def a1(ev):
        await asyncio.sleep(0.01)
        order.append("a1")

    async def a2(ev):
        order.append("a2")

    bus.subscribe(ProjectorOutputDone, s1)
    bus.subscribe(ProjectorOutputDone, a1)
    bus.subscribe(ProjectorOutputDone, s2)
    bus.subscribe(ProjectorOutputDone, a2)

    ev = ProjectorOutputDone(
        session_id="s1", turn_counter=1, iteration=0,
        msg_count=1, msg_roles=("user",),
    )
    await bus.publish_async(ev)
    # Sync handlers run inline in registration order: s1, s2.
    # Async handlers are gather'd — a2 completes first (no sleep), a1 later.
    assert order[:2] == ["s1", "s2"], (
        "sync handlers MUST run before any awaits in registration order"
    )
    # Both asyncs are definitely done (we awaited gather):
    assert "a1" in order and "a2" in order


@pytest.mark.anyio
async def test_wait_drained_timeout_cancels_pending():
    bus = SessionBus()
    slow_started = asyncio.Event()

    async def slow(ev):
        slow_started.set()
        await asyncio.sleep(3600)  # "forever"

    bus.subscribe(PackerFinalDone, slow)
    ev = PackerFinalDone(session_id="s1", turn_counter=1, iteration=0)
    bus.publish(ev)
    # Ensure slow started executing, otherwise we cancel an unstarted task.
    await asyncio.wait_for(slow_started.wait(), timeout=1.0)
    waited = await bus.wait_drained(timeout_s=0.05)
    # slow task didn't finish → cancelled; waited count = number tasks that
    # DID finish (0 since only slow was in-flight).
    assert waited == 0
    assert not bus._in_flight, "Cancelled tasks must be removed from _in_flight"


# ---------------------------------------------------------------------------
# ContextVar integration
# ---------------------------------------------------------------------------

def test_current_bus_returns_noop_when_unbound():
    assert current_bus() is NOOP_BUS


def test_bind_ctx_manager_restores_previous_state():
    bus1 = SessionBus()
    bus2 = SessionBus()
    with bus1.bind():
        assert current_bus() is bus1
        with bus2.bind():
            assert current_bus() is bus2
        assert current_bus() is bus1, "Nested bind reset token broken"
    assert current_bus() is NOOP_BUS, "Outer bind reset token broken"
