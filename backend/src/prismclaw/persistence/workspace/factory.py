from __future__ import annotations

from typing import Any

from prismclaw.persistence.workspace.base import WorkspaceStore


def create_workspace_store(
    settings: Any,
    redis_client: Any = None,
) -> WorkspaceStore:
    if redis_client is None:
        raise ValueError(
            "Redis is required for workspace store. "
            "Ensure Redis is configured and pass redis_client to create_workspace_store()."
        )
    from prismclaw.persistence.workspace.redis import RedisWorkspaceStore

    return RedisWorkspaceStore(redis_client, key_prefix=settings.redis.key_prefix)
