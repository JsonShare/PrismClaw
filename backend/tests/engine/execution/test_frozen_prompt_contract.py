"""冻结 prompt 契约 + 子代理工具注册验收。

锁定 SDD §10.1 的验收项：
- 冻结段顺序与 stability（stable 连续位于 semi_stable 前）；
- 冻结段不含请求级状态（时间戳 / refreshed_at / active skill / session id）；
- 专家包子代理名与 repo_map 不出现在固定 prompt；
- ``subagent_search`` 位于治理通用工具面；
- ``register_subagent_tools`` 在启用时注册四件套、禁用时清理。
"""

from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

from prismclaw.engine.execution.request._prompt_types import (
    AvailableSubagentEntry,
    MarketplaceSummary,
    PromptInputs,
)
from prismclaw.engine.execution.request.system_prompt import (
    build_frozen_prefix,
    build_frozen_prompt_sections,
)
from prismclaw.engine.experts.prompt_slot import (
    ROLE_OVERRIDE,
    build_expert_agent_prompt,
)
from prismclaw.schema.agent.domains import InvocationPolicy


def _inputs() -> PromptInputs:
    return PromptInputs(
        session_id="sess-123",
        workspace_id="ws-456",
        agent_id="agent-1",
        identity="你是 PrismClaw。",
        preferences="<preferences>\n- 语言：跟随用户\n</preferences>",
        tools=[
            ("tool_search", "d"),
            ("skill_search", "d"),
            ("subagent_search", "d"),
            ("sub_agent", "d"),
            ("read", "d"),
            ("todo_write", "d"),
        ],
        project_skill_names=("code_review",),
        marketplace_summaries=(MarketplaceSummary("legal", "法律能力包", 4),),
        available_subagents=(
            AvailableSubagentEntry("architect", "架构规划"),
            AvailableSubagentEntry(
                "docket-watcher",
                "案卷监控",
                expert_name="legal",
            ),
        ),
        workspace_path="/tmp/ws",
        subagent_registry_size=2,
    )


_LEGACY_MARKERS = (
    "[System Time]",
    "[Active Skill]",
    "[Active Command]",
    "[Skill Status]",
    "[Allowed Tools Override]",
    "[TaskSpec]",
    "[Task Goal]",
    "[Task Pre-flight]",
    "[Task Constraints]",
    "[Task Strategy]",
)


def test_runtime_context_uses_xml_tags() -> None:
    """运行时上下文统一为 XML 子标签，不再使用 [X] 前缀。"""
    import inspect

    from prismclaw.engine.execution.request.system_prompt import runtime_context_section

    text = runtime_context_section(_inputs())
    assert text is not None
    assert text.startswith("<runtime_context>")
    assert "<system_time>" in text
    assert "<time_rule>" in text

    source = inspect.getsource(runtime_context_section)
    for marker in _LEGACY_MARKERS:
        assert marker not in source


def test_soft_landing_prompt_is_xml_and_unnumbered() -> None:
    """软着陆指令用 XML 包裹，且不占用编号（避免与工作循环的编号冲突）。"""
    from prismclaw.engine.execution.loop.stream import _SOFT_LANDING_PROMPT

    body = _SOFT_LANDING_PROMPT.strip()
    assert body.startswith('<system_directive scope="final_turn">')
    assert body.endswith("</system_directive>")
    assert "\n1." not in _SOFT_LANDING_PROMPT


def test_frozen_section_order_and_stability() -> None:
    sections = build_frozen_prompt_sections(_inputs())
    names = [s.name for s in sections]
    assert names == [
        "role",
        "principles",
        "tools",
        "delegation",
        "workspace",
        "data_safety",
        "reference_sources",
        "catalogs",
    ]
    stabilities = [s.stability for s in sections]
    assert stabilities[:-1] == ["stable"] * (len(stabilities) - 1)
    assert stabilities[-1] == "semi_stable"


def test_expert_persona_is_one_complete_critical_section_after_role() -> None:
    body = "\n".join(f"Expert rule {i}: preserve every requirement." for i in range(1500))
    persona = build_expert_agent_prompt(
        f"---\nname: writer\n---\n{body}"
    )
    inputs = replace(_inputs(), expert_persona=persona)

    sections = build_frozen_prompt_sections(inputs)
    names = [section.name for section in sections]

    assert names[:3] == ["role", "expert", "principles"]
    expert = sections[names.index("expert")]
    assert expert.text == persona
    assert expert.priority == "critical"
    assert expert.truncatable is False

    result = build_frozen_prefix(inputs, budget=2000)
    expert_block = next(block for block in result.blocks if block.source == "expert")
    assert expert_block.content == persona
    assert expert_block.truncatable is False
    assert result.text.count(ROLE_OVERRIDE) == 1
    assert body in result.text


