"""Dream subsystem contracts.

Protocol for dream dispatch queue operations.
"""
from __future__ import annotations

from typing import Any, Protocol


class DreamDispatchProtocol(Protocol):
    """Protocol for dream dispatch — duck-typing compatible with dream/dispatch.RedisDreamDispatchQueue."""

    async def ensure_group(self) -> None:
        """Ensure the consumer group exists."""
        ...

    async def enqueue(self, job: Any) -> bool:
        """Enqueue a dream job."""
        ...

    async def read(
        self,
        consumer_name: str,
        *,
        count: int = 10,
        block_ms: int = 1000,
    ) -> list[Any]:
        """Read pending jobs from the queue."""
        ...

    async def ack(self, job: Any) -> None:
        """Acknowledge a processed job."""
        ...

    async def fail(self, job: Any, *, reason: str | None = None) -> dict[str, Any]:
        """Mark a job as failed."""
        ...

    async def stats(self) -> dict[str, Any]:
        """Return queue statistics."""
        ...
