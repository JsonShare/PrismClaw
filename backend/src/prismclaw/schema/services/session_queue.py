"""Session queue protocol — for interactive user pauses during agent execution."""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from prismclaw.schema.agent.types import RunRequest


@runtime_checkable
class SessionQueueProtocol(Protocol):
    """Protocol for the session queue used by AskUserQuestion etc.

    Only available in interactive channels (web). Headless channels
    (scheduler/CLI/test) set this to None.
    """
    async def enqueue(self, session_id: str, request: RunRequest) -> None: ...
    async def dequeue(self, session_id: str) -> RunRequest | None: ...
    async def is_active(self, session_id: str) -> bool: ...
