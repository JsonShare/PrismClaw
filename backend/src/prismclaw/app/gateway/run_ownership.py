from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OwnershipRecord:
    """Minimal ownership record — zero prismclaw internal dependencies."""

    run_id: str
    session_id: str | None = None
    owner_worker_id: str = ""
    snapshot_cursor: str = ""
    last_safe_boundary: str = ""
    heartbeat_at_epoch_ms: int = 0


def parse_ownership_record(data: dict[str, Any]) -> OwnershipRecord | None:
    """Deserialize a dict (from Redis JSON) into OwnershipRecord."""
    try:
        run_id = data.get("run_id") or ""
        if not run_id:
            return None
        lsb = data.get("last_safe_boundary", "")
        # Handle enum-style values: extract .value if present
        if hasattr(lsb, "value"):
            lsb = lsb.value
        return OwnershipRecord(
            run_id=str(run_id),
            session_id=data.get("session_id"),
            owner_worker_id=str(data.get("owner_worker_id", "")),
            snapshot_cursor=str(data.get("snapshot_cursor", "")),
            last_safe_boundary=str(lsb),
            heartbeat_at_epoch_ms=int(data.get("heartbeat_at_epoch_ms", 0)),
        )
    except Exception:
        logger.warning("failed to parse ownership record", exc_info=True)
        return None


_CLAIM_OR_REFRESH_SCRIPT = """
local key = KEYS[1]
local index_key = KEYS[2]
local run_id = ARGV[1]
local owner_worker_id = ARGV[2]
local payload = ARGV[3]
local ttl_seconds = tonumber(ARGV[4])
local heartbeat_at_ms = tonumber(ARGV[5])

local existing = redis.call('get', key)
if not existing then
    redis.call('set', key, payload, 'EX', ttl_seconds)
    redis.call('zadd', index_key, heartbeat_at_ms, run_id)
    return 1
end

local decoded = cjson.decode(existing)
if decoded['owner_worker_id'] == owner_worker_id then
    redis.call('set', key, payload, 'EX', ttl_seconds)
    redis.call('zadd', index_key, heartbeat_at_ms, run_id)
    return 1
end

return 0
"""

_RELEASE_SCRIPT = """
local key = KEYS[1]
local index_key = KEYS[2]
local run_id = ARGV[1]
local owner_worker_id = ARGV[2]

local existing = redis.call('get', key)
if not existing then
    redis.call('zrem', index_key, run_id)
    return 1
end

local decoded = cjson.decode(existing)
if decoded['owner_worker_id'] == owner_worker_id then
    redis.call('del', key)
    redis.call('zrem', index_key, run_id)
    return 1
end

return 0
"""

_TAKEOVER_SCRIPT = """
local key = KEYS[1]
local index_key = KEYS[2]
local run_id = ARGV[1]
local expected_owner_worker_id = ARGV[2]
local payload = ARGV[3]
local ttl_seconds = tonumber(ARGV[4])
local heartbeat_at_ms = tonumber(ARGV[5])

local existing = redis.call('get', key)
if not existing then
    return 0
end

local decoded = cjson.decode(existing)
if decoded['owner_worker_id'] ~= expected_owner_worker_id then
    return 0
end

redis.call('set', key, payload, 'EX', ttl_seconds)
redis.call('zadd', index_key, heartbeat_at_ms, run_id)
return 1
"""


def _record_to_payload(record: OwnershipRecord) -> str:
    """Serialize OwnershipRecord to JSON for Redis storage."""
    return json.dumps({
        "run_id": record.run_id,
        "session_id": record.session_id,
        "owner_worker_id": record.owner_worker_id,
        "snapshot_cursor": record.snapshot_cursor,
        "last_safe_boundary": record.last_safe_boundary,
        "heartbeat_at_epoch_ms": record.heartbeat_at_epoch_ms,
    })


