from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from prismclaw.engine.execution.capabilities.catalog import (
    RuntimeCatalog,
    SkillCatalogEntry,
)
from prismclaw.engine.execution.loop.deps import ExpertBundle
from prismclaw.engine.execution.query._catalog_builder import _build_available_skills_catalog
from prismclaw.engine.execution.skills.runtime import SkillRuntime
from prismclaw.engine.execution.skills.selection import replace_session_skill_selection
from prismclaw.engine.experts.codes import ExpertError, ExpertErrorCode
from prismclaw.engine.experts.scope import (
    UNRESTRICTED_SURFACE,
    ActiveScope,
    NoExpertScope,
    RejectedScope,
    build_session_scope,
    tool_surface_of,
)
from prismclaw.engine.experts.loading.load_result import ExpertPackage
from prismclaw.engine.experts.manifest import ExpertManifest
from prismclaw.engine.experts.registry.expert_registry import ExpertRegistry
from prismclaw.infra.environment_catalog import EnvironmentCatalog


def _catalog(expert_registry, capability_policy_registry):
    return EnvironmentCatalog(
        expert_registry=expert_registry,
        capability_policy_registry=capability_policy_registry,
    ).snapshot()


from prismclaw.engine.subagent.definition.manifest import AgentManifest
from prismclaw.engine.toolkit.builtin.skill_activate import ActivateSkillTool
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.infra.config.capability_policy import CapabilityPolicyRegistry
from prismclaw.schema.agent.session import (
    SessionEvent,
    SessionEventType,
    SessionHeader,
)
from prismclaw.schema.services.skills import (
    SkillExecutionStrategy,
    SkillInvocation,
    SkillInvocationState,
    SkillManifest,
)
from tests.engine.session_store import FakeRedisSessionStore


class _Catalog:
    def __init__(self, experts: list[ExpertPackage]) -> None:
        self._experts = experts

    def known_ids(self, kind: str) -> frozenset[str]:
        if kind in ("expert", "team"):
            expected_type = "agent" if kind == "expert" else "team"
            return frozenset(
                expert.name
                for expert in self._experts
                if expert.source == "experts"
                and expert.manifest is not None
                and expert.manifest.expert_type == expected_type
            )
        if kind == "skill":
            return frozenset(
                f"{expert.name}:{skill.name}"
                for expert in self._experts
                for skill in expert.skills
            )
        return frozenset()


def _expert(
    tmp_path: Path,
    name: str,
    *,
    source: str = "experts",
    expert_type: str = "agent",
    skills: tuple[str, ...] = (),
    persona: str = "Expert persona",
) -> ExpertPackage:
    root = tmp_path / source / name
    root.mkdir(parents=True, exist_ok=True)
    manifest = ExpertManifest.model_validate(
        {
            "name": name,
            "expertType": expert_type,
            "agentName": f"{name}-lead",
        }
    )
    if source == "experts":
        agents_dir = root / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        (agents_dir / f"{name}-lead.md").write_text(persona, encoding="utf-8")
    agents = (
        [
            AgentManifest(
                name=f"{name}-lead",
                description="Expert lead",
                system_prompt=persona,
            )
        ]
        if source == "experts"
        else []
    )
    return ExpertPackage(
        name=name,
        source=source,
        path=str(root),
        root_path=root,
        manifest=manifest,
        skills=[SkillManifest(name=skill, description=skill) for skill in skills],
        agents=agents,
    )


def _registries(
    tmp_path: Path,
    experts: list[ExpertPackage],
    *,
    state_yaml: str = "version: 1\n",
) -> tuple[ExpertRegistry, CapabilityPolicyRegistry]:
    state_path = tmp_path / "capability_policy.yaml"
    state_path.write_text(state_yaml, encoding="utf-8")
    return (
        ExpertRegistry(experts),
        CapabilityPolicyRegistry(state_path, catalog=_Catalog(experts)),
    )


def _header(session_id: str, **expert_fields: object) -> SessionHeader:
    return SessionHeader(
        id=session_id,
        workspace_id="workspace",
        agent_id="agent",
        **expert_fields,
    )


