"""Session queue protocol — for interactive user pauses during agent execution."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ApprovalRef(Protocol):
    """交互式暂停记录的最小视图（引擎层只读取 ``approval_id``）。"""

    @property
    def approval_id(self) -> str: ...


@runtime_checkable
class SessionQueueProtocol(Protocol):
    """Protocol for the session queue used by AskUserQuestion etc.

    Only available in interactive channels (web). Headless channels
    (scheduler/CLI/test) set this to None.

    The engine only needs the clarification surface; the concrete web queue
    (``prismclaw.app.web.chat.chat_protocol.SessionQueue``) offers more.
    """

    def create_pending(
        self,
        conversation_id: str,
        tool_call_id: str,
        tool_name: str,
        *,
        run_id: str,
        turn_id: str,
        questions: list[dict[str, Any]] | None = None,
    ) -> ApprovalRef: ...

    def set_phase(self, phase: str | None) -> None: ...

    async def wait_for_clarification(self, approval_id: str) -> str | None: ...
