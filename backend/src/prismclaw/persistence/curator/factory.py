from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from prismclaw.schema.agent.curator import CuratorSopRepository


def create_curator_repository(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    graduator: Callable | None = None,
) -> CuratorSopRepository:
    from prismclaw.persistence.curator.repository import PostgresCuratorSopRepository
    return PostgresCuratorSopRepository(session_factory, graduator=graduator)
