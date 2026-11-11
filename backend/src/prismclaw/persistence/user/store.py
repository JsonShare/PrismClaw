from __future__ import annotations

from prismclaw.schema.storage.storage_types import UserStore
from prismclaw.schema.services.auth import UserProfile


def _make_profile(
    channel: str,
    user_id: str,
) -> UserProfile:
    """Construct a UserProfile."""
    return UserProfile(
        channel=channel,  # type: ignore[arg-type]
        user_id=user_id,
    )


class DatabaseUserProfileStore:
    """``UserProfileStore`` backed by the ``pc_users`` table.

    Satisfies the exact protocol consumed by
    ``prismclaw.app.auth.handler_helpers.resolve_profile_and_mode``
    (``get`` / ``set`` / ``list_users`` / ``discard``), replacing the old
    config-list + Redis JSON store. The DB is the single source of truth for
    identity; Redis is no longer needed for user data.
    """

    def __init__(self, user_store: UserStore) -> None:
        self._store = user_store

    async def get(self, channel: str, user_id: str) -> UserProfile:
        user = await self._store.get_by_id(user_id)
        if user is None or user.disabled:
            # Mirror RedisJsonStore: unknown/disabled → default profile.
            return _make_profile(channel, user_id)
        return _make_profile(channel, user.id)

    async def set(self, profile: UserProfile, *, ttl_seconds: int | None = None) -> bool:
        # ``ttl_seconds`` is ignored — the DB is the durable source of truth.
        # Profile carries no mutable fields anymore (role removed, per-user
        # prefs table dropped), so this is effectively a no-op.
        return await self._store.update_profile(profile.user_id)

    async def list_users(self, channel: str) -> list[UserProfile]:
        users = await self._store.list_all()
        return [_make_profile(channel, u.id) for u in users]

    async def discard(self, channel: str, user_id: str) -> bool:
        return await self._store.delete(user_id)