def _skill_catalog_entry(
    name: str,
    *,
    expert_name: str | None,
    body: str = "",
    expert_description: str | None = None,
) -> SkillCatalogEntry:
    manifest = SkillManifest(
        name=name,
        description=f"description of {name}",
        body=body,
        source="skill",
        file_path=f"/tmp/{expert_name or 'project'}/{name}/SKILL.md",
    )
    return SkillCatalogEntry(
        name=name,
        description=manifest.description,
        location=manifest.file_path,
        instructions=body,
        manifest=manifest,
        expert_name=expert_name,
        expert_description=expert_description,
    )


def test_views_are_session_scoped_and_idempotent(tmp_path: Path) -> None:
    expert = _expert(
        tmp_path,
        "writer",
        skills=("draft",),
        persona="---\nname: writer-lead\n---\nWrite precisely",
    )
    builtin, states = _registries(tmp_path, [expert])
    selected = _header(
        "session-a",
        expert_id="writer",
        expert_activation_source="user",
    )
    unselected = _header("session-b")
    before = selected.model_dump()

    selected_view = build_session_scope(selected, _catalog(builtin, states))
    unselected_view = build_session_scope(unselected, _catalog(builtin, states))

    assert isinstance(selected_view, ActiveScope)
    assert selected_view.expert.expert_id == "writer@experts"
    assert selected_view.persona_source is not None
    assert selected_view.persona_source.name == "writer-lead.md"
    assert selected_view.persona_source.read_text(encoding="utf-8").endswith("Write precisely")
    assert selected_view.tool_surface.restricted is True
    assert isinstance(unselected_view, NoExpertScope)
    assert unselected_view.tool_surface.restricted is False
    assert build_session_scope(selected, _catalog(builtin, states)) == selected_view
    assert selected.model_dump() == before


def test_single_agent_expert_is_not_a_team(tmp_path: Path) -> None:
    """非团队专家不得暴露 team_name：子代理入口据此判定团队校验与成员面收窄。"""
    agent_expert = _expert(tmp_path, "writer")
    team_expert = _expert(tmp_path, "pod", expert_type="team")
    builtin, states = _registries(tmp_path, [agent_expert, team_expert])

    agent_view = build_session_scope(
        _header("s1", expert_id="writer", expert_activation_source="user"),
        _catalog(builtin, states),
    )
    team_view = build_session_scope(
        _header("s2", expert_id="pod", expert_activation_source="user"),
        _catalog(builtin, states),
    )

    assert isinstance(agent_view, ActiveScope)
    assert agent_view.is_team is False
    assert agent_view.team_name is None
    assert agent_view.lead_agent is None
    assert isinstance(team_view, ActiveScope)
    assert team_view.is_team is True
    assert team_view.team_name == "pod"
    assert team_view.lead_agent is not None


@pytest.mark.parametrize("source", ["user"])
def test_user_activation_source_is_supported_and_scope_build_is_pure(
    tmp_path: Path, source: str
) -> None:
    expert = _expert(tmp_path, "writer")
    builtin, states = _registries(tmp_path, [expert])
    header = _header("session", expert_id="writer", expert_activation_source=source)

    views = [build_session_scope(header, _catalog(builtin, states)) for _ in range(4)]

    assert all(isinstance(view, ActiveScope) for view in views)
    assert views == [views[0]] * 4


@pytest.mark.parametrize("source", ["model", "scene", "template", "deeplink", "default"])
def test_non_user_activation_source_is_rejected(tmp_path: Path, source: str) -> None:
    """隐式激活通道已删除：只有 user 选中的会话能生效。"""
    expert = _expert(tmp_path, "writer")
    builtin, states = _registries(tmp_path, [expert])
    header = _header("session", expert_id="writer", expert_activation_source=source)

    view = build_session_scope(header, _catalog(builtin, states))

    assert isinstance(view, RejectedScope)
    assert view.code == ExpertErrorCode.EXPERT_UNAVAILABLE
    assert view.expert_id == "writer"


