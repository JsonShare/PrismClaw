"""Persistence context Protocol — covers PersistenceBundle's public surface."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from prismclaw.schema.services.lock import LockManagerProtocol
from prismclaw.schema.trajectory.trajectory import TrajectoryPersistence

if TYPE_CHECKING:
    from prismclaw.schema.agent.bundles import (
        RunOwnershipStoreProtocol,
    )


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
    def trajectory_persistence(self) -> TrajectoryPersistence | None: ...


#: Distributed lock manager — single source of truth is ``schema.services.lock``.
LockManager = LockManagerProtocol


# ── RunSnapshotStore Protocol ────────────────────────────────────


class RunSnapshotLike(Protocol):
    """快照的结构化最小视图：可序列化 + 暴露运行标识。"""

    @property
    def run_state(self) -> RunStateLike: ...

    @property
    def created_at_epoch_ms(self) -> float: ...

    def model_dump(self, *, mode: str = ...) -> dict[str, Any]: ...


class RunStateLike(Protocol):
    @property
    def run_id(self) -> str | None: ...

    @property
    def session_id(self) -> str: ...


@runtime_checkable
class RunSnapshotStore(Protocol):
    """与 prismclaw.engine.persistence.run_state.store.RunSnapshotStore duck-typing 兼容。

    所有方法均为 async（与 PostgresRunSnapshotStore 实际签名一致）。
    engine 层类型 RunSnapshot 以 Any 代替，避免 contracts 依赖 engine。
    """

    async def save(self, snapshot: RunSnapshotLike) -> None: ...
    async def load_latest(self, run_id: str) -> RunSnapshotLike | None: ...
    async def list_for_session(self, session_id: str) -> tuple[RunSnapshotLike, ...]: ...


__all__ = ["PersistenceContextProtocol", "LockManager", "RunSnapshotStore"]
