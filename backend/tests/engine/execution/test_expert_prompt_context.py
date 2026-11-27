from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from prismclaw.engine.execution.capabilities.catalog import RuntimeCatalog
from prismclaw.engine.execution.loop.deps import AgentRunnerDeps, ExpertBundle
from prismclaw.engine.execution.query._static_context_types import (
    StaticContextResult,
    StaticTurnShellCache,
)
from prismclaw.engine.execution.query.context_preparation import StaticContextBuilder
from prismclaw.engine.execution.request.model_fallback import ModelSpec, register_model_spec
from prismclaw.engine.experts.codes import ExpertErrorCode
from prismclaw.engine.experts.prompt_slot import ROLE_OVERRIDE
from prismclaw.engine.experts.loading.load_result import ExpertPackage
from prismclaw.engine.experts.manifest import ExpertManifest
from prismclaw.engine.experts.registry.expert_registry import ExpertRegistry
from prismclaw.engine.experts.scope import build_session_scope
from prismclaw.engine.subagent.definition.manifest import AgentManifest
from prismclaw.engine.toolkit.registry.registry import ToolRegistry
from prismclaw.engine.toolkit.registry.tool_runtime import Tool
from prismclaw.infra.config.capability_policy import CapabilityPolicyRegistry
from prismclaw.infra.environment_catalog import EnvironmentCatalog
from prismclaw.schema.agent.session import SessionHeader
from prismclaw.schema.agent.types import RunRequest
from prismclaw.schema.session.types import SessionTree
from prismclaw.schema.shared.utils import estimate_tokens


class _Tool:
    name = "read"
    description = "Read files"
    parameters = {"type": "object", "properties": {}}
    tool_class = "read"
    effect_class = "pure_read"
    expert_name = None


def _expert(root: Path, name: str, source: str, *, persona: str = "") -> ExpertPackage:
    root.mkdir(parents=True, exist_ok=True)
    manifest = ExpertManifest.model_validate(
        {
            "name": name,
            **(
                {"expertType": "agent", "agentName": f"{name}-lead"}
                if source == "experts"
                else {}
            ),
        }
    )
    graph_agents = (
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
        agents=graph_agents,
    )


def _write_agent(root: Path, agent_name: str, body: str) -> Path:
    path = root / "agents" / f"{agent_name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {agent_name}\ndescription: Expert agent\n---\n{body}",
        encoding="utf-8",
    )
    return path


def _deps(
    tmp_path: Path, expert: ExpertPackage | list[ExpertPackage]
) -> SimpleNamespace:
    experts = expert if isinstance(expert, list) else [expert]
    builtin = ExpertRegistry(experts)
    state_path = tmp_path / "capability_policy.yaml"
    state_path.write_text("version: 1\n", encoding="utf-8")
    states = CapabilityPolicyRegistry(state_path, catalog=builtin)
    registry = ToolRegistry()
    registry.register(cast(Tool, _Tool()))
    budget = SimpleNamespace(
        compute_history_budget=lambda **kwargs: 8000,
        window_tokens=100_000,
        system_zone_tokens=50_000,
        dynamic_zone_tokens=10_000,
    )

    async def get_bootstrap(_session_id: str) -> None:
        return None

    return SimpleNamespace(
        tools=registry,
        context_bootstrap_provider=SimpleNamespace(get_bootstrap=get_bootstrap),
        static_shell_cache=StaticTurnShellCache(),
        expert=ExpertBundle(
            environment_catalog=EnvironmentCatalog(
                expert_registry=builtin,
                capability_policy_registry=states,
            ),
        ),
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


def _request() -> SimpleNamespace:
    return SimpleNamespace(
        session_id="session-1",
        workspace_id="workspace-1",
        agent_id="agent-1",
        task_spec=None,
    )


def _tree() -> SessionTree:
    return SessionTree(
        header=SessionHeader(
            id="session-1",
            workspace_id="workspace-1",
            agent_id="agent-1",
            expert_id="writer",
            expert_activation_source="user",
        )
    )


async def _build(
    deps: SimpleNamespace,
    tree: SessionTree,
    *,
    model: str = "expert-test-model",
) -> StaticContextResult:
    return await StaticContextBuilder().build(
        request=cast(RunRequest, _request()),
        deps=cast(AgentRunnerDeps, deps),
        tree=tree,
        tool_workspace_path="/tmp/workspace",
        effective_model=model,
        visible_tool_names={"read"},
        session_scope=build_session_scope(
            tree.header, deps.expert.environment_catalog.snapshot()
        ),
    )


@pytest.fixture
def prompt_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "prismclaw.engine.execution.query.context_preparation.build_identity_text",
        lambda: "Base identity",
    )
    monkeypatch.setattr(
        "prismclaw.engine.execution.query.context_preparation.build_preferences_text",
        lambda: "Base preferences",
    )
    monkeypatch.setattr(
        "prismclaw.engine.execution.query.context_preparation._require_capability_catalog",
        lambda deps: RuntimeCatalog(tool_registry=deps.tools),
    )


