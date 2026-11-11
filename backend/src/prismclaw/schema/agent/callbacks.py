"""Loop callback protocol — converges scattered functional fields on deps."""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, runtime_checkable


@runtime_checkable
class LoopCallbacks(Protocol):
    """Agent loop behavioral callbacks — converges scattered function fields on deps.

    Aligned with Pi's minimal injection philosophy: all behavioral callbacks
    that were previously scattered as individual fields on AgentRunnerDeps
    are now grouped into a single protocol.
    """
    on_compaction_node: Callable[..., None] | None
    on_mcp_death: Callable[[str], Awaitable[None]] | None
    on_subagent_progress: Callable[[Any], Awaitable[None]] | None
    on_stream_event: Callable[[Any], Awaitable[None]] | None
