from __future__ import annotations

import logging
from pathlib import Path

import pytest
from tests.engine.session_store import FakeRedisSessionStore

from prismclaw.engine.experts.codes import ExpertError, ExpertErrorCode
from prismclaw.engine.experts.loading.load_result import ExpertPackage
from prismclaw.engine.experts.manifest import ExpertManifest
from prismclaw.engine.experts.registry.expert_registry import (
    ExpertRegistry,
    ExpertRegistryIssue,
)
from prismclaw.engine.experts.selection import (
    ExpertState,
    get_session_expert_state,
    select_session_expert,
)
from prismclaw.engine.trajectory.bus import SessionBus
from prismclaw.engine.trajectory.bus_events import (
    ExpertCancelled,
    ExpertStateChanged,
    ExpertUnavailable,
)
from prismclaw.infra.config.capability_policy import CapabilityPolicyRegistry
from prismclaw.schema.agent.session import SessionEvent, SessionEventType, SessionHeader
from prismclaw.schema.session.event_logger import SessionEventLogger
from prismclaw.schema.session.types import SessionTree
from prismclaw.schema.storage.storage_types import SessionStore


def _expert(
    tmp_path: Path, name: str, *, expert_type: str = "agent", source: str = "experts"
) -> ExpertPackage:
    root = tmp_path / source / name
    root.mkdir(parents=True, exist_ok=True)
    return ExpertPackage(
        name=name,
        source=source,
        path=str(root),
        root_path=root,
        manifest=ExpertManifest.model_validate(
            {"name": name, "expertType": expert_type, "agentName": f"{name}-lead"}
        ),
    )


def _registries(
    tmp_path: Path,
    experts: list[ExpertPackage],
    *,
    unavailable_ids: list[str] | None = None,
    state_yaml: str = "version: 1\n",
) -> tuple[ExpertRegistry, CapabilityPolicyRegistry]:
    state_path = tmp_path / "capability_policy.yaml"
    state_path.write_text(state_yaml, encoding="utf-8")
    builtin = ExpertRegistry(
        experts,
        issues=[
            ExpertRegistryIssue(
                code="manifest_parse",
                expert_id=expert_id,
                message=f"{expert_id} failed to load",
            )
            for expert_id in (unavailable_ids or [])
        ],
    )
    return builtin, CapabilityPolicyRegistry(state_path, catalog=builtin)


async def _store(header: SessionHeader, *, with_history: bool = False) -> FakeRedisSessionStore:
    store = FakeRedisSessionStore()
    events = [
        SessionEvent(
            seq=0,
            type=SessionEventType.SESSION_CREATED,
            session_id=header.id,
            data={"type": "session_created", "header": header.model_dump()},
        )
    ]
    if with_history:
        events.append(
            SessionEventLogger().user_message(
                header.id,
                "Keep this message",
                entry_id="message-1",
            )
        )
    await store.commit_events(events)
    return store


async def _load_tree(store: SessionStore, session_id: str) -> SessionTree:
    tree = await store.load(session_id)
    assert tree is not None
    return tree


@pytest.mark.parametrize(
    ("state_yaml", "expert_id", "expected"),
    [
        (
            "version: 1\noverrides:\n  expert:\n    writer:\n      enabled: false\n",
            "writer",
            ExpertErrorCode.MODULE_DISABLED.value,
        ),
        ("version: 1\n", "nowhere", ExpertErrorCode.EXPERT_UNAVAILABLE.value),
        ("version: 1\n", None, None),
    ],
)
async def test_session_expert_state_reports_unavailability(
    tmp_path: Path,
    state_yaml: str,
    expert_id: str | None,
    expected: str | None,
) -> None:
    """查询路径必须与 run 入口判定一致：禁用/不存在都要显式报出来。"""
    builtin, states = _registries(tmp_path, [_expert(tmp_path, "writer")], state_yaml=state_yaml)
    header = SessionHeader(
        id="session-1",
        workspace_id="workspace-1",
        agent_id="agent-1",
        expert_id=expert_id,
        expert_activation_source="user" if expert_id else None,
    )
    store = await _store(header)

    state = await get_session_expert_state(
        header.id,
        session_store=store,
        expert_registry=builtin,
        capability_policy_registry=states,
    )

    assert state["unavailable_reason"] == expected


def _state(
    expert_id: str | None,
    *,
    source: str | None = "user",
    unavailable_reason: str | None = None,
) -> ExpertState:
    return {
        "expert_id": expert_id,
        "source": source,
        "selected_skill_ids": ["research:search", "writer:draft"],
        "unavailable_reason": unavailable_reason,
    }