def test_legacy_header_without_activation_source_stays_active(tmp_path: Path) -> None:
    """字段引入前落库的会话没有 source，按显式选择继续生效（数据兼容，非逻辑兼容）。"""
    expert = _expert(tmp_path, "writer")
    builtin, states = _registries(tmp_path, [expert])
    header = _header("session", expert_id="writer", expert_activation_source=None)

    assert isinstance(build_session_scope(header, _catalog(builtin, states)), ActiveScope)


@pytest.mark.parametrize("legacy_source", ["session", "marketplace"])
def test_legacy_expert_id_is_unavailable_without_implicit_mapping(
    tmp_path: Path, legacy_source: str
) -> None:
    writer = _expert(tmp_path, "writer")
    builtin, states = _registries(tmp_path, [writer])
    legacy_id = f"writer@{legacy_source}"

    view = build_session_scope(
        _header("session", expert_id=legacy_id, expert_activation_source="user"),
        _catalog(builtin, states)
    )

    assert isinstance(view, RejectedScope)
    assert view.code == ExpertErrorCode.EXPERT_UNAVAILABLE
    assert view.expert_id == legacy_id


def test_ambiguous_expert_id_degrades_and_full_id_selects_source(tmp_path: Path) -> None:
    deployed = _expert(tmp_path, "writer")
    alternate = _expert(tmp_path, "writer", source="builtin")
    builtin, states = _registries(tmp_path, [deployed, alternate])

    ambiguous = build_session_scope(
        _header("ambiguous", expert_id="writer", expert_activation_source="user"),
        _catalog(builtin, states)
    )
    exact_views = [
        build_session_scope(
            _header("exact", expert_id=expert_id, expert_activation_source="user"),
            _catalog(builtin, states)
    )
        for expert_id in ("writer@experts", "writer@builtin")
    ]

    assert isinstance(ambiguous, RejectedScope)
    assert ambiguous.code == ExpertErrorCode.EXPERT_ID_AMBIGUOUS
    assert ambiguous.expert_id == "writer"
    assert ambiguous.reason is not None
    assert "builtin" in ambiguous.reason
    assert "experts" in ambiguous.reason
    assert [view.expert.expert_id for view in exact_views if isinstance(view, ActiveScope)] == [
        "writer@experts",
        "writer@builtin",
    ]


def test_missing_expert_degrades_with_structured_unavailable_reason(
    tmp_path: Path,
) -> None:
    builtin, states = _registries(tmp_path, [])

    view = build_session_scope(
        _header("session", expert_id="missing", expert_activation_source="user"),
        _catalog(builtin, states)
    )

    assert isinstance(view, RejectedScope)
    assert view.code == ExpertErrorCode.EXPERT_UNAVAILABLE
    assert view.kind == "expert"
    assert view.expert_id == "missing"


@pytest.mark.parametrize(
    ("expert_type", "state_kind"),
    [("agent", "expert"), ("team", "team")],
)
def test_disabled_expert_degrades_with_module_disabled_reason(
    tmp_path: Path, expert_type: str, state_kind: str
) -> None:
    expert = _expert(tmp_path, "writer", expert_type=expert_type)
    builtin, states = _registries(
        tmp_path,
        [expert],
        state_yaml=(
            "version: 1\n"
            "overrides:\n"
            f"  {state_kind}:\n"
            "    writer:\n"
            "      enabled: false\n"
            "      reason: legal review\n"
        ),
    )

    view = build_session_scope(
        _header("session", expert_id="writer@experts", expert_activation_source="user"),
        _catalog(builtin, states)
    )

    assert isinstance(view, RejectedScope)
    assert view.code == ExpertErrorCode.MODULE_DISABLED
    assert view.kind == state_kind
    assert view.expert_id == "writer"
    assert view.reason == "legal review"


