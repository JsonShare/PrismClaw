"""Lock manager protocol — for distributed lock operations."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LockManagerProtocol(Protocol):
    """Protocol for the distributed lock manager."""
    async def acquire(self, key: str, *, timeout: float = 30.0) -> bool: ...
    async def release(self, key: str) -> None: ...
    async def extend(self, key: str, *, additional: float = 30.0) -> bool: ...
