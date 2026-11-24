"""Dream subsystem contracts.

Protocol for dream dispatch queue operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from prismclaw.engine.dream.dispatch.dispatch import DreamDispatchJob


class DreamDispatchProtocol(Protocol):
    """
    Protocol for dream dispatch — duck-typing compatible with
    dream/dispatch.RedisDreamDispatchQueue.
    """

    async def ensure_group(self) -> None:
        """Ensure the consumer group exists."""
        ...

    async def enqueue(self, job: DreamDispatchJob) -> bool:
        """Enqueue a dream job."""
        ...

    async def read(
        self,
        consumer_name: str,
        *,
        count: int = 10,
        block_ms: int = 1000,
    ) -> list[DreamDispatchJob]:
        """Read pending jobs from the queue."""
        ...

    async def ack(self, job: DreamDispatchJob) -> None:
        """Acknowledge a processed job."""
        ...

    async def fail(self, job: DreamDispatchJob, *, reason: str | None = None) -> dict[str, Any]:
        """Mark a job as failed."""
        ...

    async def stats(self) -> dict[str, Any]:
        """Return queue statistics."""
        ...
