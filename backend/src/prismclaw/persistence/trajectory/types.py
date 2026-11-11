"""Backward-compat re-export — domain models have moved to contracts.

All Pydantic models (RequestAuditRecord, TrajectoryCell, etc.) are now
defined in ``prismclaw.contracts.infra.trajectory``.  This module re-exports
them so that existing ``from prismclaw.persistence.trajectory.types import``
statements continue to work during the transition period.
"""
from prismclaw.schema.trajectory import (  # noqa: F401
    RequestAuditRecord,
    TrajectoryCell,
    TrajectoryCellSnapshot,
    TrajectorySnapshot,
    TrajectorySnapshotMeta,
)

__all__ = [
    "RequestAuditRecord",
    "TrajectoryCell",
    "TrajectoryCellSnapshot",
    "TrajectorySnapshot",
    "TrajectorySnapshotMeta",
]
