from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from prismclaw.schema.storage.storage_types import UserStore


def create_user_store(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    lockout_threshold: int = 5,
    lockout_window_seconds: int = 900,
    scheme: str = "scrypt",
) -> UserStore:
    from prismclaw.persistence.user.postgres import PostgresUserRepository
    return PostgresUserRepository(
        session_factory,
        lockout_threshold=lockout_threshold,
        lockout_window_seconds=lockout_window_seconds,
        scheme=scheme,
    )


def create_user_profile_store(user_store: UserStore | Any) -> Any:
    """Create a ``DatabaseUserProfileStore`` wrapping a ``UserStore``."""
    from prismclaw.persistence.user.store import DatabaseUserProfileStore
    return DatabaseUserProfileStore(user_store)