@pytest.mark.parametrize(
    ("expert_id", "state_yaml"),
    [
        (
            "writer@experts",
            "version: 1\noverrides:\n  expert:\n    writer:\n      enabled: false\n",
        ),
        ("nowhere@experts", "version: 1\n"),
    ],
)
def test_rejected_expert_yields_empty_surface(
    tmp_path: Path, expert_id: str, state_yaml: str
) -> None:
    """选了专家但不可用：工具面必须是受限的空面，否则就是「少给变多给」。"""
    expert = _expert(tmp_path, "writer")
    builtin, states = _registries(tmp_path, [expert], state_yaml=state_yaml)

    view = build_session_scope(
        _header("session", expert_id=expert_id, expert_activation_source="user"),
        _catalog(builtin, states),
    )

    assert isinstance(view, RejectedScope)
    surface = tool_surface_of(view)
    assert surface is not None
    assert surface.restricted is True
    assert surface.active == frozenset()
    assert surface.deferred == frozenset()
    assert surface.member == frozenset()
    assert not surface.allows("web_fetch")
    assert not surface.allows("web_fetch", member=True)


def test_tool_surface_covers_agent_team_deferred_member_and_mcp(tmp_path: Path) -> None:
    agent = _expert(tmp_path, "writer", expert_type="agent")
    team = _expert(tmp_path, "legal-team", expert_type="team")
    builtin, states = _registries(tmp_path, [agent, team])

    agent_view = build_session_scope(
        _header("agent", expert_id="writer", expert_activation_source="user"),
        _catalog(builtin, states)
    )
    team_view = build_session_scope(
        _header("team", expert_id="legal-team", expert_activation_source="user"),
        _catalog(builtin, states)
    )
    no_expert_view = build_session_scope(_header("plain"), _catalog(builtin, states))

    assert len(agent_view.tool_surface.active) == 12
    assert len(agent_view.tool_surface.deferred) == 12
    assert len(agent_view.tool_surface.member) == 12
    assert len(team_view.tool_surface.active) == 14
    assert len(team_view.tool_surface.deferred) == 16
    assert {"sub_agent", "subagent_control"} <= team_view.tool_surface.active
    assert not {"team_create", "team_delete"} & team_view.tool_surface.active
    assert {"team_create", "team_delete"} <= team_view.tool_surface.deferred
    assert not {"sub_agent", "subagent_control"} & (
        team_view.tool_surface.member
    )
    assert not agent_view.tool_surface.allows("web_fetch")
    assert team_view.tool_surface.allows("team_create", include_deferred=True)
    assert not team_view.tool_surface.allows("team_create")
    assert team_view.tool_surface.allows("legal-core.search:lookup")
    assert team_view.tool_surface.allows("legal-core.search:lookup", member=True)
    assert no_expert_view.tool_surface.allows("web_fetch")


def test_skill_scope_unions_enabled_packages_and_explicit_selections(
    tmp_path: Path,
) -> None:
    research = _expert(
        tmp_path,
        "research",
        skills=("base", "disabled", "selected"),
    )
    writer = _expert(tmp_path, "writer", skills=("draft", "disabled"))
    other = _expert(tmp_path, "other", skills=("automatic", "selected"))
    unlisted = _expert(tmp_path, "unlisted", skills=("selected",))
    builtin, states = _registries(
        tmp_path,
        [research, writer, other],
        state_yaml=(
            "version: 1\n"
            "overrides:\n"
            "  skill:\n"
            "    research:disabled: false\n"
            "    writer:disabled: false\n"
        ),
    )
    # Keep an undeployed package in the state catalog without registering it.
    states = CapabilityPolicyRegistry(
        tmp_path / "capability_policy.yaml",
        catalog=_Catalog([research, writer, other, unlisted]),
    )
    header = _header(
        "session",
        expert_id="writer",
        expert_activation_source="user",
        selected_skill_ids=[
            "research:selected",
            "other:selected",
            "research:selected",
            "research:disabled",
            "unlisted:selected",
            "missing:nope",
            "bad",
        ],
    )

    view = build_session_scope(header, _catalog(builtin, states))

    assert view.tool_owners == frozenset({"research", "writer", "other"})