@pytest.mark.asyncio
async def test_static_prompt_reads_agent_file_and_invalidates_persona_cache(
    tmp_path: Path,
    prompt_profiles: None,
) -> None:
    root = tmp_path / "expert"
    first_body = "First expert body."
    _write_agent(root, "writer-lead", first_body)
    deps = _deps(tmp_path, _expert(root, "writer", "experts", persona=first_body))
    tree = _tree()

    first = await _build(deps, tree)
    _write_agent(root, "writer-lead", "Second expert body.")
    second = await _build(deps, tree)

    assert first.frozen_result.text.count(ROLE_OVERRIDE) == 1
    assert first_body in first.frozen_result.text
    assert first.frozen_result.text.index("<role>") < first.frozen_result.text.index(
        ROLE_OVERRIDE
    ) < first.frozen_result.text.index("<principles>")
    assert "Second expert body." not in first.frozen_result.text
    assert "Second expert body." in second.frozen_result.text
    assert first_body not in second.frozen_result.text
    assert len(deps.static_shell_cache.entries) == 2


@pytest.mark.asyncio
async def test_persona_is_stable_across_turns_and_replaced_on_expert_switch(
    tmp_path: Path,
    prompt_profiles: None,
) -> None:
    writer_root = tmp_path / "writer"
    reviewer_root = tmp_path / "reviewer"
    _write_agent(writer_root, "writer-lead", "Writer persona")
    _write_agent(reviewer_root, "reviewer-lead", "Reviewer persona")
    deps = _deps(
        tmp_path,
        [
            _expert(writer_root, "writer", "experts", persona="Writer persona"),
            _expert(reviewer_root, "reviewer", "experts", persona="Reviewer persona"),
        ],
    )
    tree = _tree()

    first = await _build(deps, tree)
    second = await _build(deps, tree)
    tree.header.expert_id = "reviewer"
    switched = await _build(deps, tree)

    assert first.prompt_inputs.expert_persona == second.prompt_inputs.expert_persona
    assert first.frozen_result.text == second.frozen_result.text
    assert "Writer persona" in first.frozen_result.text
    assert "Reviewer persona" in switched.frozen_result.text
    assert "Writer persona" not in switched.frozen_result.text
    assert switched.frozen_result.text.count(ROLE_OVERRIDE) == 1


@pytest.mark.asyncio
async def test_team_persona_contains_only_the_lead_agent_body(
    tmp_path: Path,
    prompt_profiles: None,
) -> None:
    root = tmp_path / "clinical-team"
    team = _expert(root, "clinical-team", "experts", persona="Lead persona")
    team.manifest = ExpertManifest.model_validate(
        {
            "name": "clinical-team",
            "expertType": "team",
            "agentName": "clinical-team-lead",
            "teamInfo": {
                "leadAgent": "clinical-team-lead",
                "memberAgents": ["medication-safety"],
            },
        }
    )
    assert team.agents
    team.agents.append(
        AgentManifest(
            name="medication-safety",
            description="Team member",
            system_prompt="Member persona must not be injected",
        )
    )
    _write_agent(root, "clinical-team-lead", "Lead persona")
    _write_agent(root, "medication-safety", "Member persona must not be injected")
    deps = _deps(tmp_path, team)
    tree = _tree()
    tree.header.expert_id = "clinical-team"

    result = await _build(deps, tree)

    assert "Lead persona" in result.frozen_result.text
    assert "Member persona must not be injected" not in result.frozen_result.text
    assert result.frozen_result.text.count(ROLE_OVERRIDE) == 1


@pytest.mark.asyncio
async def test_session_without_expert_has_empty_persona(
    tmp_path: Path,
    prompt_profiles: None,
) -> None:
    root = tmp_path / "writer"
    _write_agent(root, "writer-lead", "Writer persona")
    deps = _deps(tmp_path, _expert(root, "writer", "experts", persona="Writer persona"))
    tree = _tree()
    tree.header.expert_id = None
    tree.header.expert_activation_source = None

    result = await _build(deps, tree)

    assert result.prompt_inputs.expert_persona == ""
    assert ROLE_OVERRIDE not in result.frozen_result.text
    assert "Writer persona" not in result.frozen_result.text
    assert "Base identity" in result.frozen_result.text


