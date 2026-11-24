"""TurnPlan / TurnPlanner 单元验证（SDD-ARCH-001 步 1）。

锁定目标 G2 / G4：
- 同一输入产出同一 plan（纯函数语义，无可变状态）；
- governance 在本轮决策中只被调用一次；
- ``tool_scope`` 作为 plan 入参直传 governance（D14 的前提）；
- 熔断工具同时从可见集与可调集消失。
"""

from __future__ import annotations

from types import SimpleNamespace

from prismclaw.engine.execution.query import turn_planner as turn_planner_module
from prismclaw.engine.execution.query.pre_request_pipeline import format_turn_plan_log
from prismclaw.engine.execution.query.turn_plan import TurnPlan
from prismclaw.engine.execution.query.turn_planner import TurnPlanner
from prismclaw.engine.state.turn_state import ToolDiscoveryRecord, TurnState
from prismclaw.engine.toolkit.registry.registry import ToolRegistry


class _Tool:
    def __init__(self, name: str, *, tool_class: str = "read") -> None:
        self.name = name
        self.description = f"{name} 描述"
        self.parameters = {"type": "object", "properties": {}}
        self.side_effect = tool_class == "write"
        self.tool_class = tool_class
        self.effect_class = "pure_read" if tool_class == "read" else "local_mutation"


def _registry(*names: str) -> ToolRegistry:
    registry = ToolRegistry()
    for name in names:
        registry.register(
            _Tool(name, tool_class="write" if name in {"write", "edit", "sub_agent"} else "read")
        )
    return registry


def _deps(registry: ToolRegistry, queue: object | None = object()) -> SimpleNamespace:
    return SimpleNamespace(tools=registry, session_queue=queue)


def _tool_ctx(
    *, subagent_depth: int = 0, tool_scope: frozenset[str] | None = None, headless: bool = False
) -> SimpleNamespace:
    return SimpleNamespace(subagent_depth=subagent_depth, tool_scope=tool_scope, headless=headless)


