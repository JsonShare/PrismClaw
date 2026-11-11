"""UserProfileStore implementation — Protocol defined in contracts, Redis impl here."""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Mapping

from prismclaw.schema.services.auth import UserProfile, UserProfileStore  # noqa: F401 (re-exported via app.auth / handler_helpers)

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_KEY_PREFIX = "prismclaw:userprofile"
_DEFAULT_TTL_SECONDS = 30 * 24 * 3600

_SCAN_COUNT_HINT = 2000
"""Redis SCAN cursor batch size for list_users.

The default ``scan_iter`` count is 10 — on remote Redis (~25ms RTT) with a
~7000-key keyspace, this means ~700 round-trips ≈ 13-17 seconds for user
list. count=2000 collapses this to 4 batches ≈ 75ms (184x faster) on
the same dataset, with no correctness change (SCAN cursor semantics are
identical regardless of count, count is only a hint).
"""


def _redis_key(channel: str, user_id: str) -> str:
    return f"{_KEY_PREFIX}:{channel}:{user_id}"


_KNOWN_FIELDS = frozenset({
    "channel",
    "user_id",
})


def _serialize(profile: UserProfile, raw_payload: dict | None = None) -> str:
    payload: dict = dict(raw_payload) if raw_payload else {}
    payload.update({
        "channel": profile.channel,
        "user_id": profile.user_id,
    })
    return json.dumps(payload, ensure_ascii=False)


def _deserialize(raw: bytes | str) -> tuple[UserProfile, dict] | tuple[None, None]:
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        payload = json.loads(text)
    except Exception:
        logger.warning("UserProfile JSON decode failed", exc_info=True)
        return None, None
    if not isinstance(payload, dict):
        return None, None
    channel = payload.get("channel")
    user_id = payload.get("user_id")
    if channel not in ("web",) or not isinstance(user_id, str) or not user_id:
        return None, None
    profile = UserProfile(
        channel=channel,
        user_id=user_id,
    )
    unknown = {k: v for k, v in payload.items() if k not in _KNOWN_FIELDS}
    return profile, unknown


class RedisJsonStore:
    """UserProfileStore impl: Redis-first with JSON-file fallback.

    - ``get``: Redis hit → JSON fallback → default profile (identity only).
    - ``set``: Redis SETEX 30 days (sliding window). JSON file is read-only at runtime.
    - ``list_users``: union of Redis SCAN matches + JSON entries; Redis takes precedence on overlap.
    - ``discard``: Redis DEL only (JSON is operator-controlled).
    """

    def __init__(
        self,
        *,
        redis_client: "Redis | None",
        json_source: Mapping[str, list[UserProfile]] | None = None,
    ) -> None:
        self._redis = redis_client
        self._json_source: Mapping[str, list[UserProfile]] = dict(json_source or {})

    async def get(self, channel: str, user_id: str) -> UserProfile:
        if self._redis is not None and user_id:
            try:
                raw = await self._redis.get(_redis_key(channel, user_id))
            except Exception:
                logger.warning(
                    "UserProfile redis get failed; falling back to JSON",
                    exc_info=True,
                )
                raw = None
            if raw is not None:
                profile, unknown = _deserialize(raw)
                if profile is not None:
                    if unknown:
                        profile = profile.__class__(
                            **{**profile.__dict__, "unknown_fields": unknown}
                        )
                    return profile
        for entry in self._json_source.get(channel, ()):
            if entry.user_id == user_id:
                return entry
        return UserProfile(
            channel=channel,  # type: ignore[arg-type]
            user_id=user_id,
        )

    async def set(
        self, profile: UserProfile, *, ttl_seconds: int | None = None
    ) -> bool:
        if self._redis is None or not profile.user_id:
            return False
        ttl = _DEFAULT_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        try:
            existing_unknown: dict | None = None
            try:
                existing_raw = await self._redis.get(
                    _redis_key(profile.channel, profile.user_id)
                )
                if existing_raw is not None:
                    _, existing_unknown = _deserialize(existing_raw)
            except Exception:
                existing_unknown = None
            merged_unknown: dict = dict(existing_unknown or {})
            if profile.unknown_fields:
                merged_unknown.update(profile.unknown_fields)
            await self._redis.setex(
                _redis_key(profile.channel, profile.user_id),
                ttl,
                _serialize(profile, raw_payload=merged_unknown or None),
            )
            return True
        except Exception:
            logger.warning("UserProfile redis setex failed", exc_info=True)
            return False

    async def list_users(self, channel: str) -> list[UserProfile]:
        seen: dict[str, UserProfile] = {}
        if self._redis is not None:
            pattern = f"{_KEY_PREFIX}:{channel}:*"
            try:
                async for key in self._redis.scan_iter(match=pattern, count=_SCAN_COUNT_HINT):
                    key_str = key.decode("utf-8") if isinstance(key, bytes) else str(key)
                    try:
                        raw = await self._redis.get(key_str)
                    except Exception:
                        continue
                    if raw is None:
                        continue
                    profile, unknown = _deserialize(raw)
                    if profile is None or profile.channel != channel:
                        continue
                    if unknown:
                        from dataclasses import replace
                        profile = replace(profile, unknown_fields=unknown)
                    seen[profile.user_id] = profile
            except Exception:
                logger.warning(
                    "UserProfile redis scan failed; using JSON only", exc_info=True
                )
        for entry in self._json_source.get(channel, ()):
            seen.setdefault(entry.user_id, entry)
        return list(seen.values())

    async def discard(self, channel: str, user_id: str) -> bool:
        if self._redis is None or not user_id:
            return False
        try:
            await self._redis.delete(_redis_key(channel, user_id))
            return True
        except Exception:
            logger.warning("UserProfile redis delete failed", exc_info=True)
            return False
