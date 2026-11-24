from __future__ import annotations

from types import SimpleNamespace

from prismclaw.engine.execution.query.turn_planner import TurnPlanner
from prismclaw.engine.security.gate.governance import _has_write_intent, assess_turn
from prismclaw.engine.state.turn_state import TurnState
from prismclaw.engine.toolkit.registry.registry import ToolRegistry
from prismclaw.schema.agent.task_runtime import PreflightProbeResult
from prismclaw.schema.agent.task_types import TaskSpec


def _spec(
    *,
    goal: str,
    paths: tuple[str, ...] = (),
    risk: str = "normal",
    critical: tuple[str, ...] = (),
    turn_index: int = 1,
) -> TaskSpec:
    return TaskSpec(
        task_id="t1",
        session_id="s1",
        turn_index=turn_index,
        goal=goal,
        risk_level=risk,
        source_message=goal,
        preflight_probe=PreflightProbeResult(
            probe_id="p1",
            summary="",
            mentioned_paths=list(paths),
            baseline_risk=risk,
            critical_workspace_paths=list(critical),
        ),
    )


def _assess(
    message: str,
    *,
    paths: tuple[str, ...] = (),
    risk: str = "normal",
    critical: tuple[str, ...] = (),
    turn_index: int = 1,
):
    return assess_turn(
        user_message=message,
        task_spec=_spec(
            goal=message,
            paths=paths,
            risk=risk,
            critical=critical,
            turn_index=turn_index,
        ),
    )


def test_plain_question_is_simple() -> None:
    result = _assess("解释一下这个函数")
    assert result.level == "simple"
    assert result.score == 2
    assert result.signals == ["turn_relation=new"]


def test_single_read_is_not_simple_path() -> None:
    # 首轮 + 提及路径 = 3 分 → moderate；保守偏置，避免把简单任务误判为只读。
    result = _assess("读一下 auth.py", paths=("auth.py",))
    assert result.level == "moderate"


def test_multi_file_refactor_is_complex() -> None:
    result = _assess(
        "重构 auth.py 和 session.py",
        paths=("auth.py", "session.py"),
        risk="high",
        critical=("auth.py",),
    )
    assert result.level == "complex"


def test_continue_turn_prefers_simple() -> None:
    result = _assess("继续上一步", turn_index=2)
    assert result.level == "simple"
    assert result.score == 0


def test_empty_goal_first_turn_is_complex() -> None:
    result = _assess("")
    # 首轮 +2，目标缺失 +5（原 missing_information 与 ambiguity 两个信号合并）
    assert result.score == 7
    assert result.level == "complex"
    assert "missing_info=1" in result.signals
    assert "ambiguity=high" in result.signals


def test_empty_goal_continue_turn_is_moderate() -> None:
    result = _assess("", turn_index=2)
    assert result.score == 5
    assert result.level == "moderate"


def test_write_intent_uses_word_boundary_for_english() -> None:
    assert _has_write_intent("add a new function")
    assert _has_write_intent("改一下按钮文案")
    assert not _has_write_intent("address the issue")
    assert not _has_write_intent("additional context")


def test_complexity_switch_is_simple_only() -> None:
    """TurnPlanner 的 simple 判定只认 complexity == "simple"。"""
    planner = TurnPlanner()
    deps = SimpleNamespace(tools=ToolRegistry(), session_queue=object())
    simple = planner.plan(
        request=SimpleNamespace(complexity="simple"),
        deps=deps,
        turn_state=TurnState(),
        tool_ctx=SimpleNamespace(subagent_depth=0, tool_scope=None, headless=False),
        skill_invocation=None,
    )
    assert simple.skip_history_compaction is True
    assert simple.include_l1_memory is False
