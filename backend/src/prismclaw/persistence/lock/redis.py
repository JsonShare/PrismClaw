from __future__ import annotations

import secrets

import redis.asyncio as aioredis

from prismclaw.schema.shared.exceptions import LockAcquireError

_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""

_RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
else
    return 0
end
"""


class RedisLockManager:
    def __init__(self, client: aioredis.Redis, *, key_prefix: str = "prismclaw:") -> None:
        self._client = client
        self._prefix = key_prefix

    def _full_key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    async def acquire(self, key: str, ttl_ms: int = 30_000, *, owner: str = "") -> str:
        """Acquire the lock; returns an opaque token used for renew/release.

        ``owner`` embeds a human-readable identity (e.g. worker_id) into the
        lock value: ``"{owner}:{random}"``. The random suffix keeps tokens
        unique per acquire, so CAS semantics are unchanged. Omitting ``owner``
        yields a plain random token (legacy format).
        """
        full = self._full_key(key)
        token = f"{owner}:{secrets.token_hex(8)}" if owner else secrets.token_hex(16)
        ok = await self._client.set(full, token, nx=True, px=ttl_ms)
        if not ok:
            raise LockAcquireError(full)
        return token

    async def owner_of(self, key: str) -> str | None:
        """Return the owner identity embedded in the lock's value, if any.

        - Lock absent (expired/released) -> None
        - Legacy token (plain random, no ``:``) -> None
        - ``"{owner}:{random}"`` -> the ``owner`` part (owner itself may
          contain ``:`` — e.g. ``worker:host:pid:hex`` — so we split from
          the right, where the random hex suffix always sits)
        """
        full = self._full_key(key)
        raw = await self._client.get(full)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        parts = raw.rsplit(":", 1)
        if len(parts) != 2:
            return None
        return parts[0] or None

    async def release(self, key: str, token: str) -> bool:
        full = self._full_key(key)
        result = await self._client.eval(_RELEASE_SCRIPT, 1, full, token)
        return bool(result)

    async def renew(self, key: str, token: str, ttl_ms: int = 30_000) -> bool:
        full = self._full_key(key)
        result = await self._client.eval(_RENEW_SCRIPT, 1, full, token, str(ttl_ms))
        return bool(result)
