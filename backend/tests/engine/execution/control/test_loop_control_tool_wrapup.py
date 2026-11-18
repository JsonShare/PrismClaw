"""工具预算耗尽时必须安排一次纯文本收尾轮，而不是直接终止。"""

from __future__ import annotations

import time

from prismclaw.engine.execution.checkpoint.models import RunState
from prismclaw.engine.execution.control.kernel import RunBudget, evaluate_loop_control


def _budget() -> RunBudget:
    return RunBudget(
        steps=10,
        tool_calls=4,
        soft_s=60.0,
        hard_s=120.0,
        reserve_s=10.0,
        cap_s=60.0,
        converge_tools=2,
    )


def _state(*, steps_spent: int, tool_calls_spent: int) -> RunState:
    now = time.monotonic()
    state = RunState(session_id="s")
    state.loop_state.steps_spent = steps_spent
    state.loop_state.tool_calls_spent = tool_calls_spent
    state.loop_state.soft_at = now + 60.0
    state.loop_state.hard_at = now + 120.0
    return state


def test_tool_budget_exhausted_schedules_wrapup_turn() -> None:
    state = _state(steps_spent=3, tool_calls_spent=4)
    now = time.monotonic()
    decision = evaluate_loop_control(
        run_state=state,
        run_budget=_budget(),
        now=now,
        phase="prepare",
    )
    assert decision.should_stop is False
    assert decision.is_last_turn is True
    assert decision.freeze_tools is True
    assert state.loop_state.tool_wrapup_step == 4


def test_wrapup_turn_survives_stream_phase() -> None:
    state = _state(steps_spent=3, tool_calls_spent=4)
    now = time.monotonic()
    evaluate_loop_control(
        run_state=state, run_budget=_budget(), now=now, phase="prepare"
    )
    # stream 阶段 steps_spent 已自增，同一收尾轮仍须判为 last_turn。
    state.loop_state.steps_spent = 4
    decision = evaluate_loop_control(
        run_state=state, run_budget=_budget(), now=now, phase="stream"
    )
    assert decision.should_stop is False
    assert decision.is_last_turn is True


def test_stop_after_wrapup_turn_finished() -> None:
    state = _state(steps_spent=3, tool_calls_spent=4)
    now = time.monotonic()
    evaluate_loop_control(
        run_state=state, run_budget=_budget(), now=now, phase="prepare"
    )
    state.loop_state.steps_spent = 4
    evaluate_loop_control(
        run_state=state, run_budget=_budget(), now=now, phase="stream"
    )
    # 收尾轮结束后进入下一轮 prepare：这次必须停。
    decision = evaluate_loop_control(
        run_state=state, run_budget=_budget(), now=now, phase="prepare"
    )
    assert decision.should_stop is True


def test_steps_exhausted_stops_immediately() -> None:
    state = _state(steps_spent=10, tool_calls_spent=0)
    decision = evaluate_loop_control(
        run_state=state,
        run_budget=_budget(),
        now=time.monotonic(),
        phase="prepare",
    )
    assert decision.should_stop is True
