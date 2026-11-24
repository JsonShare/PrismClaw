"""Lock manager protocol — for distributed lock operations."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LockManagerProtocol(Protocol):
    """Protocol for the distributed lock manager."""

    async def acquire(self, key: str, ttl_ms: int = 30_000, *, owner: str = "") -> str: ...
    async def release(self, key: str, token: str) -> bool: ...
    async def renew(self, key: str, token: str, ttl_ms: int = 30_000) -> bool: ...
    async def owner_of(self, key: str) -> str | None: ...
