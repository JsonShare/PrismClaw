from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prismclaw.schema.scheduler.scheduler import SchedulerStoreProtocol


def create_scheduler_store(
    session_factory: async_sessionmaker[AsyncSession],
) -> SchedulerStoreProtocol:
    from prismclaw.persistence.scheduler.store import PostgresSchedulerStore

    return PostgresSchedulerStore(session_factory)
