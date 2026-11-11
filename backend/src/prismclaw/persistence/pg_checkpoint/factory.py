from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from prismclaw.schema.persistence.persistence import RunSnapshotStore


def create_run_snapshot_store(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    payload_parser: Any | None = None,
) -> RunSnapshotStore:
    from prismclaw.persistence.pg_checkpoint.store import PostgresRunSnapshotStore

    return PostgresRunSnapshotStore(session_factory, payload_parser=payload_parser)