def test_skill_scope_without_expert_includes_only_enabled_selections(
    tmp_path: Path,
) -> None:
    writer = _expert(tmp_path, "writer", skills=("automatic", "shared"))
    other = _expert(tmp_path, "other", skills=("selected", "disabled"))
    builtin, states = _registries(
        tmp_path,
        [writer, other],
        state_yaml=("version: 1\noverrides:\n  skill:\n    other:disabled: false\n"),
    )
    header = _header(
        "session",
        selected_skill_ids=["other:selected", "other:selected", "other:disabled"],
    )

    view = build_session_scope(header, _catalog(builtin, states))

    assert isinstance(view, NoExpertScope)
    # 未选专家不是降级：工具面全量，但技能归属仍要收窄。
    assert view.tool_surface.restricted is False
    assert view.tool_surface.allows("web_fetch")
    assert view.tool_owners == frozenset({"other"})


def test_skill_catalog_groups_entries_by_expert_name(tmp_path: Path) -> None:
    entries = [
        _skill_catalog_entry(
            "search",
            expert_name="research",
            expert_description="Research package",
        ),
        _skill_catalog_entry("draft", expert_name="writer", expert_description="Writer package"),
        _skill_catalog_entry("fact-check", expert_name="research"),
        _skill_catalog_entry("local", expert_name=None),
    ]
    catalog = RuntimeCatalog()

    with patch.object(RuntimeCatalog, "list_skill_entries", return_value=entries):
        project_names, summaries = _build_available_skills_catalog(
            capability_catalog=catalog,
            workspace_path=str(tmp_path),
            tool_summaries=[("skill_search", "search skills")],
        )

    assert project_names == ("local",)
    assert [(item.name, item.description, item.skill_count) for item in summaries] == [
        ("research", "Research package", 2),
        ("writer", "Writer package", 1),
    ]


def test_same_named_skill_resolution_honors_expert_preference_without_fallback() -> None:
    entries = [
        _skill_catalog_entry("shared", expert_name="research", body="RESEARCH BODY"),
        _skill_catalog_entry("shared", expert_name="writer", body="WRITER BODY"),
    ]
    catalog = RuntimeCatalog()

    with patch.object(RuntimeCatalog, "list_skill_entries", return_value=entries):
        preferred = catalog.resolve_skill_reference("shared", expert_prefer="writer")
        assert preferred.entry is not None
        assert preferred.entry.expert_name == "writer"
        assert preferred.entry.manifest.body == "WRITER BODY"
        with pytest.raises(LookupError, match="ambiguous across experts"):
            catalog.resolve_skill_reference("shared")
        with pytest.raises(LookupError, match="not available in expert 'missing'"):
            catalog.resolve_skill_reference("shared", expert_prefer="missing")


def test_expert_member_preload_skill_resolves_by_bare_suffix() -> None:
    """成员 md 的 `skills:`（preload_skills）用裸名，须解析到专家包命名空间条目。"""
    entry = _skill_catalog_entry(
        "medical-clinical:clinical-note-extract",
        expert_name="medical-clinical",
        body="NOTE",
    )
    catalog = RuntimeCatalog()

    with patch.object(RuntimeCatalog, "list_skill_entries", return_value=[entry]):
        resolution = catalog.resolve_skill_reference("clinical-note-extract")

    assert resolution.kind == "resolved"
    assert resolution.entry is entry


def test_unique_skill_remains_resolvable_with_unrelated_active_expert() -> None:
    entry = _skill_catalog_entry("capability-only", expert_name="research", body="CAPABILITY")
    catalog = RuntimeCatalog()

    with patch.object(RuntimeCatalog, "list_skill_entries", return_value=[entry]):
        resolution = catalog.resolve_skill_reference("capability-only", expert_prefer="writer")

    assert resolution.entry is entry


