from __future__ import annotations

import asyncio
import json
import shutil
import socket
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from tests.engine.session_store import FakeRedisSessionStore

from prismclaw.engine.execution.capabilities.catalog import (
    AgentCatalogEntry,
    RuntimeCatalog,
)
from prismclaw.engine.execution.capabilities.discovery_runtime import ToolDiscoveryRuntime
from prismclaw.engine.execution.loop.deps import AgentRunnerDeps
from prismclaw.engine.execution.loop.preparer import RunPreparer
from prismclaw.engine.execution.loop.runtime import LoopRuntime
from prismclaw.engine.execution.tools.expert_mode import (
    DEFERRED_TEAM_TOOLS,
    EXPERT_MODE_TOOLS,
    TEAM_LEAD_TOOLS,
)
from prismclaw.engine.experts.codes import ExpertErrorCode
from prismclaw.engine.experts.scope import ActiveScope, ToolSurface, tool_surface_for_expert
from prismclaw.engine.experts.loading.load_result import ExpertPackage
from prismclaw.engine.experts.manifest import ExpertManifest
from prismclaw.engine.experts.registry.expert_registry import ExpertRegistry
from prismclaw.engine.subagent.control_tool import SubagentControlTool
from prismclaw.engine.subagent.core._loop_mixin import LoopMixin
from prismclaw.engine.subagent.core._result_types import AgentRunResult
from prismclaw.engine.subagent.core.executor import SubAgentExecutor
from prismclaw.engine.subagent.definition.manifest import AgentManifest
from prismclaw.engine.subagent.definition.registry import SubAgentRegistry
from prismclaw.engine.subagent.runtime.collaboration import (
    SubagentCollaborationService,
    SubagentRunLaunch,
    build_definition_snapshot,
)
from prismclaw.engine.subagent.runtime.records import AgentSessionRecord
from prismclaw.engine.subagent.team_guard import TeamGuardError, guard_dispatch
from prismclaw.engine.subagent.team_registry import (
    TeamErrorCode,
    TeamRegistration,
    get_team_registry,
)
from prismclaw.engine.subagent.team_tools import TeamCreateTool, TeamDeleteTool
from prismclaw.engine.subagent.tool import SubAgentTool
from prismclaw.engine.toolkit.registry.discovery import ToolSearchTool
from prismclaw.engine.toolkit.registry.registry import ToolRegistry
from prismclaw.engine.toolkit.registry.tool_runtime import Tool, ToolContext, text_result
from prismclaw.infra.config.capability_policy import CapabilityPolicyRegistry, CapabilityPolicySnapshot
from prismclaw.infra.environment_catalog import EnvironmentCatalog
from prismclaw.persistence.session.redis import RedisSessionStore
from prismclaw.schema.agent.types import RunRequest
from prismclaw.schema.config.config import SubAgentConfig
from prismclaw.schema.content.content_types import TextBlock, ToolResult
from prismclaw.schema.session.types import SessionHeader, SessionTree

_TEAM = "alpha-team"
_LEAD = "alpha-team-team-lead"
_MEMBER = "researcher"


def _surface() -> ToolSurface:
    active = EXPERT_MODE_TOOLS | TEAM_LEAD_TOOLS
    return ToolSurface(
        active=active,
        deferred=active | DEFERRED_TEAM_TOOLS,
        member=EXPERT_MODE_TOOLS,
        restricted=True,
    )


def _entry(
    name: str,
    *,
    package: str = _TEAM,
    persona: str | None = None,
) -> AgentCatalogEntry:
    manifest = AgentManifest(
        name=name,
        description=f"{name} description",
        system_prompt=persona or f"{name} persona",
    )
    return AgentCatalogEntry(
        name=name,
        description=manifest.description,
        manifest=manifest,
        expert_name=package,
    )


