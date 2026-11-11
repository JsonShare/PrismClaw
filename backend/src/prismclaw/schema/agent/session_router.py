"""Session router contracts.

Protocol for session lifecycle management.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from prismclaw.schema.session.types import SessionTree


class SessionRouterProtocol(Protocol):
    """Protocol for session routing — duck-typing compatible with delivery/session_router.SessionRouter."""

    async def resolve_or_create(
        self,
        session_key: str,
        workspace_id: str,
        agent_id: str = "default",
    ) -> tuple[str, SessionTree]:
        """Resolve an existing session or create a new one, returning (session_id, tree)."""
        ...

    async def rotate(
        self,
        session_key: str,
        workspace_id: str,
        agent_id: str = "default",
    ) -> tuple[str, SessionTree]:
        """Rotate the session, archiving the current one and starting fresh."""
        ...

    async def update_last_interaction(self, session_id: str) -> None:
        """Update the last interaction timestamp for a session."""
        ...

    async def check_idle_reset(
        self,
        session_key: str,
        session_id: str,
        idle_minutes: int,
    ) -> bool:
        """Check if a session should be reset due to inactivity."""
        ...
