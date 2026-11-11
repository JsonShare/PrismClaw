from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from prismclaw.app.auth.profile import UserProfile
from prismclaw.app.auth.profile_store import RedisJsonStore, UserProfileStore
from prismclaw.app.auth.mode_resolution import resolve_effective_mode
from prismclaw.schema.agent.types import WorkspaceMode

if TYPE_CHECKING:
    from redis.asyncio import Redis

# Registered at startup (see prismclaw.app._lifespan). When set, every
# resolve_profile_and_mode call uses it as the source of truth for identity,
# replacing the legacy config-list + Redis JSON store.
_DEFAULT_USER_PROFILE_STORE: UserProfileStore | None = None


def register_default_user_profile_store(store: UserProfileStore | None) -> None:
    global _DEFAULT_USER_PROFILE_STORE
    _DEFAULT_USER_PROFILE_STORE = store


def _build_default_store(
    *,
    channel: Literal["web"],
    redis_client: "Redis | None",
    user_configs: list[Any],
    open_id_attr: str = "id",
) -> UserProfileStore:
    """Build the config-list fallback store (Redis-first, JSON-file fallback).

    Used when the DB-backed user store is disabled or unreachable, so the web
    channel still boots with identities from ``channels.web.users``. Each
    config entry contributes an identity-only ``UserProfile`` (channel +
    user_id); per-user mode defaults were removed with the admin/role cleanup.
    """
    profiles: list[UserProfile] = []
    for cfg in user_configs or []:
        user_id = (
            getattr(cfg, open_id_attr, None)
            or getattr(cfg, "open_id", None)
            or getattr(cfg, "id", None)
        )
        if not user_id:
            continue
        profiles.append(
            UserProfile(
                channel=channel,
                user_id=str(user_id),
            )
        )
    json_source = {channel: profiles} if profiles else {}
    return RedisJsonStore(redis_client=redis_client, json_source=json_source)


async def resolve_profile_and_mode(
    *,
    channel: Literal["web"],
    user_id: str,
    redis_client: "Redis | None",
    message_mode: WorkspaceMode | None,
    deployment_default: WorkspaceMode,
    store: UserProfileStore | None = None,
) -> tuple[UserProfile, WorkspaceMode]:
    """Look up the user's profile and resolve the effective per-turn mode.

    Returns ``(profile, effective_mode)``. ``effective_mode`` already applies
    the current Web precedence (per-message > deployment).
    Channel handlers SHOULD pass the result to ``RunRequest.user_profile`` and
    ``RunRequest.mode_override`` respectively.
    """
    resolved_store = store or _DEFAULT_USER_PROFILE_STORE
    if resolved_store is None:
        resolved_store = RedisJsonStore(redis_client=redis_client, json_source={})
    profile = await resolved_store.get(channel, user_id)
    effective_mode = resolve_effective_mode(
        message_mode=message_mode,
        deployment_default_mode=deployment_default,
    )
    return profile, effective_mode