class _Catalog:
    def __init__(
        self,
        entries: list[AgentCatalogEntry],
        *,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._entries = entries
        self.tool_registry = tool_registry

    def get_agent_entry(self, name: str) -> AgentCatalogEntry | None:
        return next((entry for entry in self._entries if entry.name == name), None)

    def list_agent_entries(self) -> list[AgentCatalogEntry]:
        return list(self._entries)

    def count_agent_entries(self) -> int:
        return len(self._entries)


def _team_package(
    tmp_path: Path,
    *,
    name: str = _TEAM,
    lead: str = _LEAD,
    members: tuple[str, ...] = (_MEMBER,),
) -> ExpertPackage:
    root = tmp_path / name
    (root / ".plugin").mkdir(parents=True)
    (root / "agents").mkdir()
    display_name = {"en": "Alpha Team", "zh": "阿尔法团队"}
    profession = display_name
    raw = {
        "name": name,
        "version": "1.0.0",
        "description": "A team used to verify run-scoped orchestration.",
        "expertType": "team",
        "agentName": lead,
        "agents": [lead, *members],
        "skills": [],
        "displayName": display_name,
        "profession": profession,
        "displayDescription": "团" * 40,
        "avatar": "avatar.png",
        "categoryId": "08-Test",
        "categoryName": "测试分组",
        "defaultInitPrompt": {"en": "Start", "zh": "开始"},
        "tags": ["team", "test", "orchestration"],
        "quickPrompts": [
            {"en": "Start", "zh": "开始"},
            {"en": "Second prompt", "zh": "第二个提示"},
            {"en": "Third prompt", "zh": "第三个提示"},
        ],
        "plugin": name,
        "teamInfo": {"leadAgent": lead, "memberAgents": list(members)},
        "members": [
            {
                "id": agent_name,
                "name": {"en": agent_name, "zh": agent_name},
                "displayName": {"en": agent_name, "zh": agent_name},
                "profession": {"en": agent_name, "zh": agent_name},
                "avatar": "avatar.png",
                "role": "lead" if agent_name == lead else "member",
            }
            for agent_name in (lead, *members)
        ],
    }
    (root / ".plugin" / "plugin.json").write_text(
        json.dumps(raw, ensure_ascii=False), encoding="utf-8"
    )
    for agent_name in (lead, *members):
        (root / "agents" / f"{agent_name}.md").write_text(
            f"---\nname: {agent_name}\ndescription: {agent_name} description\n---\n"
            f"{agent_name} persona\n",
            encoding="utf-8",
        )
    return ExpertPackage(
        name=name,
        source="experts",
        path=str(root),
        root_path=root,
        manifest=ExpertManifest.model_validate(raw),
    )


def _team_scope(*, member: bool = False) -> ActiveScope:
    """团队会话作用域：expert 为团队包，工具面为团队 lead 面。"""
    expert = ExpertPackage(
        name=_TEAM,
        source="experts",
        path=f"/tmp/{_TEAM}",
        manifest=ExpertManifest.model_validate(
            {"name": _TEAM, "expertType": "team", "agentName": _LEAD}
        ),
    )
    return ActiveScope(
        tool_surface=_surface(),
        tool_owners=frozenset({_TEAM}),
        member=member,
        expert=expert,
        persona_source=None,
    )


def _as_member(context: ToolContext) -> ToolContext:
    assert context.session_scope is not None
    context.session_scope = context.session_scope.with_member()
    return context


def _context(
    tmp_path: Path,
    *,
    session_id: str = "session-a",
    run_id: str = "run-a",
    team: bool = True,
    member: bool = False,
    session_store: FakeRedisSessionStore | RedisSessionStore | None = None,
) -> ToolContext:
    return ToolContext(
        workspace_id="workspace",
        workspace_path=tmp_path,
        session_id=session_id,
        request_id=run_id,
        session_store=session_store or FakeRedisSessionStore(),
        session_scope=_team_scope(member=member) if team else None,
    )


def _metadata(result: ToolResult) -> dict[str, Any]:
    metadata = result.metadata
    assert isinstance(metadata, dict)
    return metadata


def _text(result: ToolResult) -> str:
    block = result.content[0]
    assert isinstance(block, TextBlock)
    text = block.text
    assert isinstance(text, str)
    return text


def _registration(*, members: tuple[str, ...] = (_MEMBER,)) -> TeamRegistration:
    return TeamRegistration(
        team_name=_TEAM,
        lead=_LEAD,
        members=members,
        expert_package=_TEAM,
    )


class _RecordingExecutor:
    _default_model = "test-model"

    def __init__(self) -> None:
        self.calls: list[tuple[AgentManifest, ToolContext]] = []

    async def run(
        self,
        manifest: AgentManifest,
        _task: str,
        context: ToolContext,
        *,
        call_id: str = "",
        **_kwargs: object,
    ) -> AgentRunResult:
        self.calls.append((manifest, context))
        return AgentRunResult(
            tool_result=text_result(call_id, "done"),
            outcome="success",
            turns=1,
            tokens_input=0,
            tokens_output=0,
            duration_seconds=0.0,
            model_used=self._default_model,
        )


def _subagent_tool(catalog: _Catalog, executor: _RecordingExecutor) -> SubAgentTool:
    return SubAgentTool(
        executor=cast(SubAgentExecutor, executor),
        registry=cast(SubAgentRegistry, SimpleNamespace()),
        settings=SubAgentConfig(),
        capability_catalog=cast(RuntimeCatalog, catalog),
    )


def _spawn_args(*agent_names: str) -> dict[str, Any]:
    return {
        "_call_id": "spawn-call",
        "agents": [
            {"agent_name": agent_name, "task": f"task for {agent_name}"}
            for agent_name in agent_names
        ],
    }


async def test_team_create_is_idempotent_and_delete_only_unregisters(
    tmp_path: Path,
) -> None:
    expert = _team_package(tmp_path)
    catalog = _Catalog([_entry(_LEAD), _entry(_MEMBER)])
    builtin = SimpleNamespace(list_experts=lambda: [expert])
    create = TeamCreateTool(
        expert_registry=cast(ExpertRegistry, builtin),
        capability_catalog=cast(RuntimeCatalog, catalog),
    )
    delete = TeamDeleteTool()
    context = _context(tmp_path)

    created = await create.execute({"_call_id": "create", "name": _TEAM}, context)
    existing = await create.execute({"_call_id": "existing", "name": _TEAM}, context)

    assert created.metadata == {
        "status": "created",
        "created": True,
        **_registration().payload(),
    }
    assert _metadata(existing)["status"] == "existing"
    assert _metadata(existing)["created"] is False
    registry = get_team_registry(context)
    assert registry is not None
    assert await registry.get(_TEAM) == _registration()

    deleted = await delete.execute({"_call_id": "delete", "name": _TEAM}, context)
    assert deleted.metadata == {"status": "deleted", **_registration().payload()}
    assert await registry.get(_TEAM) is None
    repeated = await delete.execute({"_call_id": "delete-again", "name": _TEAM}, context)
    assert repeated.is_error
    assert _metadata(repeated)["code"] == TeamErrorCode.TEAM_NOT_CREATED


async def test_same_named_team_is_isolated_across_sessions_and_concurrent_runs(
    tmp_path: Path,
) -> None:
    expert = _team_package(tmp_path)
    catalog = _Catalog([_entry(_LEAD), _entry(_MEMBER)])
    builtin = SimpleNamespace(list_experts=lambda: [expert])
    create = TeamCreateTool(
        expert_registry=cast(ExpertRegistry, builtin),
        capability_catalog=cast(RuntimeCatalog, catalog),
    )
    contexts = [
        _context(tmp_path, session_id="session-a", run_id="run-1"),
        _context(tmp_path, session_id="session-b", run_id="run-1"),
        _context(tmp_path, session_id="session-a", run_id="run-2"),
    ]

    results = await asyncio.gather(
        *(create.execute({"name": _TEAM}, context) for context in contexts)
    )

    assert all(_metadata(result)["created"] for result in results)
    registries = [get_team_registry(context) for context in contexts]
    assert all(registry is not None for registry in registries)
    assert registries[0] is not None
    assert registries[1] is not None
    assert registries[2] is not None
    assert len({id(registry) for registry in registries}) == 3

    await TeamDeleteTool().execute({"name": _TEAM}, contexts[0])
    assert await registries[0].get(_TEAM) is None
    assert await registries[1].get(_TEAM) == _registration()
    assert await registries[2].get(_TEAM) == _registration()


async def test_two_registry_instances_share_the_same_redis_hash(tmp_path: Path) -> None:
    store = FakeRedisSessionStore()
    context_a = _context(tmp_path, session_store=store)
    context_b = _context(tmp_path, session_store=store)
    registry_a = get_team_registry(context_a, create=True)
    registry_b = get_team_registry(context_b, create=True)
    assert registry_a is not None and registry_b is not None
    assert registry_a is not registry_b

    _, created = await registry_a.register(_registration())
    assert created is True
    assert await registry_b.get(_TEAM) == _registration()
    assert await registry_b.delete(_TEAM) == _registration()
    assert await registry_a.get(_TEAM) is None


async def test_sub_agent_distinguishes_redis_failure_from_an_uncreated_team(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = _Catalog([_entry(_LEAD), _entry(_MEMBER)])
    executor = _RecordingExecutor()
    tool = _subagent_tool(catalog, executor)
    context = _context(tmp_path)
    registry = get_team_registry(context, create=True)
    assert registry is not None
    await registry.register(_registration())

    async def fail_hget(*_args: object, **_kwargs: object) -> None:
        raise ConnectionError("redis disconnected")

    monkeypatch.setattr(context.session_store.redis_client, "hget", fail_hget)
    result = await tool.execute(_spawn_args(_MEMBER), context)

    assert result.is_error
    assert _metadata(result)["code"] == ExpertErrorCode.TEAM_REGISTRY_UNAVAILABLE
    assert _metadata(result)["code"] != TeamErrorCode.TEAM_NOT_CREATED
    assert executor.calls == []


async def test_preparer_registers_an_active_team_for_each_new_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expert = _team_package(tmp_path)
    entries = {_LEAD: _entry(_LEAD), _MEMBER: _entry(_MEMBER)}
    catalog = RuntimeCatalog()
    monkeypatch.setattr(
        RuntimeCatalog,
        "get_agent_entry",
        lambda _self, name: entries.get(name),
    )
    store = FakeRedisSessionStore()
    await store.save_header(
        SessionTree(
            header=SessionHeader(
                id="session-a",
                workspace_id="workspace",
                agent_id="main",
                expert_id=_TEAM,
            )
        )
    )
    deps = AgentRunnerDeps(
        llm=SimpleNamespace(default_model="stub-model"),
        tools=ToolRegistry(),
        session_store=store,
        capability_catalog=catalog,
    )
    deps.expert.environment_catalog = EnvironmentCatalog(
        expert_registry=ExpertRegistry([expert]),
        capability_policy_registry=SimpleNamespace(
            snapshot=lambda: CapabilityPolicySnapshot(version=1, states={}),
            reload_if_changed=lambda: None,
        ),
    )
    monkeypatch.setattr(
        deps.expert,
        "build_session_scope",
        lambda _header: ActiveScope(
            tool_surface=tool_surface_for_expert(expert),
            tool_owners=frozenset(),
            member=False,
            expert=expert,
            persona_source=None,
        ),
    )
    request = RunRequest(
        session_id="session-a",
        workspace_id="workspace",
        agent_id="main",
        user_message="continue",
        request_id="run-next",
        sandbox_policy=SimpleNamespace(tier="standard"),
    )

    prepared = await RunPreparer(request, deps, tool_workspace_path=tmp_path).prepare()

    assert isinstance(prepared, LoopRuntime)
    registration = await prepared.tool_ctx.team_registry.get(_TEAM)
    assert registration is not None
    assert registration.team_name == _TEAM
    assert prepared.tool_ctx.request_id == "run-next"


@pytest.mark.integration
async def test_real_redis_team_registry_cross_instance_and_disconnect(
    tmp_path: Path,
) -> None:
    redis_server = shutil.which("redis-server")
    if redis_server is None:
        pytest.skip("redis-server is required for the real Redis integration test")

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process = await asyncio.create_subprocess_exec(
        redis_server,
        "--bind",
        "127.0.0.1",
        "--port",
        str(port),
        "--save",
        "",
        "--appendonly",
        "no",
        "--dir",
        str(tmp_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    from redis.asyncio import Redis
    from redis.exceptions import ConnectionError as RedisConnectionError

    client_a = Redis(
        host="127.0.0.1",
        port=port,
        decode_responses=True,
        socket_connect_timeout=0.2,
        socket_timeout=0.2,
    )
    client_b = Redis(
        host="127.0.0.1",
        port=port,
        decode_responses=True,
        socket_connect_timeout=0.2,
        socket_timeout=0.2,
    )
    prefix = f"test:team-registry:{tmp_path.name}:"
    store_a = RedisSessionStore(
        client_a,
        SimpleNamespace(),
        ttl_seconds=90,
        key_prefix=prefix,
    )
    store_b = RedisSessionStore(
        client_b,
        SimpleNamespace(),
        ttl_seconds=90,
        key_prefix=prefix,
    )
    key = f"{prefix}session:{{session-real}}:run:run-real:teams"

    try:
        for _ in range(100):
            try:
                await client_a.ping()
                break
            except RedisConnectionError:
                if process.returncode is not None:
                    pytest.fail("temporary redis-server exited before accepting connections")
                await asyncio.sleep(0.02)
        else:
            pytest.fail("temporary redis-server did not become ready")

        expert = _team_package(tmp_path)
        catalog = _Catalog([_entry(_LEAD), _entry(_MEMBER)])
        builtin = SimpleNamespace(list_experts=lambda: [expert])
        create_a = TeamCreateTool(
            expert_registry=cast(ExpertRegistry, builtin),
            capability_catalog=cast(RuntimeCatalog, catalog),
        )
        create_b = TeamCreateTool(
            expert_registry=cast(ExpertRegistry, builtin),
            capability_catalog=cast(RuntimeCatalog, catalog),
        )
        delete_a = TeamDeleteTool()
        delete_b = TeamDeleteTool()
        context_a = _context(
            tmp_path,
            session_id="session-real",
            run_id="run-real",
            session_store=store_a,
        )
        context_b = _context(
            tmp_path,
            session_id="session-real",
            run_id="run-real",
            session_store=store_b,
        )
        registry_a = get_team_registry(context_a, create=True)
        registry_b = get_team_registry(context_b, create=True)
        assert registry_a is not None and registry_b is not None
        assert registry_a is not registry_b

        created = await create_a.execute({"name": _TEAM}, context_a)
        assert _metadata(created)["created"] is True
        existing = await create_b.execute({"name": _TEAM}, context_b)
        assert _metadata(existing)["created"] is False
        raw = await client_a.hget(key, _TEAM)
        assert raw is not None
        assert json.loads(raw)["version"] == 1
        assert 0 < await client_a.ttl(key) <= 90

        executor_a = _RecordingExecutor()
        executor_b = _RecordingExecutor()
        tool_a = _subagent_tool(catalog, executor_a)
        tool_b = _subagent_tool(catalog, executor_b)
        dispatched = await tool_b.execute(_spawn_args(_MEMBER), context_b)
        assert not dispatched.is_error
        assert len(executor_b.calls) == 1

        deleted = await delete_b.execute({"name": _TEAM}, context_b)
        assert _metadata(deleted)["status"] == "deleted"
        invalidated = await tool_a.execute(_spawn_args(_MEMBER), context_a)
        assert _metadata(invalidated)["code"] == TeamErrorCode.TEAM_NOT_CREATED
        assert executor_a.calls == []

        recreated = await create_a.execute({"name": _TEAM}, context_a)
        assert _metadata(recreated)["created"] is True
        await client_a.delete(key)
        await client_b.aclose()
        process.terminate()
        await process.wait()

        disconnected = await tool_a.execute(_spawn_args(_MEMBER), context_a)
        assert _metadata(disconnected)["code"] == ExpertErrorCode.TEAM_REGISTRY_UNAVAILABLE
        delete_failure = await delete_a.execute({"name": _TEAM}, context_a)
        assert _metadata(delete_failure)["code"] == ExpertErrorCode.TEAM_REGISTRY_UNAVAILABLE
        create_failure = await create_a.execute({"name": _TEAM}, context_a)
        assert _metadata(create_failure)["code"] == ExpertErrorCode.TEAM_REGISTRY_UNAVAILABLE
        assert executor_a.calls == []
    finally:
        await client_a.aclose()
        await client_b.aclose()
        if process.returncode is None:
            process.terminate()
            await process.wait()


async def test_team_create_rejects_foreign_members_without_partial_registration(
    tmp_path: Path,
) -> None:
    expert = _team_package(tmp_path)
    member = _entry(_MEMBER, package="other-team")
    catalog = _Catalog([_entry(_LEAD), member])
    builtin = SimpleNamespace(list_experts=lambda: [expert])
    context = _context(tmp_path)

    result = await TeamCreateTool(
        expert_registry=cast(ExpertRegistry, builtin),
        capability_catalog=cast(RuntimeCatalog, catalog),
    ).execute({"name": _TEAM}, context)

    assert result.is_error
    assert _metadata(result)["code"] == TeamErrorCode.TEAM_MEMBER_NOT_IN_PACKAGE
    registry = get_team_registry(context)
    assert registry is not None and await registry.get(_TEAM) is None


async def test_team_guard_enforces_exact_membership_and_caller_rules() -> None:
    registry = get_team_registry(
        ToolContext(
            workspace_id="workspace",
            workspace_path=Path("/tmp"),
            session_id="session",
            request_id="run",
            session_store=FakeRedisSessionStore(),
        ),
        create=True,
    )
    assert registry is not None
    await registry.register(_registration())
    member = _entry(_MEMBER)
    cases = [
        ("researcher", _MEMBER, member, False, TeamErrorCode.TEAM_SPAWN_BY_MEMBER),
        (_LEAD, _LEAD, _entry(_LEAD), False, TeamErrorCode.TEAM_LEAD_SELF_SPAWN),
        (_LEAD, "Researcher", None, False, TeamErrorCode.TEAM_MEMBER_UNKNOWN),
        (
            _LEAD,
            _MEMBER,
            _entry(_MEMBER, package="other-team"),
            False,
            TeamErrorCode.TEAM_MEMBER_NOT_IN_PACKAGE,
        ),
        (_LEAD, _MEMBER, member, True, TeamErrorCode.TEAM_SPAWN_BY_MEMBER),
    ]
    for caller, agent_name, entry, is_member, expected in cases:
        with pytest.raises(TeamGuardError) as exc_info:
            await guard_dispatch(
                caller,
                agent_name,
                _TEAM,
                registry=registry,
                lead_agent=_LEAD,
                entry=entry,
                is_member=is_member,
            )
        assert exc_info.value.code == expected
        assert set(exc_info.value.metadata) == {
            "code",
            "team_name",
            "agent_name",
            "caller",
            "reason",
        }

    assert (
        await guard_dispatch(
            _LEAD,
            _MEMBER,
            _TEAM,
            registry=registry,
            lead_agent=_LEAD,
            entry=member,
        )
        == _registration()
    )
    await registry.delete(_TEAM)
    with pytest.raises(TeamGuardError) as exc_info:
        await guard_dispatch(
            _LEAD,
            _MEMBER,
            _TEAM,
            registry=registry,
            lead_agent=_LEAD,
            entry=member,
        )
    assert exc_info.value.code == TeamErrorCode.TEAM_NOT_CREATED


async def test_sub_agent_rejects_a_whole_invalid_team_batch_before_executor(
    tmp_path: Path,
) -> None:
    catalog = _Catalog(
        [
            _entry(_LEAD),
            _entry(_MEMBER),
            _entry("outsider", package="other-team"),
        ]
    )
    executor = _RecordingExecutor()
    tool = _subagent_tool(catalog, executor)
    context = _context(tmp_path)

    unregistered = await tool.execute(_spawn_args(_MEMBER), context)
    assert _metadata(unregistered)["code"] == TeamErrorCode.TEAM_NOT_CREATED

    registry = get_team_registry(context, create=True)
    assert registry is not None
    await registry.register(_registration())
    foreign = await tool.execute(_spawn_args("outsider"), context)
    assert _metadata(foreign)["code"] == TeamErrorCode.TEAM_MEMBER_NOT_IN_PACKAGE
    self_spawn = await tool.execute(_spawn_args(_LEAD), context)
    assert _metadata(self_spawn)["code"] == TeamErrorCode.TEAM_LEAD_SELF_SPAWN
    batch = await tool.execute(_spawn_args("outsider", "missing"), context)
    assert _metadata(batch)["code"] == TeamErrorCode.TEAM_MEMBER_NOT_IN_PACKAGE
    assert [error["code"] for error in _metadata(batch)["errors"]] == [
        TeamErrorCode.TEAM_MEMBER_NOT_IN_PACKAGE,
        TeamErrorCode.TEAM_MEMBER_UNKNOWN,
    ]
    assert [error["agent_name"] for error in _metadata(batch)["errors"]] == [
        "outsider",
        "missing",
    ]
    assert executor.calls == []

    await registry.delete(_TEAM)
    invalidated = await tool.execute(_spawn_args(_MEMBER), context)
    assert _metadata(invalidated)["code"] == TeamErrorCode.TEAM_NOT_CREATED
    assert executor.calls == []


async def test_team_member_cannot_dispatch_even_at_depth_limit(tmp_path: Path) -> None:
    catalog = _Catalog([_entry(_LEAD), _entry(_MEMBER)])
    executor = _RecordingExecutor()
    tool = _subagent_tool(catalog, executor)
    context = _context(tmp_path, member=True)
    context.subagent_name = _MEMBER
    context.subagent_depth = 1
    registry = get_team_registry(context, create=True)
    assert registry is not None
    await registry.register(_registration())

    result = await tool.execute(_spawn_args(_MEMBER), context)

    assert result.is_error
    assert _metadata(result)["code"] == TeamErrorCode.TEAM_SPAWN_BY_MEMBER
    assert executor.calls == []


async def test_team_lead_dispatch_uses_member_manifest_and_spawn_scoped_identity(
    tmp_path: Path,
) -> None:
    member = _entry(_MEMBER, persona="CURRENT PACKAGE MEMBER PERSONA")
    catalog = _Catalog([_entry(_LEAD), member])
    executor = _RecordingExecutor()
    tool = _subagent_tool(catalog, executor)
    context = _context(tmp_path)
    registry = get_team_registry(context, create=True)
    assert registry is not None
    await registry.register(_registration())

    result = await tool.execute(_spawn_args(_MEMBER), context)

    assert not result.is_error
    assert len(executor.calls) == 1
    manifest, child = executor.calls[0]
    assert manifest is member.manifest
    assert manifest.system_prompt == "CURRENT PACKAGE MEMBER PERSONA"
    assert child.session_scope is not None
    assert child.session_scope.member is True
    assert child.session_scope.team_name == _TEAM
    assert child.session_scope.tool_surface == context.session_scope.tool_surface
    assert child.team_registry is registry
    assert context.session_scope is not None and context.session_scope.member is False
    assert get_team_registry(context) is registry


async def test_non_team_dispatch_keeps_existing_behavior(tmp_path: Path) -> None:
    entry = _entry("standalone", package="another-package")
    catalog = _Catalog([entry])
    executor = _RecordingExecutor()
    tool = _subagent_tool(catalog, executor)
    context = _context(tmp_path, team=False)

    result = await tool.execute(_spawn_args("standalone"), context)

    assert not result.is_error
    manifest, child = executor.calls[0]
    assert manifest is entry.manifest
    assert child.session_scope is None


class _DummyTool:
    description = "dummy tool"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    side_effect = False
    tool_class = "read"
    effect_class = "pure_read"

    def __init__(
        self,
        name: str,
        *,
        visibility_scope: str = "all",
        deferred: bool = False,
        mcp: bool = False,
    ) -> None:
        self.name = name
        self.visibility_scope = visibility_scope
        self.deferred = deferred
        if mcp:
            self.server_config: dict[str, Any] = {}


def _member_registry() -> ToolRegistry:
    registry = ToolRegistry()
    forbidden = {
        "sub_agent": "main_only",
        "subagent_control": "main_only",
        "team_create": "main_only",
        "team_delete": "main_only",
        "memory": "all",
    }
    for name in sorted(EXPERT_MODE_TOOLS | forbidden.keys()):
        registry.register(
            cast(Tool, _DummyTool(name, visibility_scope=forbidden.get(name, "all")))
        )
    registry.register(cast(Tool, _DummyTool("server:lookup", mcp=True)))
    return registry


def test_plain_subagent_settles_scoped_surface_and_hides_lead_tools(tmp_path: Path) -> None:
    from prismclaw.engine.subagent.tool_surface import SUBAGENT_TOOL_SURFACE

    instance = object.__new__(LoopMixin)
    instance._tools = _member_registry()
    context = _context(tmp_path, team=False)
    context.subagent_depth = 1

    assert SUBAGENT_TOOL_SURFACE.restricted is True
    settled = instance._settle_tool_surface(sub_ctx=context)

    assert {"read", "write", "edit", "bash"} <= set(settled.callable_tool_names)
    assert not {"sub_agent", "subagent_control"} & set(settled.callable_tool_names)


def test_member_loop_settles_exact_member_surface_and_keeps_mcp(tmp_path: Path) -> None:
    instance = object.__new__(LoopMixin)
    instance._tools = _member_registry()
    context = _context(tmp_path, member=True)
    context.subagent_depth = 1

    settled = instance._settle_tool_surface(sub_ctx=context)

    expected = set(EXPERT_MODE_TOOLS) | {"server:lookup"}
    assert set(settled.visible_tool_names) == expected
    assert set(settled.callable_tool_names) == expected
    assert not {
        "sub_agent",
        "subagent_control",
    } & set(settled.callable_tool_names)


async def test_tool_discovery_uses_the_same_team_surface(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(cast(Tool, _DummyTool("read")))
    registry.register(
        cast(Tool, _DummyTool("team_create", visibility_scope="main_only", deferred=True))
    )
    registry.register(
        cast(Tool, _DummyTool("team_delete", visibility_scope="main_only", deferred=True))
    )
    catalog = RuntimeCatalog(tool_registry=registry)
    search = ToolSearchTool(capability_catalog=catalog)
    registry.register(search)
    context = _context(tmp_path)

    lead_result = await search.execute({"query": "team_create"}, context)
    assert "#### 工具：`team_create`" in _text(lead_result)
    visible = ToolDiscoveryRuntime(capability_catalog=catalog).visible_surface(
        tool_surface=context.session_scope.tool_surface if context.session_scope else None
    )
    assert {entry.canonical_name for entry in visible} >= DEFERRED_TEAM_TOOLS

    context.subagent_depth = 1
    _as_member(context)
    member_result = await search.execute({"query": "team_create"}, context)
    assert "#### 工具：`team_create`" not in _text(member_result)
    assert "`team_create`" not in _text(member_result)


def test_persistent_member_launch_restores_surface_and_definition_fingerprint(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    registry.register(cast(Tool, _DummyTool("read")))
    member = _entry(_MEMBER, persona="PERSISTENT MEMBER PERSONA")
    catalog = _Catalog([member], tool_registry=registry)
    executor = _RecordingExecutor()
    spawned_context = _context(tmp_path)
    _as_member(spawned_context)
    spawned_context.subagent_name = _MEMBER
    original_launch = SubagentRunLaunch(
        manifest=member.manifest,
        executor=executor,
        parent_ctx=spawned_context,
        tool_registry=registry,
    )
    snapshot = build_definition_snapshot(original_launch)
    assert snapshot["expert_team"]["team_name"] == _TEAM
    agent = AgentSessionRecord(
        agent_id="ag:member",
        owner_session_id="session-a",
        child_session_id="child-session",
        agent_name=_MEMBER,
        agent_source="expert",
        lifecycle="persistent",
        definition_snapshot=snapshot,
    )
    control = SubagentControlTool(
        collaboration=cast(SubagentCollaborationService, SimpleNamespace()),
        capability_catalog=cast(RuntimeCatalog, catalog),
        executor=cast(SubAgentExecutor, executor),
    )
    # 团队身份由 parent 作用域的 expert 派生，故恢复会话仍是团队会话。
    next_run = _context(tmp_path, run_id="run-next", team=True)

    restored = control._build_launch(agent, next_run, call_id="continue")

    assert restored.parent_ctx is not next_run
    restored_scope = restored.parent_ctx.session_scope
    assert restored_scope is not None
    assert restored_scope.member is True
    assert restored_scope.team_name == _TEAM
    assert restored_scope.lead_agent == _LEAD
    assert restored_scope.tool_surface == ToolSurface(
        active=EXPERT_MODE_TOOLS,
        deferred=EXPERT_MODE_TOOLS,
        member=EXPERT_MODE_TOOLS,
        restricted=True,
    )
    assert restored.parent_ctx.team_registry is None
    assert restored.manifest.system_prompt == "PERSISTENT MEMBER PERSONA"
    assert build_definition_snapshot(restored) == snapshot