def test_expert_fragments_are_copied_without_workbuddy_template_residue() -> None:
    from prismclaw.engine.execution.request.expert_fragments import (
        EXPERT_AGENT_LOOP,
        EXPERT_RESULT_PRESENTATION,
        EXPERT_TASK_MANAGEMENT,
        EXPERT_TOOL_USE,
    )

    assert EXPERT_AGENT_LOOP.startswith("<agent_loop>\n")
    assert "<final_answer_instructions>" in EXPERT_AGENT_LOOP
    assert "{{" not in EXPERT_TOOL_USE
    assert "{%" not in EXPERT_TOOL_USE
    assert "Tencent Docs" not in EXPERT_TOOL_USE
    assert "WecomAgentAvailable" not in EXPERT_TOOL_USE
    for legacy_tool in ("TaskCreate", "TaskGet", "TaskUpdate", "TaskList"):
        assert legacy_tool not in EXPERT_TASK_MANAGEMENT
    assert "todo_write" in EXPERT_TASK_MANAGEMENT
    assert "present_files" in EXPERT_RESULT_PRESENTATION
    assert "<final_answer_instructions>" in EXPERT_RESULT_PRESENTATION


def test_frozen_contains_no_request_state() -> None:
    text = "\n".join(s.text for s in build_frozen_prompt_sections(_inputs()))
    for marker in ("sess-123", "ws-456", "refreshed_at", "active_skill", "当前时间", "当前模式"):
        assert marker not in text


def test_expert_capability_names_not_in_frozen_prompt() -> None:
    text = "\n".join(s.text for s in build_frozen_prompt_sections(_inputs()))
    assert "docket-watcher" not in text
    assert "litigation" not in text
    assert "repository_map" not in text
    assert "<packages>" in text
    assert "`legal`" in text


def test_workflow_section_requires_todo_write() -> None:
    inputs = replace(_inputs(), tools=[("read", "d")])
    names = [s.name for s in build_frozen_prompt_sections(inputs)]
    assert "workflow" not in names


def test_subagent_search_is_governed_universal_and_core_visible() -> None:
    from prismclaw.engine.toolkit.registry.governance import (
        _CORE_VISIBLE_TOOLS,
        _UNIVERSAL_TOOLS,
    )

    assert "subagent_search" in _UNIVERSAL_TOOLS
    assert "subagent_search" in _CORE_VISIBLE_TOOLS
    assert "todo_write" in _UNIVERSAL_TOOLS
    assert "todo_write" in _CORE_VISIBLE_TOOLS


# ---------------------------------------------------------------------------
# register_subagent_tools：启动/刷新共用注册点
# ---------------------------------------------------------------------------
class _FakeCatalog:
    def count_agent_entries(self) -> int:
        return 1

    def list_agent_entries(self) -> list[object]:
        return []


class _FakeSubAgentTool:
    tool_class = "write"

    def __init__(self, **kwargs: object) -> None:
        self.name = "sub_agent"


class _FakeExecutor:
    def __init__(self, **kwargs: object) -> None:
        pass


def _register(monkeypatch: pytest.MonkeyPatch, *, enabled: bool) -> set[str]:
    import prismclaw.engine.subagent as subagent_pkg
    from prismclaw.engine.execution.capabilities.catalog import RuntimeCatalog
    from prismclaw.engine.execution.skills.runtime import SkillRuntime
    from prismclaw.engine.subagent.definition.registry import SubAgentRegistry
    from prismclaw.engine.toolkit.registry.registry import ToolRegistry
    from prismclaw.infra.settings import load_settings
    from prismclaw.runtime.capability_refresh import register_subagent_tools
    from prismclaw.schema.llm.types import LLMClient

    monkeypatch.setattr(subagent_pkg, "SubAgentExecutor", _FakeExecutor)
    monkeypatch.setattr(subagent_pkg, "SubAgentTool", _FakeSubAgentTool)

    settings = load_settings(force_reload=True)
    settings = settings.model_copy(
        update={"subagent": settings.subagent.model_copy(update={"enabled": enabled})}
    )
    registry = ToolRegistry()
    register_subagent_tools(
        registry,
        settings=settings,
        capability_catalog=cast(RuntimeCatalog, _FakeCatalog()),
        subagent_registry=cast(SubAgentRegistry, object()),
        llm_client=cast(LLMClient, object()),
        memory_store=None,
        skill_runtime=cast(SkillRuntime, None),
    )
    return set(registry.names())


def test_register_subagent_tools_installs_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    names = _register(monkeypatch, enabled=True)
    assert {
        "sub_agent",
        "subagent_search",
    } <= names


def test_register_subagent_tools_clears_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    names = _register(monkeypatch, enabled=False)
    assert not names & {
        "sub_agent",
        "subagent_search",
    }


