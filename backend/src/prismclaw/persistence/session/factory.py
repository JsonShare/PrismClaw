from __future__ import annotations

from typing import Any

from prismclaw.schema.persistence.persistence import LockManager
from prismclaw.schema.storage.storage_types import SessionStore


def create_session_store(
    redis_client: Any,
    lock_manager: LockManager,
    *,
    ttl_seconds: int = 604_800,
) -> SessionStore:
    from prismclaw.persistence.session.redis import RedisSessionStore

    store = RedisSessionStore(
        redis_client,
        lock_manager,
        ttl_seconds=ttl_seconds,
    )
    return store
