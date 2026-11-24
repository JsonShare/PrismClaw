"""``<tools>`` 段与实发工具同源（SDD-ARCH-001 步 2 / T12）。

锁 D14：壳内工具摘要必须取自 ``TurnPlan.toolset.visible_tools_for_llm``
（含 ``tool_scope`` 治理结果），不得再自行跑一次漏传 ``tool_scope`` 的
governance。否则插件作用域未激活时，技能声明的白名单会把作用域外工具
解锁进提示词，而实发 ``tools_for_llm`` 里根本没有它。
"""

from __future__ import annotations

from types import SimpleNamespace

from prismclaw.engine.execution.capabilities.catalog import CapabilityCatalogService
from prismclaw.engine.execution.query._catalog_builder import _tool_prompt_summaries
from prismclaw.engine.execution.query.turn_planner import TurnPlanner
from prismclaw.engine.state.turn_state import TurnState
from prismclaw.engine.toolkit.registry.registry import ToolRegistry
from prismclaw.schema.services.skills import SkillInvocationState


class _Tool:
    def __init__(self, name: str, *, plugin_name: str | None = None) -> None:
        self.name = name
        self.description = f"{name} 描述"
        self.parameters = {"type": "object", "properties": {}}
        self.tool_class = "read"
        self.effect_class = "pure_read"
        self.plugin_name = plugin_name


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(_Tool("read"))
    registry.register(_Tool("tool_search"))
    registry.register(_Tool("legal_lookup", plugin_name="legal"))
    return registry


def _plan(
    *,
    registry: ToolRegistry,
    tool_scope: frozenset[str] | None,
    skill_invocation: SkillInvocationState | None = None,
):
    deps = SimpleNamespace(tools=registry, session_queue=object())
    return TurnPlanner().plan(
        request=SimpleNamespace(complexity=None),
        deps=deps,
        turn_state=TurnState(),
        tool_ctx=SimpleNamespace(subagent_depth=0, tool_scope=tool_scope, headless=False),
        skill_invocation=skill_invocation,
    )


def _prompt_names(*, registry: ToolRegistry, visible_names: set[str]) -> set[str]:
    catalog = CapabilityCatalogService(tool_registry=registry)
    summaries = _tool_prompt_summaries(
        capability_catalog=catalog,
        visible_names=visible_names,
        subagent_depth=0,
    )
    return {name for name, _desc in summaries}


def test_out_of_scope_skill_tool_absent_from_prompt(tmp_path) -> None:
    """技能白名单指向未激活插件的工具时，plan 与 ``<tools>`` 都不得包含它。

    旧实现（壳内重跑 governance 且不传 ``tool_scope``）会把 ``legal_lookup``
    当作已解锁工具列进 ``<tools>``，但实发工具集里没有它——提示词谎报。
    """
    registry = _registry()
    skill = SkillInvocationState(
        skill_name="legal_research",
        allowed_tools_override=["legal_lookup"],
        is_active=True,
    )

    plan = _plan(
        registry=registry,
        tool_scope=frozenset(),  # legal 插件未激活
        skill_invocation=skill,
    )

    visible = set(plan.toolset.visible_tools_for_llm)
    assert "legal_lookup" not in visible
    assert "tool_search" in visible

    prompt_names = _prompt_names(registry=registry, visible_names=visible)
    assert prompt_names == visible
    assert "legal_lookup" not in prompt_names


def test_in_scope_skill_tool_present_in_both() -> None:
    """插件已激活时，技能解锁的工具在 plan 与 ``<tools>`` 中同时出现。"""
    registry = _registry()
    skill = SkillInvocationState(
        skill_name="legal_research",
        allowed_tools_override=["legal_lookup"],
        is_active=True,
    )

    plan = _plan(
        registry=registry,
        tool_scope=frozenset({"legal"}),
        skill_invocation=skill,
    )

    visible = set(plan.toolset.visible_tools_for_llm)
    assert "legal_lookup" in visible
    assert _prompt_names(registry=registry, visible_names=visible) == visible


def test_prompt_names_limited_to_registry() -> None:
    """传入不在注册表的名字不会凭空出现在提示词里。"""
    registry = _registry()
    prompt_names = _prompt_names(
        registry=registry,
        visible_names={"read", "tool_search", "ghost_tool"},
    )
    assert prompt_names == {"read", "tool_search"}


def test_frozen_tools_excluded_from_prompt_and_wire() -> None:
    """熔断（frozen）工具必须同时从 ``<tools>`` 与实发工具面消失。

    ``frozen_tools`` 是本轮循环状态，在 pipeline 之前写入。壳与实发若各用
    一份集合，``<tools>`` 会宣传一个调用必然失败的工具——与 D14 同类。
    因此 pipeline 必须在构建壳之前先收敛到终集，壳消费的正是这份终集。
    """
    registry = _registry()
    plan = _plan(registry=registry, tool_scope=None)
    visible = set(plan.toolset.visible_tools_for_llm)

    frozen = {"tool_search"}
    final_visible = visible - frozen

    # 壳读到的是移除熔断工具后的终集
    prompt_names = _prompt_names(registry=registry, visible_names=final_visible)
    assert prompt_names == final_visible
    assert "tool_search" not in prompt_names
    # 若误用未剔除的集合，壳就会多出一个实发没有的工具
    assert prompt_names != visible
