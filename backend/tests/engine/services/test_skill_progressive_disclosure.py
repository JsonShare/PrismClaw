"""技能目录契约——渲染/预算/解析三层单测。

覆盖对象:
* skills_section() 契约式渲染：每个可见名可被 activate_skill 解析
  （项目级名称索引行 + `# 插件包` 组注释 + 逐技能行）
* _hard_cap_names() / _within_budget_catalog() 逐条计费硬截断
* resolve_skill_reference() 解析契约（技能名优先 / 别名直取 / ambiguous）
* build_skill_execution() 错误消息路由（名单回传，无自反文案）
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
# 辅助：构造 AvailableSkillEntry
# ---------------------------------------------------------------------------
def _make_skill_entry(
    *,
    name: str = "skill_a",
    description: str = "",
    category: str = "",
    plugin_name: str | None = None,
    priority: int = 0,
    is_active: bool = False,
    marketplace_name: str | None = None,
    marketplace_description: str | None = None,
) -> Any:
    from prismclaw.engine.execution.request.system_prompt import AvailableSkillEntry
    return AvailableSkillEntry(
        name=name,
        description=description,
        category=category,
        plugin_name=plugin_name,
        priority=priority,
        is_active=is_active,
        marketplace_name=marketplace_name,
        marketplace_description=marketplace_description,
    )


def _make_prompt_inputs(
    *,
    skills: list[Any] | None = None,
    tools: list[tuple[str, str]] | None = None,
    note: str = "",
    summaries: list[Any] | None = None,
) -> Any:
    from prismclaw.engine.execution.request.system_prompt import PromptInputs
    return PromptInputs(
        session_id="test-session",
        workspace_id="test-workspace",
        agent_id="test-agent",
        model="test-model",
        available_skills=skills or [],
        tools=tools or [("skill_search", "search for a skill")],
        available_subagents=[],
        available_subagents_refreshed_at="",
        available_skills_note=note,
        marketplace_summaries=summaries or [],
    )


def _make_summary(name: str, *, description: str = "", skill_count: int = 1) -> Any:
    from prismclaw.engine.execution.request.system_prompt import MarketplaceSummary
    return MarketplaceSummary(name=name, description=description, skill_count=skill_count)


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
# 1. skills_section 契约式渲染（基础形状）
# ==========================================================================
class TestSkillsSectionRendering:
    def test_no_skills_returns_none(self):
        from prismclaw.engine.execution.request.system_prompt import skills_section
        inputs = _make_prompt_inputs(skills=[])
        assert skills_section(inputs) is None

    def test_no_skill_search_tool_returns_none(self):
        from prismclaw.engine.execution.request.system_prompt import skills_section
        inputs = _make_prompt_inputs(
            skills=[_make_skill_entry(name="x")],
            tools=[("other_tool", "desc")],
        )
        assert skills_section(inputs) is None

    def test_project_skills_only(self):
        from prismclaw.engine.execution.request.system_prompt import skills_section
        inputs = _make_prompt_inputs(skills=[
            _make_skill_entry(name="code_review"),
            _make_skill_entry(name="deep_research"),
        ])
        result = skills_section(inputs)
        assert result is not None
        assert "`code_review`" in result
        assert "`deep_research`" in result
        assert "# 插件包" not in result  # 无 marketplace 分组

    def test_marketplace_skills_listed_per_skill(self):
        from prismclaw.engine.execution.request.system_prompt import skills_section
        inputs = _make_prompt_inputs(
            skills=[
                _make_skill_entry(name="legal_contract_review", marketplace_name="legal-advisor"),
                _make_skill_entry(name="legal_compliance", marketplace_name="legal-advisor"),
                _make_skill_entry(name="medical_diagnosis", marketplace_name="medical-healthcare"),
            ],
            summaries=[
                _make_summary("legal-advisor", skill_count=2),
                _make_summary("medical-healthcare", skill_count=1),
            ],
        )
        result = skills_section(inputs)
        assert result is not None
        # 契约式渲染：每个技能名逐个可见（可见即可激活）
        assert "`legal_contract_review`" in result
        assert "`legal_compliance`" in result
        assert "`medical_diagnosis`" in result
        # marketplace 以包索引行出现（含技能计数，不可激活形状）
        assert "# 插件包 legal-advisor（2 个技能" in result
        assert "# 插件包 medical-healthcare（1 个技能" in result

    def test_mixed_project_and_marketplace_skills(self):
        from prismclaw.engine.execution.request.system_prompt import skills_section
        inputs = _make_prompt_inputs(
            skills=[
                _make_skill_entry(name="code_review"),
                _make_skill_entry(name="deep_research"),
                _make_skill_entry(name="legal_contract", marketplace_name="legal-advisor"),
                _make_skill_entry(name="legal_compliance", marketplace_name="legal-advisor"),
            ],
            summaries=[_make_summary("legal-advisor", skill_count=2)],
        )
        result = skills_section(inputs)
        assert result is not None
        assert "`code_review`" in result
        assert "`deep_research`" in result
        assert "# 插件包 legal-advisor" in result
        # 项目级技能在 marketplace 分组之前
        lines = result.split("\n")
        project_line_idx = next(i for i, l in enumerate(lines) if "`code_review`" in l)
        marketplace_line_idx = next(i for i, l in enumerate(lines) if "# 插件包 legal-advisor" in l)
        assert project_line_idx < marketplace_line_idx

    def test_output_wrapped_in_available_skills_tag(self):
        from prismclaw.engine.execution.request.system_prompt import skills_section
        inputs = _make_prompt_inputs(skills=[
            _make_skill_entry(name="code_review"),
        ])
        result = skills_section(inputs)
        assert result is not None
        assert result.startswith("<available_skills>")
        assert result.endswith("</available_skills>")
        assert "`skill_search`" in result
        assert "`activate_skill`" in result

    def test_marketplace_names_sorted(self):
        from prismclaw.engine.execution.request.system_prompt import skills_section
        inputs = _make_prompt_inputs(
            skills=[
                _make_skill_entry(name="z_skill", marketplace_name="z-market"),
                _make_skill_entry(name="a_skill", marketplace_name="a-market"),
            ],
            summaries=[
                _make_summary("z-market"),
                _make_summary("a-market"),
            ],
        )
        result = skills_section(inputs)
        assert result is not None
        lines = result.split("\n")
        mp_lines = [l for l in lines if l.startswith("# 插件包 ")]
        assert [l.split("（")[0] for l in mp_lines] == ["# 插件包 a-market", "# 插件包 z-market"]


# ==========================================================================
# 2. _hard_cap_names 逐条计费硬截断
# ==========================================================================
class TestHardCapNames:
    def test_all_fit_within_budget(self):
        from prismclaw.engine.execution.query.context_preparation import _hard_cap_names
        entries = [
            _make_skill_entry(name="a"),
            _make_skill_entry(name="b"),
        ]
        kept, note = _hard_cap_names(entries, budget_tokens=1000)
        assert len(kept) == 2
        assert note == ""

    def test_marketplace_skills_charged_per_entry(self):
        """逐条计费：同包多技能不再按组计一次，预算不足则截断。"""
        from prismclaw.engine.execution.query.context_preparation import _hard_cap_names
        entries = [
            _make_skill_entry(name="skill_1", marketplace_name="mp"),
            _make_skill_entry(name="skill_2", marketplace_name="mp"),
            _make_skill_entry(name="skill_3", marketplace_name="mp"),
        ]
        # 每条 ~1 token（name），预算 2 只够 2 条
        kept, note = _hard_cap_names(entries, budget_tokens=2)
        assert len(kept) == 2
        assert "skill_search" in note

    def test_active_skill_always_kept(self):
        from prismclaw.engine.execution.query.context_preparation import _hard_cap_names
        entries = [
            _make_skill_entry(name="active", is_active=True, priority=0),
            _make_skill_entry(name="low", priority=-10),
        ]
        # 极小预算：只保留 active
        kept, note = _hard_cap_names(entries, budget_tokens=1)
        assert any(e.is_active for e in kept)

    def test_omitted_note_when_truncated(self):
        from prismclaw.engine.execution.query.context_preparation import _hard_cap_names
        entries = [
            _make_skill_entry(name="a"),
            _make_skill_entry(name="b"),
            _make_skill_entry(name="c"),
        ]
        kept, note = _hard_cap_names(entries, budget_tokens=1)
        assert len(kept) < 3
        assert "skill_search" in note

    def test_omitted_note_simple_without_marketplace_names(self):
        """note 是简化文案（不带组名列表）——§4.10 后 marketplace 包索引行
        永不截断（marketplace_summaries 独立渲染），note 不再承担名单职责。"""
        from prismclaw.engine.execution.query.context_preparation import _hard_cap_names
        entries = [
            _make_skill_entry(name="tech-mining", marketplace_name="science-research"),
            _make_skill_entry(name="zeta", marketplace_name="legal-advisor"),
        ]
        kept, note = _hard_cap_names(entries, budget_tokens=1)
        assert len(kept) == 1
        assert "skill_search" in note
        assert "插件包" not in note

    def test_omitted_note_no_marketplace_when_all_project(self):
        """被裁技能全是项目级（无 marketplace）时 note 不带插件包列表。"""
        from prismclaw.engine.execution.query.context_preparation import _hard_cap_names
        entries = [
            _make_skill_entry(name="alpha"),
            _make_skill_entry(name="beta"),
            _make_skill_entry(name="gamma"),
        ]
        kept, note = _hard_cap_names(entries, budget_tokens=1)
        assert "插件包" not in note


# ==========================================================================
# 3. _within_budget_catalog 预算裁剪（逐条计费，超预算即 hard cap）
# ==========================================================================
class TestWithinBudgetCatalog:
    def test_under_budget_returns_all(self):
        from prismclaw.engine.execution.query.context_preparation import _within_budget_catalog
        entries = [
            _make_skill_entry(name="a"),
            _make_skill_entry(name="b"),
        ]
        result, summaries, note = _within_budget_catalog(entries, budget_tokens=1000)
        assert len(result) == 2
        assert summaries == ()
        assert note == ""

    def test_zero_budget_caps_to_minimal(self):
        """预算为 0 时插件技能行走 _hard_cap_names 极小预算；项目技能不受影响。"""
        from prismclaw.engine.execution.query.context_preparation import _within_budget_catalog
        entries = [
            _make_skill_entry(name="proj"),
            _make_skill_entry(name="plug_a", marketplace_name="mp"),
            _make_skill_entry(name="plug_b", marketplace_name="mp"),
        ]
        result, summaries, note = _within_budget_catalog(entries, budget_tokens=0)
        names = {e.name for e in result}
        assert "proj" in names  # 项目技能全量保留
        assert len([e for e in result if e.marketplace_name]) == 1  # 插件行截到极小
        # 包索引永不截断，计数含被裁技能
        assert len(summaries) == 1
        assert summaries[0].name == "mp"
        assert summaries[0].skill_count == 2

    def test_project_skills_budget_exempt(self):
        """§4.10：项目技能（用户主动安装）不参与预算，全部保留。"""
        from prismclaw.engine.execution.query.context_preparation import _within_budget_catalog
        entries = [_make_skill_entry(name=f"skill_{i}") for i in range(50)]
        result, summaries, note = _within_budget_catalog(entries, budget_tokens=10)
        assert len(result) == 50
        assert summaries == ()
        assert note == ""

    def test_marketplace_20_skills_over_budget_truncated(self):
        """逐条计费：20 个插件技能预算 10 token 必然截断 + note；包索引仍在。"""
        from prismclaw.engine.execution.query.context_preparation import _within_budget_catalog
        entries = [
            _make_skill_entry(name=f"skill_{i}", marketplace_name="mp")
            for i in range(20)
        ]
        result, summaries, note = _within_budget_catalog(entries, budget_tokens=10)
        assert len(result) < 20
        assert note != ""
        assert len(summaries) == 1
        assert summaries[0].skill_count == 20  # 计数含被裁技能


# ==========================================================================
# 4. skills_section 渲染细节（契约式渲染的逐行形状）
# ==========================================================================
class TestSkillsSectionContractRendering:
    def test_marketplace_skill_line_contains_desc(self):
        """技能行含 compact desc。"""
        from prismclaw.engine.execution.request.system_prompt import skills_section
        inputs = _make_prompt_inputs(
            skills=[
                _make_skill_entry(name="tech-mining", description="跨领域创新候选挖掘", marketplace_name="science-research"),
            ],
            summaries=[_make_summary("science-research")],
        )
        result = skills_section(inputs)
        assert "- `tech-mining` — 跨领域创新候选挖掘" in result

    def test_empty_desc_renders_bare_name_line(self):
        """desc 为空时裸名行，无 ' — ' 尾巴。"""
        from prismclaw.engine.execution.request.system_prompt import skills_section
        inputs = _make_prompt_inputs(
            skills=[
                _make_skill_entry(name="tech-mining", description="", marketplace_name="science-research"),
            ],
            summaries=[_make_summary("science-research")],
        )
        result = skills_section(inputs)
        assert "- `tech-mining`\n" in result
        assert "— " not in result.split("- `tech-mining`")[1].split("\n")[0]

    def test_group_comment_contains_truncated_description(self):
        """包索引行含截断的 marketplace 描述与技能计数。"""
        from prismclaw.engine.execution.request.system_prompt import skills_section
        long_desc = "法律顾问技能包：" + "合同与合规审查深度支持，" * 20
        inputs = _make_prompt_inputs(
            skills=[
                _make_skill_entry(name="legal", marketplace_name="legal-advisor"),
            ],
            summaries=[_make_summary("legal-advisor", description=long_desc, skill_count=3)],
        )
        result = skills_section(inputs)
        assert "# 插件包 legal-advisor — " in result
        assert "…" in result  # 截断标记
        assert "（3 个技能" in result

    def test_orphan_group_summary_survives(self):
        """§4.10 核心验收：组内技能行全部被预算裁掉时，包索引行仍渲染——
        模型始终知道"存在该能力域"，可用 skill_search 触达（tech-mining 事故修复）。"""
        from prismclaw.engine.execution.request.system_prompt import skills_section
        inputs = _make_prompt_inputs(
            skills=[
                _make_skill_entry(name="kept_skill", marketplace_name="kept-market"),
            ],
            summaries=[
                _make_summary("kept-market", skill_count=1),
                _make_summary("other-market", description="被裁包", skill_count=5),
            ],
        )
        result = skills_section(inputs)
        assert "# 插件包 kept-market" in result
        # other-market 组内无保留技能行，但包索引行必须渲染（含计数）
        assert "# 插件包 other-market — 被裁包（5 个技能" in result
        # 无技能行泄漏
        assert "other_skill" not in result


# ==========================================================================
# 5. available_skills_note 通道接通（死通道回归守卫）
# ==========================================================================
class TestNoteChannelWired:
    def test_note_rendered_before_closing_tag(self):
        """available_skills_note 非空时出现在 </available_skills> 前（死通道回归守卫）。"""
        from prismclaw.engine.execution.request.system_prompt import skills_section
        inputs = _make_prompt_inputs(
            skills=[_make_skill_entry(name="x")],
            note="另有 3 个技能未在此列出，使用 `skill_search` 按主题检索。",
        )
        result = skills_section(inputs)
        assert result is not None
        assert "另有 3 个技能未在此列出" in result
        assert result.index("另有 3 个技能未在此列出") < result.index("</available_skills>")

    def test_note_empty_not_rendered(self):
        """note 为空时无多余空行。"""
        from prismclaw.engine.execution.request.system_prompt import skills_section
        inputs = _make_prompt_inputs(skills=[_make_skill_entry(name="x")])
        result = skills_section(inputs)
        assert result is not None
        assert "未在此列出" not in result


# ==========================================================================
# 6. resolve_skill_reference 解析契约
# ==========================================================================
class TestResolveSkillReference:
    def test_unique_member_package_alias_resolves_directly(self):
        """唯一技能的包名 → resolved 直取，携带 marketplace_name 揭示。"""
        from prismclaw.engine.execution.capabilities.catalog import CapabilityCatalogService
        entries = [_make_catalog_entry(name="tech-mining", marketplace_name="science-research")]
        svc = CapabilityCatalogService()
        with mock.patch.object(CapabilityCatalogService, "list_skill_entries", return_value=entries):
            r = svc.resolve_skill_reference("science-research")
            assert r.kind == "resolved"
            assert r.entry.name == "tech-mining"
            assert r.marketplace_name == "science-research"

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
        """/science-research 前缀清洗后命中别名 pass。"""
        from prismclaw.engine.execution.capabilities.catalog import CapabilityCatalogService
        entries = [_make_catalog_entry(name="tech-mining", marketplace_name="science-research")]
        svc = CapabilityCatalogService()
        with mock.patch.object(CapabilityCatalogService, "list_skill_entries", return_value=entries):
            r = svc.resolve_skill_reference("/science-research")
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
# 7. build_skill_execution 错误消息路由
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
        entries = [_make_catalog_entry(name="tech-mining", marketplace_name="science-research")]
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
        entries = [_make_catalog_entry(name="tech-mining", marketplace_name="science-research")]
        svc = CapabilityCatalogService()
        rt = SkillRuntime(capability_catalog=svc)
        invocation = SkillInvocation(skill_name="science-research", task_context="", source="model")
        with mock.patch.object(CapabilityCatalogService, "list_skill_entries", return_value=entries):
            plan = rt.build_skill_execution(invocation, workspace_path=Path("/tmp"))
        assert "(matched via package 'science-research')" in plan.summary
        assert plan.skill_name == "science-research"


# ==========================================================================
# 8. 字节稳定性（frozen prefix 缓存约束）
# ==========================================================================
class TestByteStability:
    def test_same_inputs_render_identically(self):
        """同一 inputs 连续两次渲染逐字节相等（frozen prefix 缓存约束）。"""
        from prismclaw.engine.execution.request.system_prompt import skills_section
        inputs = _make_prompt_inputs(skills=[
            _make_skill_entry(name="a"),
            _make_skill_entry(name="b", marketplace_name="mp", marketplace_description="包描述"),
        ])
        assert skills_section(inputs) == skills_section(inputs)

    def test_is_active_does_not_affect_output(self):
        """is_active 不同的两组 entries 输出逐字节相等。"""
        from prismclaw.engine.execution.request.system_prompt import skills_section
        base = [_make_skill_entry(name="a"), _make_skill_entry(name="b", marketplace_name="mp", marketplace_description="包描述")]
        flipped = [_make_skill_entry(name="a", is_active=True), _make_skill_entry(name="b", is_active=True, marketplace_name="mp", marketplace_description="包描述")]
        assert skills_section(_make_prompt_inputs(skills=base)) == skills_section(_make_prompt_inputs(skills=flipped))


# ==========================================================================
# 9. marketplace_name 全链路传播验证
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

    def test_available_skill_entry_has_marketplace_name(self):
        from prismclaw.engine.execution.request.system_prompt import AvailableSkillEntry
        entry = AvailableSkillEntry(
            name="test_skill",
            description="desc",
            category="",
            plugin_name=None,
            priority=0,
            is_active=False,
            marketplace_name="medical-healthcare",
        )
        assert entry.marketplace_name == "medical-healthcare"


# ==========================================================================
# 11. workspace 段会话交付目录声明（交付物唯一有效落点）
# ==========================================================================
class TestWorkspaceSectionSessionOutputs:
    def test_renders_session_outputs_dir(self):
        """有 workspace_path + session_id 时渲染虚拟路径词汇表和会话交付目录契约。"""
        from prismclaw.engine.execution.request.system_prompt import workspace_section
        inputs = _make_prompt_inputs()
        inputs.workspace_path = "/tmp/ws"
        section = workspace_section(inputs)
        assert section is not None
        # T1 改：不再暴露宿主物理路径，改用虚拟前缀契约
        assert "outputs/" in section
        assert "虚拟路径前缀契约" in section
        # session id 存在时渲染 bash env 契约
        assert "PRISMCLAW_OUTPUT_DIR" in section

    def test_no_session_id_no_outputs_line(self):
        from prismclaw.engine.execution.request.system_prompt import workspace_section
        inputs = _make_prompt_inputs()
        inputs.workspace_path = "/tmp/ws"
        inputs.session_id = ""
        section = workspace_section(inputs)
        assert section is not None
        # 无 session_id 时不渲染 bash env 契约
        assert "PRISMCLAW_OUTPUT_DIR" not in section


# ==========================================================================
# 10. 任务级技能路由（§4.10 动态层：_route_skill_hints 三分支）
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