def test_activate_skill_selects_current_expert_version_and_records_expert(
    tmp_path: Path,
) -> None:
    entries = [
        _skill_catalog_entry("shared", expert_name="research", body="RESEARCH BODY"),
        _skill_catalog_entry("shared", expert_name="writer", body="WRITER BODY"),
    ]
    catalog = RuntimeCatalog()
    runtime = SkillRuntime(capability_catalog=catalog)
    build_skill_execution = runtime.build_skill_execution
    plans: list[SkillExecutionStrategy] = []

    def capture_build(
        invocation: SkillInvocation,
        *,
        workspace_path: Path,
        parent_active_state: SkillInvocationState | None = None,
    ) -> SkillExecutionStrategy:
        plan = build_skill_execution(
            invocation,
            workspace_path=workspace_path,
            parent_active_state=parent_active_state,
        )
        plans.append(plan)
        return plan

    tool = ActivateSkillTool(skill_runtime=runtime, capability_catalog=catalog)
    active_state = SkillInvocationState(
        skill_name="workflow",
        is_active=True,
        expert_name="writer",
    )
    context = ToolContext(
        workspace_id="workspace",
        workspace_path=tmp_path,
        session_scope=NoExpertScope(
            tool_surface=UNRESTRICTED_SURFACE,
            tool_owners=frozenset({"research", "writer"}),
            member=False,
        ),
        invoked_skill_state={"active_invocation": active_state},
    )

    with (
        patch.object(RuntimeCatalog, "list_skill_entries", return_value=entries),
        patch.object(runtime, "build_skill_execution", side_effect=capture_build),
    ):
        result = asyncio.run(tool.execute({"skill_name": "shared"}, context))

    assert not result.is_error, result.content
    assert len(plans) == 1
    plan = plans[0]
    assert plan.invocation_state is not None
    assert plan.invocation_state.expert_name == "writer"
    assert plan.expert_name == "writer"
    assert result.runtime_control is not None
    modifier = result.runtime_control.context_modifier
    assert modifier is not None
    injected = "\n".join(
        str(message.get("content", ""))
        for message in modifier.append_messages
        if isinstance(message, dict)
    )
    assert "WRITER BODY" in injected
    assert "RESEARCH BODY" not in injected


def test_runner_expert_bundle_builds_a_view_for_each_session_header(
    tmp_path: Path,
) -> None:
    writer = _expert(tmp_path, "writer")
    builtin, states = _registries(tmp_path, [writer])
    bundle = ExpertBundle(
        environment_catalog=EnvironmentCatalog(
            expert_registry=builtin,
            capability_policy_registry=states,
        ),
    )

    selected = bundle.build_session_scope(
        _header("selected", expert_id="writer", expert_activation_source="user")
    )
    unselected = bundle.build_session_scope(_header("unselected"))

    assert isinstance(selected, ActiveScope)
    assert selected.expert.name == "writer"
    assert isinstance(unselected, NoExpertScope)
    assert selected == build_session_scope(
        _header("selected", expert_id="writer", expert_activation_source="user"),
        _catalog(builtin, states)
    )


async def _selection_store(header: SessionHeader) -> FakeRedisSessionStore:
    store = FakeRedisSessionStore()
    await store.commit_events(
        [
            SessionEvent(
                seq=0,
                type=SessionEventType.SESSION_CREATED,
                session_id=header.id,
                data={"type": "session_created", "header": header.model_dump()},
            )
        ]
    )
    return store


@pytest.mark.parametrize("expert_id", [None, "writer"])
async def test_skill_selection_writes_only_selected_ids_and_expands_scope_across_packages(
    tmp_path: Path, expert_id: str | None
) -> None:
    research = _expert(tmp_path, "research", skills=("base", "selected"))
    writer = _expert(tmp_path, "writer", skills=("draft",))
    other = _expert(tmp_path, "other", skills=("selected",))
    builtin, states = _registries(tmp_path, [research, writer, other])
    header = _header(
        "session",
        title="Keep this title",
        expert_id=expert_id,
        expert_activation_source="user" if expert_id else None,
        selected_skill_ids=["research:base"],
    )
    before = header.model_dump()
    store = await _selection_store(header)

    updated = await replace_session_skill_selection(
        header.id,
        ["other:selected", "research:selected", "other:selected"],
        session_store=store,
        expert_registry=builtin,
        capability_policy_registry=states,
    )

    expected = ["other:selected", "research:selected"]
    persisted = await store.load(header.id)
    assert persisted is not None
    assert persisted.header == updated
    assert updated.selected_skill_ids == expected
    assert {
        key: value for key, value in updated.model_dump().items() if key != "selected_skill_ids"
    } == {key: value for key, value in before.items() if key != "selected_skill_ids"}
    header_events = [
        event
        for event in await store.get_events(header.id)
        if event.type == SessionEventType.REQUEST_HEADER
    ]
    assert len(header_events) == 1
    assert header_events[0].data["header"] == {"selected_skill_ids": expected}

    view = build_session_scope(persisted.header, _catalog(builtin, states))
    expected_owners = {"other", "research"} | ({expert_id} if expert_id else set())
    assert view.tool_owners == frozenset(expected_owners)


