"""Trajectory domain contracts — 审计轨迹领域模型与 Protocol。

Pydantic 数据模型（RequestAuditRecord, TrajectoryCell 等）定义在 trajectory_types，
persistence 和 engine 层通过 contracts 单向引用，消除循环依赖。

此处仅保留 Protocol 定义 + 从 trajectory_types 的 re-export。
"""
from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable

from prismclaw.schema.trajectory.trajectory_types import (  # noqa: F401
    STATE_VERSION,
    RequestAuditRecord,
    TrajectoryCell,
    TrajectoryCellSnapshot,
    TrajectoryProjectionState,
    TrajectorySnapshot,
    TrajectorySnapshotMeta,
    init_state,
    view,
)


# ── Persistence Protocol ─────────────────────────────────────────────
# Engine 层通过此 Protocol 访问 I/O，不直接 import persistence。

@runtime_checkable
class TrajectoryPersistence(Protocol):
    """Abstract I/O boundary for trajectory audit data.

    All methods encapsulate their own session management — callers
    never pass ``session_factory``.  This keeps the engine layer
    completely free of persistence imports.
    """

    async def flush_state(
        self,
        state: TrajectoryProjectionState,
        *,
        only_if_dirty: bool = True,
    ) -> bool: ...

    async def load_state(
        self,
        session_id: str,
        *,
        state_initializer: Callable[[str], TrajectoryProjectionState] | None = None,
    ) -> TrajectoryProjectionState: ...

    async def insert_cell(self, cell: TrajectoryCell) -> None: ...

    async def insert_system_cell(
        self,
        *,
        session_id: str,
        turn: int,
        iteration: int,
        sha1_16: str,
        full_text: str,
    ) -> None: ...

    async def mark_or_synthesize_error(
        self,
        *,
        session_id: str,
        turn: int,
        iteration: int | None,
        error_code: str,
        error_message: str,
        model: str | None,
        ttft_ms: float | None,
        total_ms: float | None,
        failed_at_ms: int,
    ) -> None: ...

    async def read_snapshot(
        self,
        session_id: str,
        *,
        limit: int | None = None,
        ascending: bool = True,
    ) -> TrajectorySnapshot: ...

    async def read_cells(
        self,
        session_id: str,
        *,
        after_seq: int = 0,
    ) -> list[TrajectoryCell]: ...

    async def init_schema(self) -> None: ...


__all__ = [
    # 领域数据模型
    "RequestAuditRecord",
    "TrajectoryCell",
    "TrajectoryCellSnapshot",
    "TrajectorySnapshot",
    "TrajectorySnapshotMeta",
    # 投影状态 & 纯函数
    "STATE_VERSION",
    "TrajectoryProjectionState",
    "init_state",
    "view",
    # Protocol
    "TrajectoryPersistence",
]