async def test_user_expert_lifecycle_preserves_session(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    experts = [
        _expert(tmp_path, "writer"),
        _expert(tmp_path, "researcher"),
        _expert(tmp_path, "legal-team", expert_type="team"),
    ]
    builtin, states = _registries(tmp_path, experts)
    header = SessionHeader(
        id="session-1",
        workspace_id="workspace-1",
        agent_id="agent-1",
        title="Keep this title",
        model_override="model-1",
        selected_skill_ids=["research:search", "writer:draft"],
    )
    store = await _store(header, with_history=True)
    before = await store.load(header.id)
    assert before is not None
    base_header = before.header.model_dump()
    base_history = (before.entries, before.order, before.leaf_id)
    states_before = (tmp_path / "capability_policy.yaml").read_bytes()
    bus = SessionBus()
    state_events: list[ExpertStateChanged] = []
    cancelled_events: list[ExpertCancelled] = []
    bus.subscribe(ExpertStateChanged, state_events.append)
    bus.subscribe(ExpertCancelled, cancelled_events.append)

    with bus.bind(), caplog.at_level(
        logging.INFO, logger="prismclaw.engine.experts.selection"
    ):
        assert await get_session_expert_state(
            header.id,
            session_store=store,
            expert_registry=builtin,
            capability_policy_registry=states,
        ) == _state(None, source=None)

        selected = await select_session_expert(
            header.id,
            "writer@experts",
            session_store=store,
            expert_registry=builtin,
            capability_policy_registry=states,
        )
        assert selected == _state("writer")
        persisted = await store.load(header.id)
        assert persisted is not None
        assert persisted.header.expert_runtime_identity == "writer"
        assert persisted.header.last_user_prompt_expert_selection == "writer"

        switched = await select_session_expert(
            header.id,
            "legal-team",
            session_store=store,
            expert_registry=builtin,
            capability_policy_registry=states,
        )
        assert switched == _state("legal-team")

        cancelled = await select_session_expert(
            header.id,
            None,
            session_store=store,
            expert_registry=builtin,
            capability_policy_registry=states,
        )
        assert cancelled == _state(None)
        cleared = await store.load(header.id)
        assert cleared is not None
        assert cleared.header.expert_id is None
        assert cleared.header.expert_runtime_identity is None
        assert cleared.header.last_user_prompt_expert_selection == "legal-team"

        reselected = await select_session_expert(
            header.id,
            "researcher",
            session_store=store,
            expert_registry=builtin,
            capability_policy_registry=states,
        )
        assert reselected == _state("researcher")
        assert (
            await get_session_expert_state(
                header.id,
                session_store=store,
                expert_registry=builtin,
                capability_policy_registry=states,
            )
            == reselected
        )

    final = await store.load(header.id)
    assert final is not None
    assert final.header.expert_activation_source == "user"
    assert final.header.last_user_prompt_expert_selection == "researcher"
    assert (final.entries, final.order, final.leaf_id) == base_history
    expert_fields = {
        "expert_id",
        "expert_runtime_identity",
        "last_user_prompt_expert_selection",
        "expert_activation_source",
    }
    assert {
        key: value for key, value in final.header.model_dump().items() if key not in expert_fields
    } == {key: value for key, value in base_header.items() if key not in expert_fields}
    assert (tmp_path / "capability_policy.yaml").read_bytes() == states_before
    assert [event.expert_id for event in state_events] == [
        "writer",
        "legal-team",
        None,
        "researcher",
    ]
    assert [event.state for event in state_events] == ["active", "active", "none", "active"]
    assert [event.expert_type for event in state_events] == ["agent", "team", None, "agent"]
    assert [event.source for event in state_events] == ["user"] * 4
    assert [event.revision for event in state_events] == [3, 4, 5, 6]
    assert [event.reason for event in state_events] == [
        "expert_selection",
        "expert_selection",
        "expert_cancellation",
        "expert_selection",
    ]
    assert [event.turn for event in state_events] == [1] * 4
    assert len(cancelled_events) == 1
    assert cancelled_events[0].model_dump() == {
        "session_id": "session-1",
        "expert_id": "legal-team",
        "source": "user",
        "reason": "expert_cancellation",
        "turn": 1,
    }

    audits = [
        record.__dict__["audit"]
        for record in caplog.records
        if record.getMessage() in {"expert_switch", "expert_cancelled"}
    ]
    assert audits == [
        {
            "session_id": "session-1",
            "expert_id": "writer",
            "source": "user",
            "reason": "expert_selection",
            "turn": 1,
            "from": None,
            "to": "writer",
        },
        {
            "session_id": "session-1",
            "expert_id": "legal-team",
            "source": "user",
            "reason": "expert_selection",
            "turn": 1,
            "from": "writer",
            "to": "legal-team",
        },
        {
            "session_id": "session-1",
            "expert_id": "legal-team",
            "source": "user",
            "reason": "expert_cancellation",
            "turn": 1,
        },
        {
            "session_id": "session-1",
            "expert_id": "researcher",
            "source": "user",
            "reason": "expert_selection",
            "turn": 1,
            "from": None,
            "to": "researcher",
        },
    ]
    assert all("switch_count" not in audit for audit in audits)

    header_events = [
        event
        for event in await store.get_events(header.id)
        if event.type == SessionEventType.REQUEST_HEADER
    ]
    assert len(header_events) == 4
    assert all("selected_skill_ids" not in event.data["header"] for event in header_events)
    assert all("agent_id" not in event.data["header"] for event in header_events)


async def test_same_user_selection_does_not_commit_or_emit_again(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    writer = _expert(tmp_path, "writer")
    builtin, states = _registries(tmp_path, [writer])
    header = SessionHeader(
        id="session-1",
        workspace_id="workspace-1",
        agent_id="agent-1",
        expert_id="writer",
        expert_runtime_identity="writer",
        last_user_prompt_expert_selection="writer",
        expert_activation_source="user",
        selected_skill_ids=["research:search", "writer:draft"],
    )
    store = await _store(header)
    bus = SessionBus()
    state_events: list[ExpertStateChanged] = []
    bus.subscribe(ExpertStateChanged, state_events.append)

    with bus.bind(), caplog.at_level(
        logging.INFO, logger="prismclaw.engine.experts.selection"
    ):
        result = await select_session_expert(
            header.id,
            "writer",
            session_store=store,
            expert_registry=builtin,
            capability_policy_registry=states,
        )

    assert result == _state("writer")
    assert state_events == []
    assert [record for record in caplog.records if record.getMessage() == "expert_switch"] == []
    assert [
        event
        for event in await store.get_events(header.id)
        if event.type == SessionEventType.REQUEST_HEADER
    ] == []


async def test_user_cancellation_of_model_expert_records_user_source(tmp_path: Path) -> None:
    writer = _expert(tmp_path, "writer")
    builtin, states = _registries(tmp_path, [writer])
    header = SessionHeader(
        id="session-1",
        workspace_id="workspace-1",
        agent_id="agent-1",
        expert_id="writer",
        expert_activation_source="model",
        selected_skill_ids=["research:search", "writer:draft"],
    )
    store = await _store(header)

    result = await select_session_expert(
        header.id,
        None,
        session_store=store,
        expert_registry=builtin,
        capability_policy_registry=states,
    )

    assert result == _state(None)
    persisted = await store.load(header.id)
    assert persisted is not None
    assert persisted.header.expert_activation_source == "user"


async def test_repeated_cancellation_does_not_commit_or_emit_again(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    writer = _expert(tmp_path, "writer")
    builtin, states = _registries(tmp_path, [writer])
    header = SessionHeader(
        id="session-1",
        workspace_id="workspace-1",
        agent_id="agent-1",
        last_user_prompt_expert_selection="writer",
        expert_activation_source="user",
        selected_skill_ids=["research:search", "writer:draft"],
    )
    store = await _store(header)
    bus = SessionBus()
    state_events: list[ExpertStateChanged] = []
    cancelled_events: list[ExpertCancelled] = []
    bus.subscribe(ExpertStateChanged, state_events.append)
    bus.subscribe(ExpertCancelled, cancelled_events.append)

    with bus.bind(), caplog.at_level(
        logging.INFO, logger="prismclaw.engine.experts.selection"
    ):
        result = await select_session_expert(
            header.id,
            None,
            session_store=store,
            expert_registry=builtin,
            capability_policy_registry=states,
        )

    assert result == _state(None)
    assert state_events == []
    assert cancelled_events == []
    assert [record for record in caplog.records if record.getMessage() == "expert_cancelled"] == []
    assert [
        event
        for event in await store.get_events(header.id)
        if event.type == SessionEventType.REQUEST_HEADER
    ] == []


async def test_user_selection_rejects_ambiguous_id_and_accepts_full_id(
    tmp_path: Path,
) -> None:
    deployed = _expert(tmp_path, "writer")
    alternate = _expert(tmp_path, "writer", source="builtin")
    builtin, states = _registries(tmp_path, [deployed, alternate])
    header = SessionHeader(
        id="session-1",
        workspace_id="workspace-1",
        agent_id="agent-1",
    )
    store = await _store(header)

    with pytest.raises(ExpertError) as raised:
        await select_session_expert(
            header.id,
            "writer",
            session_store=store,
            expert_registry=builtin,
            capability_policy_registry=states,
        )

    assert raised.value.code == ExpertErrorCode.EXPERT_ID_AMBIGUOUS
    assert raised.value.detail == {
        "expert_id": "writer",
        "sources": ["builtin", "experts"],
    }
    assert (await _load_tree(store, header.id)).header == header

    selected = await select_session_expert(
        header.id,
        "writer@builtin",
        session_store=store,
        expert_registry=builtin,
        capability_policy_registry=states,
    )

    assert selected["expert_id"] == "writer"
    persisted = await store.load(header.id)
    assert persisted is not None


@pytest.mark.parametrize(
    ("expert_id", "expected_code"),
    [
        ("missing", ExpertErrorCode.EXPERT_NOT_FOUND),
        ("broken", ExpertErrorCode.EXPERT_UNAVAILABLE),
    ],
)
async def test_user_selection_distinguishes_unknown_and_unavailable_without_writing(
    tmp_path: Path, expert_id: str, expected_code: ExpertErrorCode
) -> None:
    writer = _expert(tmp_path, "writer")
    builtin, states = _registries(
        tmp_path,
        [writer],
        unavailable_ids=["broken"],
    )
    header = SessionHeader(
        id="session-1",
        workspace_id="workspace-1",
        agent_id="agent-1",
        title="Keep this title",
        selected_skill_ids=["research:search"],
    )
    store = await _store(header)

    with pytest.raises(ExpertError) as raised:
        await select_session_expert(
            header.id,
            expert_id,
            session_store=store,
            expert_registry=builtin,
            capability_policy_registry=states,
        )

    assert raised.value.code == expected_code
    assert raised.value.detail == {"expert_id": expert_id}
    assert (await _load_tree(store, header.id)).header == header
    assert [
        event
        for event in await store.get_events(header.id)
        if event.type == SessionEventType.REQUEST_HEADER
    ] == []


async def test_unavailable_user_selection_emits_event_and_audit_without_state_change(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    writer = _expert(tmp_path, "writer")
    builtin, states = _registries(
        tmp_path,
        [writer],
        unavailable_ids=["broken"],
    )
    header = SessionHeader(
        id="session-1",
        workspace_id="workspace-1",
        agent_id="agent-1",
        expert_id="writer",
        expert_activation_source="model",
        selected_skill_ids=["research:search", "writer:draft"],
    )
    store = await _store(header)
    bus = SessionBus()
    unavailable_events: list[ExpertUnavailable] = []
    state_events: list[ExpertStateChanged] = []
    bus.subscribe(ExpertUnavailable, unavailable_events.append)
    bus.subscribe(ExpertStateChanged, state_events.append)

    with bus.bind(), caplog.at_level(
        logging.INFO, logger="prismclaw.engine.experts.selection"
    ), pytest.raises(ExpertError) as raised:
        await select_session_expert(
            header.id,
            "broken",
            session_store=store,
            expert_registry=builtin,
            capability_policy_registry=states,
        )

    assert raised.value.code == ExpertErrorCode.EXPERT_UNAVAILABLE
    assert (await _load_tree(store, header.id)).header == header
    assert state_events == []
    assert [event.model_dump() for event in unavailable_events] == [
        {
            "session_id": "session-1",
            "expert_id": "broken",
            "code": ExpertErrorCode.EXPERT_UNAVAILABLE,
            "source": "user",
            "reason": "expert_selection",
            "turn": 1,
        }
    ]
    audits = [record for record in caplog.records if record.getMessage() == "expert_unavailable"]
    assert len(audits) == 1
    assert audits[0].__dict__["audit"] == {
        "session_id": "session-1",
        "expert_id": "broken",
        "source": "user",
        "reason": "expert_selection",
        "turn": 1,
    }


@pytest.mark.parametrize("expert_id", ["writer", "legal-team"])
async def test_user_selection_rejects_disabled_agent_and_team_without_writing(
    tmp_path: Path, expert_id: str
) -> None:
    experts = [
        _expert(tmp_path, "writer"),
        _expert(tmp_path, "legal-team", expert_type="team"),
    ]
    builtin, states = _registries(
        tmp_path,
        experts,
        state_yaml=(
            "version: 1\n"
            "overrides:\n"
            "  expert:\n"
            "    writer: false\n"
            "  team:\n"
            "    legal-team: false\n"
        ),
    )
    header = SessionHeader(
        id="session-1",
        workspace_id="workspace-1",
        agent_id="agent-1",
        expert_id="previous",
        expert_activation_source="model",
        selected_skill_ids=["research:search"],
    )
    store = await _store(header)

    with pytest.raises(ExpertError) as raised:
        await select_session_expert(
            header.id,
            expert_id,
            session_store=store,
            expert_registry=builtin,
            capability_policy_registry=states,
        )

    assert raised.value.code == ExpertErrorCode.MODULE_DISABLED
    assert raised.value.detail == {"expert_id": expert_id}
    assert (await _load_tree(store, header.id)).header == header
    assert [
        event
        for event in await store.get_events(header.id)
        if event.type == SessionEventType.REQUEST_HEADER
    ] == []
