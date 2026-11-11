from __future__ import annotations

from dataclasses import dataclass, field

from prismclaw.schema.agent.data_types import ContentBlock


@dataclass
class InboundMessage:
    """A normalized inbound message from any channel."""

    session_id: str
    user_message: str
    workspace_id: str
    channel: str
    attachments: list[ContentBlock] = field(default_factory=list)
    raw: dict = field(default_factory=dict)  # type: ignore[type-arg]
