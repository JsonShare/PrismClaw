from __future__ import annotations

from typing import TYPE_CHECKING

from prismclaw.persistence.workspace.base import WorkspaceStore

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from prismclaw.infra.settings import Settings


def create_workspace_store(
    settings: Settings,
    redis_client: aioredis.Redis | None = None,
) -> WorkspaceStore:
    if redis_client is None:
        raise ValueError(
            "Redis is required for workspace store. "
            "Ensure Redis is configured and pass redis_client to create_workspace_store()."
        )
    from prismclaw.persistence.workspace.redis import RedisWorkspaceStore

    return RedisWorkspaceStore(redis_client, key_prefix=settings.redis.key_prefix)
