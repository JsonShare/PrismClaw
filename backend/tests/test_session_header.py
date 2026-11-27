from prismclaw.schema.agent.session import SessionEvent, SessionEventType, SessionHeader
from prismclaw.schema.session.types import SessionTree

EXPERT_FIELD_DEFAULTS = {
    "expert_id": None,
    "expert_runtime_identity": None,
    "last_user_prompt_expert_selection": None,
    "expert_activation_source": None,
    "selected_skill_ids": [],
}


def _header() -> SessionHeader:
    return SessionHeader(id="session-1", workspace_id="workspace-1", agent_id="agent-1")


def _assert_expert_defaults(header: SessionHeader) -> None:
    actual = {name: getattr(header, name) for name in EXPERT_FIELD_DEFAULTS}
    assert actual == EXPERT_FIELD_DEFAULTS


def _event(seq: int, event_type: SessionEventType, data: dict) -> SessionEvent:
    return SessionEvent(seq=seq, type=event_type, session_id="session-1", data=data)


def test_session_header_expert_field_defaults() -> None:
    header = _header()

    _assert_expert_defaults(header)
    assert set(EXPERT_FIELD_DEFAULTS).issubset(header.model_dump())

    other = _header()
    header.selected_skill_ids.append("capability/skill")
    assert other.selected_skill_ids == []


def test_legacy_session_header_snapshot_uses_expert_defaults() -> None:
    legacy_snapshot = {
        "type": "session",
        "version": 3,
        "id": "legacy-session",
        "workspace_id": "workspace-1",
        "agent_id": "agent-1",
        "title": "Legacy",
    }

    header = SessionHeader.model_validate(legacy_snapshot)

    _assert_expert_defaults(header)
    assert header.version == 3
    assert header.title == "Legacy"


def test_session_tree_preserves_fields_across_partial_header_updates() -> None:
    events = [
        _event(1, SessionEventType.SESSION_CREATED, {"header": _header().model_dump()}),
        _event(2, SessionEventType.REQUEST_HEADER, {"header": {"title": "Expert session"}}),
        _event(3, SessionEventType.REQUEST_HEADER, {"header": {"expert_id": "expert-1"}}),
        _event(
            4,
            SessionEventType.REQUEST_HEADER,
            {"header": {"selected_skill_ids": ["capability/skill"]}},
        ),
        _event(
            5,
            SessionEventType.REQUEST_HEADER,
            {"header": {"last_interaction_at": "2026-11-25T00:00:00+00:00"}},
        ),
    ]

    header = SessionTree.from_events(events).header

    assert header.title == "Expert session"
    assert header.expert_id == "expert-1"
    assert header.selected_skill_ids == ["capability/skill"]
    assert header.last_interaction_at == "2026-11-25T00:00:00+00:00"


def test_session_header_model_dump_round_trip() -> None:
    header = SessionHeader(
        id="session-1",
        workspace_id="workspace-1",
        agent_id="agent-1",
        expert_id="expert-1",
        expert_runtime_identity="runtime-1",
        last_user_prompt_expert_selection="expert-0",
        expert_activation_source="user",
        selected_skill_ids=["capability/skill", "expert-1/skill"],
    )

    dumped = header.model_dump()
    restored = SessionHeader.model_validate(dumped)

    assert restored.expert_id == "expert-1"
    assert restored.expert_runtime_identity == "runtime-1"
    assert restored.last_user_prompt_expert_selection == "expert-0"
    assert restored.expert_activation_source == "user"
    assert restored.selected_skill_ids == ["capability/skill", "expert-1/skill"]
    assert restored == header
    assert restored.model_dump() == dumped
