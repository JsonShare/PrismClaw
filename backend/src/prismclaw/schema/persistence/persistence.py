from __future__ import annotations

"""Persistence context Protocol — covers PersistenceBundle's public surface."""

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from prismclaw.schema.agent.bundles import RunOwnershipStoreProtocol, TrajectoryPersistenceProtocol


class PersistenceContextProtocol(Protocol):
    """Runner 需要的持久化上下文——覆盖 PersistenceBundle 的公开能力。

    让 ownership.py 等文件通过 Protocol 引用 persistence，
    而非 getattr 动态访问。
    """

    @property
    def run_snapshot_store(self) -> RunSnapshotStore | None: ...

    @property
    def run_ownership_store(self) -> RunOwnershipStoreProtocol | None: ...

    @property
    def trajectory_persistence(self) -> TrajectoryPersistenceProtocol | None: ...


@runtime_checkable
class LockManager(Protocol):
    """Distributed lock manager Protocol — abstracts RedisLockManager.

    Engine code should depend on this Protocol instead of importing
    the concrete RedisLockManager from persistence.
    """

    async def acquire(self, key: str, ttl_ms: int = 30_000) -> str: ...
    async def release(self, key: str, token: str) -> bool: ...
    async def renew(self, key: str, token: str, ttl_ms: int = 30_000) -> bool: ...


# ── RunSnapshotStore Protocol ────────────────────────────────────

@runtime_checkable
class RunSnapshotStore(Protocol):
    """与 prismclaw.engine.persistence.run_state.store.RunSnapshotStore duck-typing 兼容。

    所有方法均为 async（与 PostgresRunSnapshotStore 实际签名一致）。
    engine 层类型 RunSnapshot 以 Any 代替，避免 contracts 依赖 engine。
    """

    async def save(self, snapshot: Any) -> None: ...
    async def load_latest(self, run_id: str) -> Any | None: ...
    async def list_for_session(self, session_id: str) -> tuple[Any, ...]: ...


__all__ = ["PersistenceContextProtocol", "LockManager", "RunSnapshotStore"]