@pytest.mark.asyncio
async def test_malformed_agent_frontmatter_degrades_without_blocking(
    tmp_path: Path,
    prompt_profiles: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    root = tmp_path / "expert"
    agent_path = root / "agents" / "writer-lead.md"
    agent_path.parent.mkdir(parents=True)
    agent_path.write_text(
        "---\nname: [broken\n---\nMalformed persona must not be injected",
        encoding="utf-8",
    )
    deps = _deps(tmp_path, _expert(root, "writer", "experts", persona="Cached persona"))
    events: list[tuple[ExpertErrorCode, dict[str, object]]] = []
    monkeypatch.setattr(
        "prismclaw.engine.experts.prompt_slot.emit_event",
        lambda code, **payload: events.append((code, payload)),
    )

    with caplog.at_level(logging.WARNING):
        result = await _build(deps, _tree())

    assert result.prompt_inputs.expert_persona == ""
    assert "Malformed persona must not be injected" not in result.frozen_result.text
    assert "Base identity" in result.frozen_result.text
    assert [code for code, _payload in events] == [ExpertErrorCode.EXPERT_UNAVAILABLE]
    assert "EXPERT_UNAVAILABLE" in caplog.text


@pytest.mark.asyncio
async def test_agent_read_failure_emits_unavailable_and_preserves_base_prompt(
    tmp_path: Path,
    prompt_profiles: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    root = tmp_path / "expert"
    expert = _expert(root, "writer", "experts", persona="Cached persona")
    deps = _deps(tmp_path, expert)
    tree = _tree()
    before = tree.header.model_dump()
    events: list[tuple[ExpertErrorCode, dict[str, object]]] = []
    monkeypatch.setattr(
        "prismclaw.engine.experts.prompt_slot.emit_event",
        lambda code, **payload: events.append((code, payload)),
    )

    with caplog.at_level(logging.WARNING):
        result = await _build(deps, tree)

    assert result.prompt_inputs.expert_persona == ""
    assert ROLE_OVERRIDE not in result.frozen_result.text
    assert "Base identity" in result.frozen_result.text
    assert "<principles>" in result.frozen_result.text
    assert tree.header.model_dump() == before
    assert [code for code, _payload in events] == [ExpertErrorCode.EXPERT_UNAVAILABLE]
    assert "EXPERT_UNAVAILABLE" in caplog.text


@pytest.mark.asyncio
async def test_unloaded_expert_package_emits_unavailable_without_blocking(
    tmp_path: Path,
    prompt_profiles: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    other = _expert(tmp_path / "other", "other", "experts")
    deps = _deps(tmp_path, other)
    events: list[tuple[ExpertErrorCode, dict[str, object]]] = []
    monkeypatch.setattr(
        "prismclaw.engine.experts.prompt_slot.emit_event",
        lambda code, **payload: events.append((code, payload)),
    )

    with caplog.at_level(logging.WARNING):
        result = await _build(deps, _tree())

    assert result.prompt_inputs.expert_persona == ""
    assert "Base identity" in result.frozen_result.text
    assert [code for code, _payload in events] == [ExpertErrorCode.EXPERT_UNAVAILABLE]
    assert events[0][1]["expert_id"] == "writer"
    assert "EXPERT_UNAVAILABLE" in caplog.text


@pytest.mark.asyncio
async def test_overlong_persona_degrades_on_build_and_cache_hit(
    tmp_path: Path,
    prompt_profiles: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    model = "expert-small-window"
    register_model_spec(ModelSpec(model_id=model, context_window=10_000, max_output_tokens=100))
    root = tmp_path / "expert"
    body = "long-persona-" * 5000
    _write_agent(root, "writer-lead", body)
    deps = _deps(tmp_path, _expert(root, "writer", "experts", persona=body))
    tree = _tree()
    before = tree.header.model_dump()
    events: list[tuple[ExpertErrorCode, dict[str, object]]] = []
    monkeypatch.setattr(
        "prismclaw.engine.experts.prompt_slot.emit_event",
        lambda code, **payload: events.append((code, payload)),
    )

    with caplog.at_level(logging.WARNING):
        first = await _build(deps, tree, model=model)
        second = await _build(deps, tree, model=model)

    for result in (first, second):
        assert result.prompt_inputs.expert_persona == ""
        assert ROLE_OVERRIDE not in result.frozen_result.text
        assert body not in result.frozen_result.text
        assert "Base identity" in result.frozen_result.text
    assert tree.header.model_dump() == before
    assert [code for code, _payload in events] == [
        ExpertErrorCode.EXPERT_PROMPT_TOO_LONG,
        ExpertErrorCode.EXPERT_PROMPT_TOO_LONG,
    ]
    estimated_tokens = events[0][1]["estimated_tokens"]
    threshold = events[0][1]["threshold"]
    assert isinstance(estimated_tokens, int | float)
    assert isinstance(threshold, int | float)
    assert estimated_tokens >= threshold
    assert events[0][1]["context_window"] == 10_000
    assert caplog.text.count("EXPERT_PROMPT_TOO_LONG") == 2


def test_persona_at_exactly_half_window_degrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persona = "persona at the boundary"
    context_window = estimate_tokens(persona) * 2
    model = "expert-exact-half-window"
    register_model_spec(
        ModelSpec(model_id=model, context_window=context_window, max_output_tokens=100)
    )
    session = _tree().header
    events: list[tuple[ExpertErrorCode, dict[str, object]]] = []
    monkeypatch.setattr(
        "prismclaw.engine.experts.prompt_slot.emit_event",
        lambda code, **payload: events.append((code, payload)),
    )

    result = StaticContextBuilder._apply_expert_persona_budget(
        persona,
        fixed_prompt_tokens=0,
        effective_model=model,
        session=session,
    )

    assert result == ""
    assert [code for code, _payload in events] == [ExpertErrorCode.EXPERT_PROMPT_TOO_LONG]
    assert events[0][1]["estimated_tokens"] == events[0][1]["threshold"]
