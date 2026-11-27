from __future__ import annotations

import logging

import pytest

from prismclaw.engine.experts.codes import ExpertErrorCode
from prismclaw.engine.experts.prompt_slot import (
    ROLE_OVERRIDE,
    ExpertPromptSlotCollector,
    build_expert_agent_prompt,
    strip_expert_agent_frontmatter,
)
from prismclaw.engine.trajectory.bus import SessionBus
from prismclaw.engine.trajectory.bus_events import (
    ExpertPromptDegraded,
    ExpertStateChanged,
    ExpertUnavailable,
)
from prismclaw.schema.agent.session import SessionHeader


def _header(**fields: object) -> SessionHeader:
    return SessionHeader(
        id="session-1",
        workspace_id="workspace-1",
        agent_id="agent-1",
        **fields,
    )


@pytest.mark.parametrize(
    ("agent_md", "body"),
    [
        (
            "---\nname: writer\ndescription: Writes carefully\n---\n\n  Expert body.  \n",
            "Expert body.",
        ),
        ("  \nExpert body without frontmatter.\n  ", "Expert body without frontmatter."),
        ("---\nname: empty\n---\n \n\t", ""),
    ],
)
def test_agent_prompt_matches_workbuddy_frontmatter_semantics(
    agent_md: str, body: str
) -> None:
    assert strip_expert_agent_frontmatter(agent_md) == body
    assert build_expert_agent_prompt(agent_md) == (
        f"{ROLE_OVERRIDE}\n\n{body}" if body else ""
    )


@pytest.mark.asyncio
async def test_collector_resolves_only_the_session_expert() -> None:
    calls: list[dict[str, str | None]] = []

    async def resolve(**kwargs: str | None) -> str:
        calls.append(kwargs)
        return "---\nname: writer\n---\nWrite precisely."

    collector = ExpertPromptSlotCollector(resolve)
    header = _header(
        expert_id="writer",
        expert_runtime_identity="writer-lead",
    )

    assert await collector.collect(_header()) == ""
    assert calls == []
    assert await collector.collect(header) == f"{ROLE_OVERRIDE}\n\nWrite precisely."
    assert calls == [
        {
            "expert_id": "writer",
            "expert_name_hint": "writer-lead",
        }
    ]


@pytest.mark.asyncio
async def test_collector_failure_degrades_without_mutating_header(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def resolve(**kwargs: str | None) -> str:
        raise ValueError("broken agent markdown")

    header = _header(expert_id="writer", expert_activation_source="user")
    before = header.model_dump()
    bus = SessionBus()
    degraded_events: list[ExpertPromptDegraded] = []
    unavailable_events: list[ExpertUnavailable] = []
    state_events: list[ExpertStateChanged] = []
    bus.subscribe(ExpertPromptDegraded, degraded_events.append)
    bus.subscribe(ExpertUnavailable, unavailable_events.append)
    bus.subscribe(ExpertStateChanged, state_events.append)

    with bus.bind(), caplog.at_level(logging.INFO, logger="prismclaw.engine.experts.prompt_slot"):
        assert await ExpertPromptSlotCollector(resolve).collect(header) == ""

    assert header.model_dump() == before
    assert [event.model_dump() for event in degraded_events] == [
        {
            "code": ExpertErrorCode.EXPERT_UNAVAILABLE,
            "session_id": "session-1",
            "expert_id": "writer",
            "detail": "broken agent markdown",
            "estimated_tokens": None,
            "context_window": None,
            "threshold": None,
        }
    ]
    assert [event.model_dump() for event in unavailable_events] == [
        {
            "session_id": "session-1",
            "expert_id": "writer",
            "code": ExpertErrorCode.EXPERT_UNAVAILABLE,
            "source": "user",
            "reason": "broken agent markdown",
            "turn": None,
        }
    ]
    assert state_events == []
    audits = [record for record in caplog.records if record.getMessage() == "expert_unavailable"]
    assert len(audits) == 1
    assert audits[0].__dict__["audit"] == {
        "session_id": "session-1",
        "expert_id": "writer",
        "source": "user",
        "reason": "broken agent markdown",
        "turn": None,
    }
    assert "EXPERT_UNAVAILABLE" in caplog.text
    assert "broken agent markdown" in caplog.text
