"""静态壳与复杂度解耦（SDD-ARCH-001 步 3a/3b / T1 / T2）。

锁 G1 / R1：静态壳是 ``(workspace 事实, model, 本轮可见集)`` 的纯函数，
复杂度不得影响能力索引与 bootstrap 的构建。历史上 simple 轮次会把
``<packages>`` / ``<skills>`` / ``<subagents>`` 置空并跳过 bootstrap
（D1 / D2），本测试锁定该分支不再回来。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest import mock

from prismclaw.engine.execution.capabilities.catalog import (
    AgentCatalogEntry,
    CapabilityCatalogService,
    SkillCatalogEntry,
)
from prismclaw.engine.execution.query._static_context_types import StaticTurnShellCache
from prismclaw.engine.execution.query.context_preparation import StaticContextBuilder
from prismclaw.engine.toolkit.registry.registry import ToolRegistry
from prismclaw.schema.services.skills import SkillManifest

BOOTSTRAP_TEXT = "# 项目约定\n- 提交前跑 pytest\n"
_VISIBLE = {"read", "skill_search", "subagent_search", "sub_agent"}


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.description = f"{name} 描述"
        self.parameters = {"type": "object", "properties": {}}
        self.tool_class = "read"
        self.effect_class = "pure_read"
        self.plugin_name = None


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    for name in sorted(_VISIBLE):
        registry.register(_Tool(name))
    return registry


def _skill_entry(name: str) -> SkillCatalogEntry:
    manifest = SkillManifest(
        name=name,
        description=f"desc of {name}",
        body=f"instructions for {name}",
        source="skill",
        file_path=f"/tmp/nonexistent-dir/{name}/SKILL.md",
    )
    return SkillCatalogEntry(
        name=name,
        description=f"desc of {name}",
        location="",
        instructions="",
        manifest=manifest,
    )


def _agent_entry(name: str) -> AgentCatalogEntry:
    return AgentCatalogEntry(
        name=name,
        description=f"{name} 子代理",
        can_handoff_to=(),
        manifest={"name": name, "tools": ["read"]},
    )


def _catalog() -> CapabilityCatalogService:
    return CapabilityCatalogService(tool_registry=_registry())


def _deps(*, bootstrap: str | None) -> SimpleNamespace:
    async def _get_bootstrap(_sid):
        return bootstrap

    budget = SimpleNamespace(
        compute_history_budget=lambda **kw: 8000,
        window_tokens=100_000,
        system_zone_tokens=8192,
        dynamic_zone_tokens=12288,
    )
    return SimpleNamespace(
        tools=_registry(),
        handoff_supervisor=None,
        context_bootstrap_provider=SimpleNamespace(get_bootstrap=_get_bootstrap),
        static_shell_cache=StaticTurnShellCache(),
        plugin=SimpleNamespace(lifecycle_manager=SimpleNamespace(generation=0)),
        config=SimpleNamespace(
            context=SimpleNamespace(
                budget=budget,
                compaction=SimpleNamespace(
                    history=SimpleNamespace(keep_recent_tokens=0),
                ),
            )
        ),
        ports=None,
        session_queue=object(),
    )


def _request(complexity: str | None):
    return SimpleNamespace(
        session_id="sess-1",
        workspace_id="ws-1",
        agent_id="agent-1",
        complexity=complexity,
    )


def _build(deps, *, complexity, visible=_VISIBLE):
    return asyncio.run(
        StaticContextBuilder().build(
            request=_request(complexity),
            deps=deps,
            tree=None,
            tool_workspace_path="/tmp/ws",
            effective_model="test-model",
            skill_invocation=None,
            discovered_tools=None,
            subagent_depth=0,
            visible_tool_names=visible,
        )
    )


def _patch_catalog():
    return (
        mock.patch.object(
            CapabilityCatalogService,
            "list_skill_entries",
            return_value=[_skill_entry("code_review")],
        ),
        mock.patch.object(
            CapabilityCatalogService,
            "list_agent_entries",
            return_value=[_agent_entry("executor")],
        ),
        mock.patch(
            "prismclaw.engine.execution.query.context_preparation._require_capability_catalog",
            lambda deps: _catalog(),
        ),
    )


def test_catalogs_identical_across_complexity() -> None:
    """T1：simple 与 complex 的静态壳字节相同（bootstrap 关闭以隔离变量）。"""
    skills, agents, require = _patch_catalog()
    with skills, agents, require:
        deps = _deps(bootstrap=None)
        simple = _build(deps, complexity="simple")
        complex_ = _build(deps, complexity="complex")

    assert simple.frozen_result.text == complex_.frozen_result.text
    assert "<skills>" in simple.frozen_result.text
    assert "`code_review`" in simple.frozen_result.text
    assert "`executor`" in simple.frozen_result.text


def test_bootstrap_loaded_regardless_of_complexity() -> None:
    """T1（bootstrap 维度）：simple 轮次同样加载项目约定（D2）。"""
    skills, agents, require = _patch_catalog()
    with skills, agents, require:
        deps = _deps(bootstrap=BOOTSTRAP_TEXT)
        simple = _build(deps, complexity="simple")
        complex_ = _build(deps, complexity="complex")

    assert BOOTSTRAP_TEXT.strip() in simple.frozen_result.text
    assert simple.frozen_result.text == complex_.frozen_result.text


def test_shell_reused_for_same_facts() -> None:
    """T2：事实不变时同一会话连续两轮壳字节相同，且命中同一条缓存。"""
    skills, agents, require = _patch_catalog()
    with skills, agents, require:
        deps = _deps(bootstrap=BOOTSTRAP_TEXT)
        first = _build(deps, complexity="complex")
        second = _build(deps, complexity="complex")

    assert first.frozen_result.text == second.frozen_result.text
    assert len(deps.static_shell_cache.entries) == 1


def test_shell_rebuilt_when_visible_tools_change() -> None:
    """可见集是 ``<tools>`` 段的直接输入，变化后不得复用旧壳（D14）。

    ``<skills>`` / ``<subagents>`` 等段的存在与否由 ``inputs.tools`` 决定，
    因此收回 ``skill_search`` 必须改变壳字节，而不能命中旧条目。
    """
    visible_entries = mock.patch.object(
        CapabilityCatalogService,
        "list_visible_tool_entries",
        side_effect=lambda **kw: [
            SimpleNamespace(llm_name=name, description=f"{name} 描述")
            for name in sorted(kw["visible_names"])
        ],
    )
    skills, agents, require = _patch_catalog()
    with visible_entries, skills, agents, require:
        deps = _deps(bootstrap=BOOTSTRAP_TEXT)
        with_skill_search = _build(deps, complexity="complex", visible=_VISIBLE)
        without = _build(
            deps,
            complexity="complex",
            visible=_VISIBLE - {"skill_search"},
        )

    assert "<skills>" in with_skill_search.frozen_result.text
    assert "<skills>" not in without.frozen_result.text
    assert with_skill_search.frozen_result.text != without.frozen_result.text
    assert len(deps.static_shell_cache.entries) == 2
