from __future__ import annotations

from typing import Protocol

from prismclaw.engine.foundation.distributed_mutex import DistributedMutex
from prismclaw.schema.shared.exceptions import LockAcquireError
from prismclaw.schema.persistence.persistence import LockManager


class DreamExecutionLease(Protocol):
    def ensure_valid(self) -> None: ...

    async def release(self) -> None: ...


class DreamLockProvider(Protocol):
    async def try_acquire(self, session_key: str, *, owner_label: str) -> DreamExecutionLease | None: ...


class DistributedDreamLease:
    def __init__(self, mutex: DistributedMutex) -> None:
        self._mutex = mutex
        self._released = False

    def ensure_valid(self) -> None:
        self._mutex.check_alive()

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._mutex.__aexit__(None, None, None)


class DistributedDreamLockProvider:
    def __init__(
        self,
        lock_manager: LockManager,
        *,
        task_manager,
        ttl_ms: int = 300_000,
        heartbeat_interval_s: float = 30.0,
        key_prefix: str = "dream:session:",
    ) -> None:
        self._lock_manager = lock_manager
        self._task_manager = task_manager
        self._ttl_ms = ttl_ms
        self._heartbeat_interval_s = heartbeat_interval_s
        self._key_prefix = key_prefix

    async def try_acquire(self, session_key: str, *, owner_label: str) -> DreamExecutionLease | None:
        mutex = DistributedMutex(
            self._lock_manager,
            f"{self._key_prefix}{session_key}",
            task_manager=self._task_manager,
            ttl_ms=self._ttl_ms,
            heartbeat_interval_s=self._heartbeat_interval_s,
            owner_label=owner_label,
        )
        try:
            await mutex.__aenter__()
        except LockAcquireError:
            return None
        return DistributedDreamLease(mutex)
