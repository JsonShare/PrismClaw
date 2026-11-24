from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prismclaw.schema.agent.curator import CuratorSopRepository


def create_curator_repository(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    graduator: Callable[..., tuple[bool, str | None]] | None = None,
) -> CuratorSopRepository:
    from prismclaw.persistence.curator.repository import PostgresCuratorSopRepository

    return PostgresCuratorSopRepository(session_factory, graduator=graduator)
