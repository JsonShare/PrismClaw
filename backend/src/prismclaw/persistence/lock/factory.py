from __future__ import annotations

from typing import TYPE_CHECKING

from prismclaw.schema.storage.persistence_types import LockManager

if TYPE_CHECKING:
    import redis.asyncio as aioredis


def create_lock_manager(
    redis_client: aioredis.Redis,
    *,
    key_prefix: str = "prismclaw:",
) -> LockManager:
    from prismclaw.persistence.lock.redis import RedisLockManager

    return RedisLockManager(redis_client, key_prefix=key_prefix)