@pytest.mark.parametrize(
    ("skill_id", "expected_code"),
    [
        ("other:disabled", ExpertErrorCode.MODULE_DISABLED),
        ("missing:nope", ExpertErrorCode.EXPERT_NOT_FOUND),
    ],
)
async def test_skill_selection_rejects_disabled_and_unknown_skills_without_writing(
    tmp_path: Path, skill_id: str, expected_code: ExpertErrorCode
) -> None:
    research = _expert(tmp_path, "research", skills=("base",))
    other = _expert(tmp_path, "other", skills=("disabled",))
    builtin, states = _registries(
        tmp_path,
        [research, other],
        state_yaml=("version: 1\noverrides:\n  skill:\n    other:disabled: false\n"),
    )
    header = _header(
        "session",
        title="Keep this title",
        selected_skill_ids=["research:base"],
    )
    store = await _selection_store(header)

    with pytest.raises(ExpertError) as raised:
        await replace_session_skill_selection(
            header.id,
            [skill_id],
            session_store=store,
            expert_registry=builtin,
            capability_policy_registry=states,
        )

    assert raised.value.code == expected_code
    persisted = await store.load(header.id)
    assert persisted is not None
    assert persisted.header == header
    assert [
        event
        for event in await store.get_events(header.id)
        if event.type == SessionEventType.REQUEST_HEADER
    ] == []


async def test_skill_selection_rejects_more_than_twenty_without_replacing_existing(
    tmp_path: Path,
) -> None:
    skills = tuple(f"skill-{index}" for index in range(21))
    research = _expert(tmp_path, "research", skills=skills)
    builtin, states = _registries(tmp_path, [research])
    header = _header("session", selected_skill_ids=["research:skill-0"])
    store = await _selection_store(header)

    with pytest.raises(ExpertError) as raised:
        await replace_session_skill_selection(
            header.id,
            [f"research:{skill}" for skill in skills],
            session_store=store,
            expert_registry=builtin,
            capability_policy_registry=states,
        )

    assert raised.value.code == ExpertErrorCode.SKILL_SELECTION_LIMIT
    persisted = await store.load(header.id)
    assert persisted is not None
    assert persisted.header.selected_skill_ids == ["research:skill-0"]
    assert [
        event
        for event in await store.get_events(header.id)
        if event.type == SessionEventType.REQUEST_HEADER
    ] == []


async def test_cancelling_skill_selection_removes_it_from_the_next_view(
    tmp_path: Path,
) -> None:
    research = _expert(tmp_path, "research", skills=("base",))
    other = _expert(tmp_path, "other", skills=("selected",))
    builtin, states = _registries(tmp_path, [research, other])
    header = _header(
        "session",
        title="Keep this title",
        selected_skill_ids=["other:selected"],
    )
    store = await _selection_store(header)
    assert "other" in build_session_scope(header, _catalog(builtin, states)).tool_owners

    updated = await replace_session_skill_selection(
        header.id,
        [],
        session_store=store,
        expert_registry=builtin,
        capability_policy_registry=states,
    )

    assert updated.selected_skill_ids == []
    assert updated.title == "Keep this title"
    persisted = await store.load(header.id)
    assert persisted is not None
    assert persisted.header.selected_skill_ids == []
    assert "other" not in build_session_scope(persisted.header, _catalog(builtin, states)).tool_owners
    header_events = [
        event
        for event in await store.get_events(header.id)
        if event.type == SessionEventType.REQUEST_HEADER
    ]
    assert len(header_events) == 1
    assert header_events[0].data["header"] == {"selected_skill_ids": []}