# ---------------------------------------------------------------------------
# _build_shell_snapshot：按本轮可见集构建壳，技能/子代理按披露层级分区
# ---------------------------------------------------------------------------
def test_build_shell_snapshot_partitions_skill_and_subagent_disclosure() -> None:
    """壳构建必须能真实走通，且只把项目技能名放进固定前缀。

    ``<skills>`` / ``<subagents>`` 的存在取决于可见集：项目技能逐个列名，
    专家包技能与专家包子代理只保留在目录中供 ``skill_search`` /
    ``subagent_search`` 检索，不写入冻结 prompt。
    """
    import asyncio
    from types import SimpleNamespace

    from prismclaw.engine.execution.capabilities.catalog import RuntimeCatalog
    from prismclaw.engine.execution.loop.deps import AgentRunnerDeps
    from prismclaw.engine.execution.query._static_context_types import (
        StaticTurnShellSnapshot,
    )
    from prismclaw.engine.execution.query.context_preparation import StaticContextBuilder
    from prismclaw.schema.agent.types import RunRequest

    # 假工具条目：llm_name + description
    class _ToolEntry:
        def __init__(self, name: str, desc: str) -> None:
            self.llm_name = name
            self.description = desc

    # 假技能条目
    class _SkillEntry:
        def __init__(self, name: str, expert_name: str | None = None) -> None:
            self.name = name
            self.expert_name = expert_name
            self.expert_description = "测试专家包" if expert_name else ""
            self.manifest = SimpleNamespace(invocation_policy=InvocationPolicy.AUTO)

    # 假子代理条目
    class _AgentEntry:
        def __init__(self, name: str, expert_name: str | None = None) -> None:
            self.name = name
            self.description = f"{name} 描述"
            self.expert_name = expert_name
            self.expert_description = "测试包" if expert_name else None

    class _FakeCatalog:
        def list_visible_tool_entries(self, **kwargs: object) -> list[_ToolEntry]:
            return [
                _ToolEntry("read", "读文件"),
                _ToolEntry("skill_search", "检索技能"),
                _ToolEntry("sub_agent", "派生子代理"),
                _ToolEntry("subagent_search", "检索子代理"),
            ]

        def list_agent_entries(self) -> list[_AgentEntry]:
            return [
                _AgentEntry("architect"),  # 核心子代理
                _AgentEntry("expert-agent", expert_name="test-pkg"),  # 专家包子代理
            ]

        def list_skill_entries(self) -> list[_SkillEntry]:
            return [
                _SkillEntry("project-skill"),  # 项目技能
                _SkillEntry("expert-skill", expert_name="test-pkg"),  # 专家包技能
            ]

    class _FakeTools:
        def get(self, name: str) -> None:
            return None

        def wire_tokens_estimate(self) -> int:
            return 100

        def names(self) -> list[str]:
            return []

    request = SimpleNamespace(
        session_id="sess-1",
        workspace_id="ws-1",
        agent_id="agent-1",
    )

    budget_cfg = SimpleNamespace(
        compute_history_budget=lambda **kw: 8000,
        system_zone_tokens=8192,
        dynamic_zone_tokens=12288,
        context=SimpleNamespace(budget=SimpleNamespace()),
    )
    budget_cfg.context.budget = budget_cfg  # zone_of 读 deps.config.context.budget

    async def _fake_get_bootstrap(sid: str) -> None:
        return None

    deps = SimpleNamespace(
        tools=_FakeTools(),
        context_bootstrap_provider=SimpleNamespace(
            get_bootstrap=_fake_get_bootstrap,
        ),
        config=SimpleNamespace(
            context=SimpleNamespace(
                budget=budget_cfg,
                compaction=SimpleNamespace(
                    history=SimpleNamespace(keep_recent_tokens=0),
                ),
            ),
        ),
        ports=None,
        session_queue=object(),  # 非 None → headless=False
    )

    builder = StaticContextBuilder()
    snapshot = asyncio.run(
        builder._build_shell_snapshot(
            request=cast(RunRequest, request),
            deps=cast(AgentRunnerDeps, deps),
            capability_catalog=cast(RuntimeCatalog, _FakeCatalog()),
            tool_workspace_path="/tmp/ws",
            effective_model="test-model",
            subagent_depth=0,
            visible_tool_names={
                "read",
                "skill_search",
                "sub_agent",
                "subagent_search",
            },
        )
    )

    assert isinstance(snapshot, StaticTurnShellSnapshot)
    assert snapshot.model == "test-model"
    # 项目技能名进入 available_skills；专家包技能不进
    assert "project-skill" in snapshot.available_skills
    assert "expert-skill" not in snapshot.available_skills
    # 核心子代理 + 专家包子代理都在目录里（专家包子代理供 subagent_search 检索）
    agent_names = {a.name for a in snapshot.available_subagents}
    assert "architect" in agent_names
    assert "expert-agent" in agent_names
    # frozen_result 非空
    assert snapshot.frozen_result.text


def test_behavior_boundaries_are_present() -> None:
    """派发后不重复在 delegation 段；澄清门槛由 ask_user_question 描述唯一承担。"""
    from prismclaw.engine.execution.request.system_prompt import delegation_section

    delegation_text = delegation_section(_inputs())
    assert delegation_text is not None
    assert "已派发的任务不由主 Agent 重做" in delegation_text
