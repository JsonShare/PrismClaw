from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prismclaw.schema.storage.persistence_types import RunSnapshotStore

if TYPE_CHECKING:
    from prismclaw.schema.storage.persistence_types import RunSnapshotLike


def create_run_snapshot_store(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    payload_parser: Callable[[str], RunSnapshotLike | None] | None = None,
) -> RunSnapshotStore:
    from prismclaw.persistence.pg_checkpoint.store import PostgresRunSnapshotStore

    return PostgresRunSnapshotStore(session_factory, payload_parser=payload_parser)
