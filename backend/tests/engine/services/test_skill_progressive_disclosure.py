"""技能 / 插件包 / 子代理目录契约单测。

覆盖对象:
* ``skills_section()``：只列项目技能名（插件技能走 skill_search）
* ``packages_section()``：包名 + 外层描述 + skills/subagents 计数，不列具体能力
* ``subagents_section()``：只列核心子代理 + 编排规则（插件子代理走 subagent_search）
* ``resolve_skill_reference()`` 解析契约（技能名优先 / 别名直取 / ambiguous）
* ``build_skill_execution()`` 错误消息路由（名单回传，无自反文案）
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# sys.path
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def _make_prompt_inputs(
    *,
    skills: list[str] | None = None,
    tools: list[tuple[str, str]] | None = None,
    summaries: list[Any] | None = None,
    subagents: list[Any] | None = None,
) -> Any:
    from prismclaw.engine.execution.request.system_prompt import PromptInputs
    return PromptInputs(
        session_id="test-session",
        workspace_id="test-workspace",
        agent_id="test-agent",
        model="test-model",
        project_skill_names=tuple(skills or []),
        tools=tools or [
            ("skill_search", "search for a skill"),
            ("subagent_search", "search for a subagent"),
            ("sub_agent", "delegate"),
        ],
        available_subagents=subagents or [],
        marketplace_summaries=summaries or [],
    )


def _make_summary(name: str, *, description: str = "", skill_count: int = 1) -> Any:
    from prismclaw.engine.execution.request.system_prompt import MarketplaceSummary
    return MarketplaceSummary(name=name, description=description, skill_count=skill_count)


def _make_subagent(
    name: str,
    *,
    description: str = "",
    plugin_name: str | None = None,
    marketplace_name: str | None = None,
    marketplace_description: str | None = None,
) -> Any:
    from prismclaw.engine.execution.request.system_prompt import AvailableSubagentEntry
    return AvailableSubagentEntry(
        name=name,
        description=description,
        plugin_name=plugin_name,
        marketplace_name=marketplace_name,
        marketplace_description=marketplace_description,
    )


# ---------------------------------------------------------------------------
# 辅助：构造 SkillCatalogEntry（resolve_skill_reference / build_skill_execution 用）
# ---------------------------------------------------------------------------
def _make_catalog_entry(*, name: str, marketplace_name: str | None = None, plugin_name: str | None = None) -> Any:
    from prismclaw.engine.execution.capabilities.catalog import SkillCatalogEntry
    from prismclaw.schema.services.skills import SkillManifest
    manifest = SkillManifest(
        name=name, description=f"desc of {name}", body=f"instructions for {name}",
        source="skill", file_path=f"/tmp/nonexistent-dir/{name}/SKILL.md",
    )
    return SkillCatalogEntry(
        name=name, description=f"desc of {name}", location="",
        instructions="", manifest=manifest,
        plugin_name=plugin_name, marketplace_name=marketplace_name,
    )


# ==========================================================================
# 1. skills_section：只列项目技能
# ==========================================================================
class TestSkillsSectionRendering:
    def test_no_skills_returns_none(self):
        from prismclaw.engine.execution.request.system_prompt import skills_section
        assert skills_section(_make_prompt_inputs(skills=[])) is None

    def test_no_skill_search_tool_returns_none(self):
        from prismclaw.engine.execution.request.system_prompt import skills_section
        inputs = _make_prompt_inputs(
            skills=["x"],
            tools=[("other_tool", "desc")],
        )
        assert skills_section(inputs) is None

    def test_project_skills_rendered_as_name_index(self):
        from prismclaw.engine.execution.request.system_prompt import skills_section
        result = skills_section(_make_prompt_inputs(skills=["code_review", "deep_research"]))
        assert result is not None
        assert result.startswith("<skills>")
        assert result.endswith("</skills>")
        assert "`code_review`" in result
        assert "`deep_research`" in result
        assert "`skill_search`" in result
        assert "`activate_skill`" in result

    def test_plugin_skills_never_rendered(self):
        """插件技能不进 <skills>；包索引由 <packages> 负责。"""
        from prismclaw.engine.execution.request.system_prompt import skills_section
        inputs = _make_prompt_inputs(
            skills=["code_review"],
            summaries=[_make_summary("legal-advisor", skill_count=2)],
        )
        result = skills_section(inputs)
        assert result is not None
        assert "legal-advisor" not in result
        assert "# 插件包" not in result


# ==========================================================================
# 2. packages_section：包索引（不列具体能力）
# ==========================================================================
class TestPackagesSection:
    def test_none_without_discovery_tools(self):
        from prismclaw.engine.execution.request.system_prompt import packages_section
        inputs = _make_prompt_inputs(
            tools=[("other_tool", "desc")],
            summaries=[_make_summary("mp")],
        )
        assert packages_section(inputs) is None

    def test_none_without_packages(self):
        from prismclaw.engine.execution.request.system_prompt import packages_section
        assert packages_section(_make_prompt_inputs()) is None

    def test_counts_skills_and_subagents(self):
        from prismclaw.engine.execution.request.system_prompt import packages_section
        inputs = _make_prompt_inputs(
            summaries=[_make_summary("legal-chineselaw", skill_count=4)],
            subagents=[
                _make_subagent("docket-watcher", plugin_name="litigation", marketplace_name="legal-chineselaw"),
                _make_subagent("case-analyst", plugin_name="litigation", marketplace_name="legal-chineselaw"),
            ],
        )
        result = packages_section(inputs)
        assert result is not None
        assert result.startswith("<packages>")
        assert "`legal-chineselaw`" in result
        assert "4 skills" in result
        assert "2 subagents" in result

    def test_package_from_agent_only_without_skills(self):
        """只有子代理、没有技能的包也必须被索引。"""
        from prismclaw.engine.execution.request.system_prompt import packages_section
        inputs = _make_prompt_inputs(
            subagents=[
                _make_subagent(
                    "x",
                    plugin_name="p",
                    marketplace_name="agent-only",
                    marketplace_description="仅有子代理的能力包",
                ),
            ],
        )
        result = packages_section(inputs)
        assert result is not None
        assert "`agent-only`" in result
        assert "1 subagent" in result
        assert "仅有子代理的能力包" in result

    def test_capability_names_never_rendered(self):
        from prismclaw.engine.execution.request.system_prompt import packages_section
        inputs = _make_prompt_inputs(
            summaries=[_make_summary("legal", skill_count=1)],
            subagents=[_make_subagent("docket-watcher", plugin_name="litigation", marketplace_name="legal")],
        )
        result = packages_section(inputs)
        assert result is not None
        assert "docket-watcher" not in result
        assert "litigation" not in result

    def test_description_truncated(self):
        from prismclaw.engine.execution.request.system_prompt import packages_section
        inputs = _make_prompt_inputs(
            summaries=[_make_summary("legal", description="法律" * 200, skill_count=1)],
        )
        result = packages_section(inputs)
        assert result is not None
        assert "…" in result

    def test_packages_sorted_and_capped(self):
        from prismclaw.engine.execution.request.system_prompt import (
            _PACKAGE_LIST_LIMIT,
            packages_section,
        )
        inputs = _make_prompt_inputs(
            summaries=[_make_summary(f"pkg-{i}", skill_count=1) for i in range(_PACKAGE_LIST_LIMIT + 2)],
        )
        result = packages_section(inputs)
        assert result is not None
        lines = [line for line in result.split("\n") if line.startswith("`pkg-")]
        assert lines == sorted(lines)
        assert len(lines) == _PACKAGE_LIST_LIMIT
        assert "另有 2 个包" in result


# ==========================================================================
# 3. subagents_section：核心子代理 + 规则
# ==========================================================================
class TestSubagentsSection:
    def test_none_without_sub_agent_tool(self):
        from prismclaw.engine.execution.request.system_prompt import subagents_section
        inputs = _make_prompt_inputs(tools=[("skill_search", "d")])
        assert subagents_section(inputs) is None

    def test_core_only_plugin_names_absent(self):
        from prismclaw.engine.execution.request.system_prompt import subagents_section
        inputs = _make_prompt_inputs(subagents=[
            _make_subagent("architect", description="架构规划"),
            _make_subagent("docket-watcher", plugin_name="litigation", marketplace_name="legal"),
        ])
        result = subagents_section(inputs)
        assert result is not None
        assert result.startswith("<subagents>")
        assert "`architect`" in result
        assert "docket-watcher" not in result
        assert "`subagent_search`" in result

    def test_rules_present(self):
        from prismclaw.engine.execution.request.system_prompt import subagents_section
        result = subagents_section(_make_prompt_inputs(subagents=[_make_subagent("architect")]))
        assert result is not None
        assert "[HANDOFF_REQUEST]" in result
        assert "置信度不得提升" in result
        assert "`reduce_subagent_results`" in result
        assert "context_snapshot" in result


# ==========================================================================
# 4. resolve_skill_reference 解析契约
# ==========================================================================
class TestResolveSkillReference:
    def test_unique_member_package_alias_resolves_directly(self):
        """唯一技能的包名 → resolved 直取，携带 marketplace_name 揭示。"""
        from prismclaw.engine.execution.capabilities.catalog import CapabilityCatalogService
        entries = [_make_catalog_entry(name="tech-mining", marketplace_name="research-kit")]
        svc = CapabilityCatalogService()
        with mock.patch.object(CapabilityCatalogService, "list_skill_entries", return_value=entries):
            r = svc.resolve_skill_reference("research-kit")
            assert r.kind == "resolved"
            assert r.entry.name == "tech-mining"
            assert r.marketplace_name == "research-kit"

    def test_multi_member_package_alias_is_ambiguous(self):
        """多技能包名 → ambiguous 且 member_names 齐全。"""
        from prismclaw.engine.execution.capabilities.catalog import CapabilityCatalogService
        entries = [
            _make_catalog_entry(name="legal_a", marketplace_name="legal-advisor"),
            _make_catalog_entry(name="legal_b", marketplace_name="legal-advisor"),
        ]
        svc = CapabilityCatalogService()
        with mock.patch.object(CapabilityCatalogService, "list_skill_entries", return_value=entries):
            r = svc.resolve_skill_reference("legal-advisor")
            assert r.kind == "ambiguous"
            assert r.marketplace_name == "legal-advisor"
            assert r.member_names == ("legal_a", "legal_b")

    def test_skill_name_wins_over_same_named_package(self):
        """技能名与包名同名时技能名优先。"""
        from prismclaw.engine.execution.capabilities.catalog import CapabilityCatalogService
        entries = [
            _make_catalog_entry(name="foo"),  # 技能 foo
            _make_catalog_entry(name="bar", marketplace_name="foo"),  # 包 foo 含 bar
        ]
        svc = CapabilityCatalogService()
        with mock.patch.object(CapabilityCatalogService, "list_skill_entries", return_value=entries):
            r = svc.resolve_skill_reference("foo")
            assert r.kind == "resolved"
            assert r.entry.name == "foo"
            assert r.marketplace_name is None  # 技能名 pass 命中，非别名

    def test_slash_prefix_normalized_in_alias_pass(self):
        """/research-kit 前缀清洗后命中别名 pass。"""
        from prismclaw.engine.execution.capabilities.catalog import CapabilityCatalogService
        entries = [_make_catalog_entry(name="tech-mining", marketplace_name="research-kit")]
        svc = CapabilityCatalogService()
        with mock.patch.object(CapabilityCatalogService, "list_skill_entries", return_value=entries):
            r = svc.resolve_skill_reference("/research-kit")
            assert r.kind == "resolved"
            assert r.entry.name == "tech-mining"

    def test_get_skill_entry_returns_none_on_ambiguous(self):
        """get_skill_entry 委托：ambiguous → None（entry-or-None 语义不变）。"""
        from prismclaw.engine.execution.capabilities.catalog import CapabilityCatalogService
        entries = [
            _make_catalog_entry(name="legal_a", marketplace_name="legal-advisor"),
            _make_catalog_entry(name="legal_b", marketplace_name="legal-advisor"),
        ]
        svc = CapabilityCatalogService()
        with mock.patch.object(CapabilityCatalogService, "list_skill_entries", return_value=entries):
            assert svc.get_skill_entry("legal-advisor") is None
            assert svc.get_skill_entry("legal_a") is not None


# ==========================================================================
# 5. build_skill_execution 错误消息路由
# ==========================================================================
class TestBuildSkillExecutionErrors:
    def test_ambiguous_error_lists_all_members(self):
        """ambiguous → LookupError 列出全部成员技能名。"""
        from prismclaw.engine.execution.capabilities.catalog import CapabilityCatalogService
        from prismclaw.engine.execution.skills.runtime import SkillRuntime
        from prismclaw.schema.services.skills import SkillInvocation
        entries = [
            _make_catalog_entry(name="legal_a", marketplace_name="legal-advisor"),
            _make_catalog_entry(name="legal_b", marketplace_name="legal-advisor"),
        ]
        svc = CapabilityCatalogService()
        rt = SkillRuntime(capability_catalog=svc)
        invocation = SkillInvocation(skill_name="legal-advisor", task_context="", source="model")
        with mock.patch.object(CapabilityCatalogService, "list_skill_entries", return_value=entries):
            with pytest.raises(LookupError) as exc_info:
                rt.build_skill_execution(invocation, workspace_path=Path("/tmp"))
        msg = str(exc_info.value)
        assert "legal_a" in msg
        assert "legal_b" in msg
        assert "插件包名" in msg

    def test_not_found_error_has_no_reflexive_text(self):
        """not_found → 近邻指引 + skill_search 提示，无自反文案。"""
        from prismclaw.engine.execution.capabilities.catalog import CapabilityCatalogService
        from prismclaw.engine.execution.skills.runtime import SkillRuntime
        from prismclaw.schema.services.skills import SkillInvocation
        entries = [_make_catalog_entry(name="tech-mining", marketplace_name="research-kit")]
        svc = CapabilityCatalogService()
        rt = SkillRuntime(capability_catalog=svc)
        invocation = SkillInvocation(skill_name="totally-unrelated", task_context="", source="model")
        with mock.patch.object(CapabilityCatalogService, "list_skill_entries", return_value=entries):
            with pytest.raises(LookupError) as exc_info:
                rt.build_skill_execution(invocation, workspace_path=Path("/tmp"))
        msg = str(exc_info.value)
        assert "available_skills" not in msg  # 防自反回归守卫
        assert "skill_search" in msg

    def test_alias_activation_summary_discloses_package(self):
        """别名激活 summary 揭示匹配路径，state 保持请求名。"""
        from prismclaw.engine.execution.capabilities.catalog import CapabilityCatalogService
        from prismclaw.engine.execution.skills.runtime import SkillRuntime
        from prismclaw.schema.services.skills import SkillInvocation
        entries = [_make_catalog_entry(name="tech-mining", marketplace_name="research-kit")]
        svc = CapabilityCatalogService()
        rt = SkillRuntime(capability_catalog=svc)
        invocation = SkillInvocation(skill_name="research-kit", task_context="", source="model")
        with mock.patch.object(CapabilityCatalogService, "list_skill_entries", return_value=entries):
            plan = rt.build_skill_execution(invocation, workspace_path=Path("/tmp"))
        assert "(matched via package 'research-kit')" in plan.summary
        assert plan.skill_name == "research-kit"


# ==========================================================================
# 6. 字节稳定性（frozen prefix 缓存约束）
# ==========================================================================
class TestByteStability:
    def test_skills_render_identically(self):
        inputs = _make_prompt_inputs(skills=["a", "b"])
        from prismclaw.engine.execution.request.system_prompt import skills_section
        assert skills_section(inputs) == skills_section(inputs)

    def test_packages_render_identically(self):
        inputs = _make_prompt_inputs(
            summaries=[_make_summary("mp", description="包描述", skill_count=2)],
            subagents=[_make_subagent("x", plugin_name="p", marketplace_name="mp")],
        )
        from prismclaw.engine.execution.request.system_prompt import packages_section
        assert packages_section(inputs) == packages_section(inputs)


# ==========================================================================
# 7. marketplace_name 全链路传播验证
# ==========================================================================
class TestMarketplaceNamePropagation:
    def test_skill_catalog_entry_has_marketplace_name(self):
        from prismclaw.engine.execution.capabilities.catalog import SkillCatalogEntry
        entry = SkillCatalogEntry(
            name="test_skill",
            description="desc",
            location="/path",
            instructions="",
            manifest=None,
            plugin_name="plugin1",
            origin="plugin",
            marketplace_name="legal-advisor",
        )
        assert entry.marketplace_name == "legal-advisor"


# ==========================================================================
# 8. workspace 段会话交付目录声明（交付物唯一有效落点）
# ==========================================================================
class TestWorkspaceSectionSessionOutputs:
    def test_renders_session_outputs_dir(self):
        """有 workspace_path + session_id 时渲染虚拟路径词汇表和会话交付目录契约。"""
        from prismclaw.engine.execution.request.system_prompt import workspace_section
        inputs = _make_prompt_inputs()
        inputs.workspace_path = "/tmp/ws"
        section = workspace_section(inputs)
        assert section is not None
        assert "outputs/" in section
        assert "虚拟路径前缀契约" in section
        assert "PRISMCLAW_OUTPUT_DIR" in section

    def test_no_session_id_no_outputs_line(self):
        from prismclaw.engine.execution.request.system_prompt import workspace_section
        inputs = _make_prompt_inputs()
        inputs.workspace_path = "/tmp/ws"
        inputs.session_id = ""
        section = workspace_section(inputs)
        assert section is not None
        assert "PRISMCLAW_OUTPUT_DIR" not in section


# ==========================================================================
# 9. 任务级技能路由（§4.10 动态层：_route_skill_hints 三分支）
# ==========================================================================
class _FakeMatch:
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description


class _FakeDiscovery:
    """SkillDiscoveryRuntime 替身：search 返回预置结果或抛错。"""

    def __init__(self, matches=None, error=None):
        self._matches = matches or []
        self._error = error

    def search(self, query, *, workspace_path, limit):
        if self._error is not None:
            raise self._error
        return self._matches[:limit]


def _patch_discovery(monkeypatch, fake):
    monkeypatch.setattr(
        "prismclaw.engine.execution.capabilities.discovery_runtime.SkillDiscoveryRuntime",
        lambda capability_catalog=None: fake,
    )


class TestRouteSkillHints:
    def test_multi_match_produces_hints(self, monkeypatch, tmp_path):
        from prismclaw.engine.execution.state.streaming import _route_skill_hints
        _patch_discovery(monkeypatch, _FakeDiscovery(matches=[
            _FakeMatch("tech-mining", "跨领域技术创新挖掘"),
            _FakeMatch("feature-risk-assessment", "功能风险评估"),
        ]))
        hints = _route_skill_hints(
            user_message="研发高强度瓷绝缘子配方",
            capability_catalog=object(),  # 非 None 即可（runtime 被替换）
            workspace_path=str(tmp_path),
        )
        assert hints == ("tech-mining — 跨领域技术创新挖掘", "feature-risk-assessment — 功能风险评估")

    def test_single_match_yields_nothing(self, monkeypatch, tmp_path):
        """唯一命中交给 _try_auto_activate_skill 自动激活，路由不重复提示。"""
        from prismclaw.engine.execution.state.streaming import _route_skill_hints
        _patch_discovery(monkeypatch, _FakeDiscovery(matches=[_FakeMatch("tech-mining")]))
        hints = _route_skill_hints(
            user_message="瓷绝缘子 配方改进",
            capability_catalog=object(),
            workspace_path=str(tmp_path),
        )
        assert hints == ()

    def test_zero_match_yields_nothing(self, monkeypatch, tmp_path):
        from prismclaw.engine.execution.state.streaming import _route_skill_hints
        _patch_discovery(monkeypatch, _FakeDiscovery(matches=[]))
        hints = _route_skill_hints(
            user_message="继续",
            capability_catalog=object(),
            workspace_path=str(tmp_path),
        )
        assert hints == ()

    def test_no_catalog_or_workspace_yields_nothing(self):
        from prismclaw.engine.execution.state.streaming import _route_skill_hints
        assert _route_skill_hints(user_message="x", capability_catalog=None, workspace_path="/tmp") == ()
        assert _route_skill_hints(user_message="x", capability_catalog=object(), workspace_path=None) == ()
        assert _route_skill_hints(user_message="  ", capability_catalog=object(), workspace_path="/tmp") == ()

    def test_search_error_is_best_effort(self, monkeypatch, tmp_path):
        """检索异常返回空——路由是引导，不阻塞回合。"""
        from prismclaw.engine.execution.state.streaming import _route_skill_hints
        _patch_discovery(monkeypatch, _FakeDiscovery(error=RuntimeError("boom")))
        hints = _route_skill_hints(
            user_message="x",
            capability_catalog=object(),
            workspace_path=str(tmp_path),
        )
        assert hints == ()

    def test_hints_capped_at_three(self, monkeypatch, tmp_path):
        from prismclaw.engine.execution.state.streaming import _route_skill_hints
        _patch_discovery(monkeypatch, _FakeDiscovery(matches=[
            _FakeMatch(f"skill_{i}") for i in range(5)
        ]))
        hints = _route_skill_hints(
            user_message="x",
            capability_catalog=object(),
            workspace_path=str(tmp_path),
        )
        assert len(hints) == 3
