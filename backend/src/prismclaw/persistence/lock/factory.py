from __future__ import annotations

from typing import Any

from prismclaw.schema.persistence.persistence import LockManager


def create_lock_manager(
    redis_client: Any,
    *,
    key_prefix: str = "prismclaw:",
) -> LockManager:
    from prismclaw.persistence.lock.redis import RedisLockManager

    return RedisLockManager(redis_client, key_prefix=key_prefix)
