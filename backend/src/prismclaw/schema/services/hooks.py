"""Hook subsystem contracts.

Protocols for hook execution and decision handling.
Duck-typing compatible with engine.services.plugins.handlers.HookHandler.
"""
from __future__ import annotations

from typing import Any, Protocol


class HookContextProtocol(Protocol):
    """Minimal context passed to hook handlers."""

    @property
    def event_name(self) -> str: ...

    @property
    def payload(self) -> dict[str, Any]: ...


class HookResultProtocol(Protocol):
    """Result returned by a hook handler."""

    @property
    def decision(self) -> str: ...  # "allow" | "block" | "modify"

    @property
    def modified_payload(self) -> dict[str, Any] | None: ...

    @property
    def reason(self) -> str | None: ...


class HookHandlerProtocol(Protocol):
    """Protocol for hook execution — duck-typing compatible with HookHandler(ABC)."""

    async def execute(self, context: HookContextProtocol) -> HookResultProtocol:
        """Execute the hook with the given context, returning a result."""
        ...
