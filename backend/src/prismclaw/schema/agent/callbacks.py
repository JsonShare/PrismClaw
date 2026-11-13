"""Loop behavioral callbacks."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(slots=True)
class AgentCallbacks:
    """Agent loop 行为回调容器，可按需替换单个回调。"""

    on_compaction_node: Callable[..., None] | None = None
    on_mcp_death: Callable[[str], Awaitable[None]] | None = None
    on_subagent_progress: Callable[[Any], Awaitable[None]] | None = None