class RunOwnershipStore:
    def __init__(
        self,
        redis_client: Any,
        *,
        key_prefix: str = "prismclaw:",
        ttl_seconds: int = 90,
        heartbeat_interval_seconds: int = 30,
    ) -> None:
        self._redis = redis_client
        self._prefix = key_prefix
        self._ttl_seconds = max(1, int(ttl_seconds))
        self._heartbeat_interval_seconds = max(1, int(heartbeat_interval_seconds))

    @property
    def available(self) -> bool:
        return self._redis is not None

    @property
    def heartbeat_interval_seconds(self) -> int:
        return self._heartbeat_interval_seconds

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    def _run_key(self, run_id: str) -> str:
        return f"{self._prefix}run-ownership:{run_id}"

    def _index_key(self) -> str:
        return f"{self._prefix}run-ownership:index"

    def make_record(
        self,
        *,
        run_id: str,
        session_id: str | None = None,
        owner_worker_id: str = "",
        snapshot_cursor: str = "",
        last_safe_boundary: str = "",
    ) -> OwnershipRecord | None:
        """Build an OwnershipRecord from decomposed fields.

        Returns None if run_id or owner_worker_id is empty.
        """
        if not run_id or not owner_worker_id:
            return None
        return OwnershipRecord(
            run_id=run_id,
            session_id=session_id,
            owner_worker_id=owner_worker_id,
            snapshot_cursor=snapshot_cursor,
            last_safe_boundary=last_safe_boundary,
            heartbeat_at_epoch_ms=int(time.time() * 1000),
        )

    async def claim(self, record: OwnershipRecord) -> bool:
        if record is None or not self.available:
            return False
        payload = _record_to_payload(record)
        result = await self._redis.eval(
            _CLAIM_OR_REFRESH_SCRIPT,
            2,
            self._run_key(record.run_id),
            self._index_key(),
            record.run_id,
            record.owner_worker_id,
            payload,
            self._ttl_seconds,
            record.heartbeat_at_epoch_ms,
        )
        return bool(result)

    async def heartbeat(self, record: OwnershipRecord) -> bool:
        return await self.claim(record)

    async def release(self, run_id: str, owner_worker_id: str) -> bool:
        if not run_id or not owner_worker_id or not self.available:
            return False
        result = await self._redis.eval(
            _RELEASE_SCRIPT,
            2,
            self._run_key(run_id),
            self._index_key(),
            run_id,
            owner_worker_id,
        )
        return bool(result)

    async def takeover(self, record: OwnershipRecord, *, expected_owner_worker_id: str) -> bool:
        if record is None or not self.available or not expected_owner_worker_id:
            return False
        payload = _record_to_payload(record)
        result = await self._redis.eval(
            _TAKEOVER_SCRIPT,
            2,
            self._run_key(record.run_id),
            self._index_key(),
            record.run_id,
            expected_owner_worker_id,
            payload,
            self._ttl_seconds,
            record.heartbeat_at_epoch_ms,
        )
        return bool(result)

    async def get(self, run_id: str) -> OwnershipRecord | None:
        if not run_id or not self.available:
            return None
        raw = await self._redis.get(self._run_key(run_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        try:
            data = json.loads(raw)
            return parse_ownership_record(data)
        except Exception:
            logger.warning("failed to decode run ownership record run_id=%s", run_id, exc_info=True)
            return None

    async def list_active(self, *, max_age_seconds: float | None = None) -> list[OwnershipRecord]:
        if not self.available:
            return []
        now_ms = int(time.time() * 1000)
        max_age_ms = int((max_age_seconds if max_age_seconds is not None else self._ttl_seconds) * 1000)
        cutoff_ms = now_ms - max(1000, max_age_ms)
        try:
            await self._redis.zremrangebyscore(self._index_key(), "-inf", cutoff_ms - (self._ttl_seconds * 1000))
        except Exception:
            logger.debug("run ownership lazy prune failed", exc_info=True)
        run_ids = await self._redis.zrangebyscore(self._index_key(), cutoff_ms, "+inf")
        records: list[OwnershipRecord] = []
        for raw_run_id in run_ids or []:
            run_id = raw_run_id.decode() if isinstance(raw_run_id, bytes) else str(raw_run_id)
            record = await self.get(run_id)
            if record is None:
                continue
            if record.heartbeat_at_epoch_ms >= cutoff_ms:
                records.append(record)
        records.sort(key=lambda item: item.heartbeat_at_epoch_ms, reverse=True)
        return records
