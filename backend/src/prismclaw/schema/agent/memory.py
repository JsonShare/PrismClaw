"""Memory gateway contracts.

Protocol for memory write arbitration.
"""
from __future__ import annotations

from typing import Any, Protocol

from prismclaw.schema.memory.memory_types import MemoryEntry


class MemoryGatewayProtocol(Protocol):
    """Protocol for memory write gateway — duck-typing compatible with engine/memory/gateway.MemoryWriteGateway."""

    async def submit(
        self,
        session_key: str,
        candidate: MemoryEntry,
        *,
        source: str = "memorize",
    ) -> Any:
        """Submit a memory candidate for arbitration and possible storage."""
        ...
