from __future__ import annotations

import pytest
from pydantic import ValidationError

from prismclaw.engine.trajectory.bus import SessionBus
from prismclaw.engine.trajectory.bus_events import (
    ALL_EVENTS,
    ExpertCancelled,
    ExpertStateChanged,
    ExpertUnavailable,
)

type ExpertEvent = ExpertStateChanged | ExpertUnavailable | ExpertCancelled

EXPERT_EVENT_CASES: tuple[tuple[type[ExpertEvent], dict[str, object], set[str]], ...] = (
    (
        ExpertStateChanged,
        {
            "session_id": "s1",
            "revision": 7,
            "state": "active",
            "expert_id": "writer",
            "expert_type": "agent",
            "source": "model",
            "updated_at": "2026-11-25T10:00:00Z",
            "reason": "domain match",
            "turn": 3,
        },
        {
            "session_id",
            "revision",
            "state",
            "expert_id",
            "expert_type",
            "source",
            "updated_at",
            "reason",
            "turn",
        },
    ),
    (
        ExpertUnavailable,
        {
            "session_id": "s1",
            "expert_id": "writer",
            "code": "EXPERT_UNAVAILABLE",
            "source": "user",
            "reason": "broken manifest",
            "turn": 3,
        },
        {"session_id", "expert_id", "code", "source", "reason", "turn"},
    ),
    (
        ExpertCancelled,
        {
            "session_id": "s1",
            "expert_id": "writer",
            "source": "user",
            "reason": "user cancelled",
            "turn": 4,
        },
        {"session_id", "expert_id", "source", "reason", "turn"},
    ),
)


@pytest.mark.parametrize(("event_type", "payload", "fields"), EXPERT_EVENT_CASES)
def test_expert_event_explicit_fields_and_extra_forbidden(
    event_type: type[ExpertEvent], payload: dict[str, object], fields: set[str]
) -> None:
    assert set(event_type.model_fields) == fields
    invalid_payload = {**payload, "switch_count": 1}
    with pytest.raises(ValidationError):
        event_type(**invalid_payload)


@pytest.mark.parametrize(("event_type", "payload", "_fields"), EXPERT_EVENT_CASES)
def test_expert_event_is_frozen(
    event_type: type[ExpertEvent], payload: dict[str, object], _fields: set[str]
) -> None:
    event = event_type(**payload)

    with pytest.raises(ValidationError):
        event.session_id = "another-session"  # type: ignore[misc]


@pytest.mark.parametrize(("event_type", "payload", "_fields"), EXPERT_EVENT_CASES)
def test_expert_event_serializes_and_is_consumed_by_session_bus(
    event_type: type[ExpertEvent], payload: dict[str, object], _fields: set[str]
) -> None:
    event = event_type.model_validate_json(event_type(**payload).model_dump_json())
    bus = SessionBus()
    received: list[ExpertEvent] = []
    bus.subscribe(event_type, received.append)

    bus.publish(event)

    assert received == [event]
    assert event_type in ALL_EVENTS