def _request(complexity: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(complexity=complexity)


def test_plan_counts_governance_once(monkeypatch) -> None:
    calls: list[dict] = []
    real = turn_planner_module.resolve_registry_tool_governance

    def _spy(registry, **kwargs):
        calls.append(kwargs)
        return real(registry, **kwargs)

    monkeypatch.setattr(turn_planner_module, "resolve_registry_tool_governance", _spy)

    plan = TurnPlanner().plan(
        request=_request(None),
        deps=_deps(_registry("read", "write", "tool_search")),
        turn_state=TurnState(),
        tool_ctx=_tool_ctx(),
        skill_invocation=None,
    )

    assert len(calls) == 1
    assert isinstance(plan, TurnPlan)
    assert set(plan.toolset.visible_tools_for_llm) == {"read", "write", "tool_search"}


def test_simple_complexity_sets_both_switches() -> None:
    planner = TurnPlanner()
    simple = planner.plan(
        request=_request("simple"),
        deps=_deps(_registry("read")),
        turn_state=TurnState(),
        tool_ctx=_tool_ctx(),
        skill_invocation=None,
    )
    assert simple.complexity == "simple"
    assert simple.skip_history_compaction is True
    assert simple.include_l1_memory is False

    complex_plan = planner.plan(
        request=_request("complex"),
        deps=_deps(_registry("read")),
        turn_state=TurnState(),
        tool_ctx=_tool_ctx(),
        skill_invocation=None,
    )
    assert complex_plan.complexity == "complex"
    assert complex_plan.skip_history_compaction is False
    assert complex_plan.include_l1_memory is True


def test_headless_read_from_tool_context_only() -> None:
    """R3：``headless`` 已在 ToolContext 构造时判定，planner 只读取不重算。

    即便 ``deps`` 上存在与之矛盾的 `session_queue`，也以 ToolContext 为准，
    避免同一事实出现两个计算点而漂移。
    """
    planner = TurnPlanner()

    interactive = planner.plan(
        request=_request(),
        deps=_deps(_registry("ask_user_question"), queue=None),
        turn_state=TurnState(),
        tool_ctx=SimpleNamespace(subagent_depth=0, tool_scope=None, headless=False),
        skill_invocation=None,
    )
    assert interactive.headless is False

    headless = planner.plan(
        request=_request(),
        deps=_deps(_registry("ask_user_question"), queue=object()),
        turn_state=TurnState(),
        tool_ctx=SimpleNamespace(subagent_depth=0, tool_scope=None, headless=True),
        skill_invocation=None,
    )
    assert headless.headless is True


def test_tool_scope_is_forwarded_to_governance(monkeypatch) -> None:
    seen: dict = {}
    real = turn_planner_module.resolve_registry_tool_governance

    def _spy(registry, **kwargs):
        seen.update(kwargs)
        return real(registry, **kwargs)

    monkeypatch.setattr(turn_planner_module, "resolve_registry_tool_governance", _spy)

    scope = frozenset({"read", "write"})
    TurnPlanner().plan(
        request=_request(),
        deps=_deps(_registry("read", "write", "bash")),
        turn_state=TurnState(),
        tool_ctx=_tool_ctx(tool_scope=scope),
        skill_invocation=None,
    )

    assert seen["tool_scope"] == scope


def test_frozen_tools_removed_from_plan_toolset() -> None:
    """熔断工具必须由 planner 剔除，而不是让 pipeline 做二次收窄。

    熔断集是本轮循环状态（``turn_prepare`` 在 ``pipeline.run`` 前写入），
    属工具面事实。若 planner 只输出治理结果、由 pipeline 再减一次，那么
    同一决策就有两个计算点，壳与实发随时可能分叉（D14 同类）。
    """
    plan = TurnPlanner().plan(
        request=_request(),
        deps=_deps(_registry("read", "write", "bash")),
        turn_state=TurnState(),
        tool_ctx=_tool_ctx(),
        skill_invocation=None,
        frozen_tools=frozenset({"bash"}),
    )

    assert "bash" not in plan.toolset.visible_tools_for_llm
    assert "bash" not in plan.toolset.callable_tools
    # 其余工具不受影响，避免误删
    assert "read" in plan.toolset.visible_tools_for_llm
    assert "bash" in plan.toolset.all_resolved_tools


def test_discovered_tools_and_subagent_depth_reach_governance(monkeypatch) -> None:
    seen: dict = {}
    real = turn_planner_module.resolve_registry_tool_governance

    def _spy(registry, **kwargs):
        seen.update(kwargs)
        return real(registry, **kwargs)

    monkeypatch.setattr(turn_planner_module, "resolve_registry_tool_governance", _spy)

    turn_state = TurnState()
    turn_state.discovered_tool_ledger.records.append(
        ToolDiscoveryRecord(canonical_name="bash", discovery_order=1)
    )

    TurnPlanner().plan(
        request=_request(),
        deps=_deps(_registry("bash")),
        turn_state=turn_state,
        tool_ctx=_tool_ctx(subagent_depth=1),
        skill_invocation=None,
    )

    assert seen["subagent_depth"] == 1
    assert seen["discovered_tools"] == turn_state.discovered_tool_names()


def test_same_input_same_plan() -> None:
    planner = TurnPlanner()
    deps = _deps(_registry("read", "tool_search"))
    first = planner.plan(
        request=_request("moderate"),
        deps=deps,
        turn_state=TurnState(),
        tool_ctx=_tool_ctx(),
        skill_invocation=None,
    )
    second = planner.plan(
        request=_request("moderate"),
        deps=deps,
        turn_state=TurnState(),
        tool_ctx=_tool_ctx(),
        skill_invocation=None,
    )
    assert first == second


def test_plan_log_covers_every_decision_field() -> None:
    """T9（G4）：单行日志必须覆盖 plan 的全部字段，否则观测与决策脱节。"""
    plan = TurnPlanner().plan(
        request=_request("simple"),
        deps=_deps(_registry("read", "tool_search")),
        turn_state=TurnState(),
        tool_ctx=_tool_ctx(),
        skill_invocation=None,
    )

    line = format_turn_plan_log(plan)

    assert line.startswith("turn_plan ")
    for field in (
        "complexity=",
        "visible_tools=",
        "searchable=",
        "skip_compaction=",
        "l1=",
        "headless=",
    ):
        assert field in line
    # 值与 plan 一致，而非占位符
    assert "complexity=simple" in line
    assert "skip_compaction=True" in line
    assert "l1=False" in line
