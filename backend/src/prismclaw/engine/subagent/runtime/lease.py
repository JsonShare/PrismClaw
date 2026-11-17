from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, replace
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class SubagentLease:
    agent_id: str
    run_id: str
    owner_worker_id: str
    owner_token: str
    fence: int
    heartbeat: int


@dataclass(frozen=True, slots=True)
class SubagentActiveRun:
    owner_session_id: str
    agent_id: str
    run_id: str
    heartbeat: int


class SubagentLeaseStore(Protocol):
    async def claim(
        self,
        agent_id: str,
        run_id: str,
        owner_worker_id: str,
        owner_token: str,
        *,
        owner_session_id: str | None = None,
    ) -> SubagentLease | None: ...

    async def validate(self, lease: SubagentLease) -> bool: ...

    async def heartbeat(self, lease: SubagentLease) -> bool: ...

    async def takeover(
        self,
        expired_lease: SubagentLease,
        new_run_id: str,
        new_token: str,
        *,
        new_owner_worker_id: str | None = None,
        owner_session_id: str | None = None,
    ) -> SubagentLease | None: ...

    async def get(self, agent_id: str) -> SubagentLease | None: ...

    async def release(self, lease: SubagentLease) -> bool: ...

    async def list_active(self) -> list[SubagentActiveRun]: ...


def _now_epoch_ms() -> int:
    return int(time.time() * 1000)


def _same_attempt(left: SubagentLease, right: SubagentLease) -> bool:
    return (
        left.agent_id == right.agent_id
        and left.run_id == right.run_id
        and left.owner_worker_id == right.owner_worker_id
        and left.owner_token == right.owner_token
        and left.fence == right.fence
    )


def _parse_lease(raw: Any) -> SubagentLease | None:
    if isinstance(raw, bytes):
        raw = raw.decode()
    if not isinstance(raw, str):
        return None
    try:
        data = json.loads(raw)
        return SubagentLease(
            agent_id=str(data["agent_id"]),
            run_id=str(data["run_id"]),
            owner_worker_id=str(data["owner_worker_id"]),
            owner_token=str(data["owner_token"]),
            fence=int(data["fence"]),
            heartbeat=int(data["heartbeat"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _lease_payload(lease: SubagentLease, owner_session_id: str) -> str:
    return json.dumps(
        {
            "agent_id": lease.agent_id,
            "run_id": lease.run_id,
            "owner_worker_id": lease.owner_worker_id,
            "owner_token": lease.owner_token,
            "fence": lease.fence,
            "heartbeat": lease.heartbeat,
            "owner_session_id": owner_session_id,
        },
        separators=(",", ":"),
    )


def _owner_session_id(raw: Any) -> str | None:
    if isinstance(raw, bytes):
        raw = raw.decode()
    if not isinstance(raw, str):
        return None
    try:
        return str(json.loads(raw).get("owner_session_id") or "")
    except (TypeError, ValueError):
        return None


class LocalSubagentLeaseStore:
    """Process-local lease coordination; it provides no multi-process safety."""

    def __init__(self, *, ttl_seconds: float = 30.0) -> None:
        self._ttl_ms = max(1, int(ttl_seconds * 1000))
        self._leases: dict[str, SubagentLease] = {}
        self._active: dict[str, SubagentLease] = {}
        self._owner_sessions: dict[str, str] = {}
        self._fences: dict[str, int] = {}
        self._lock = asyncio.Lock()

    def _expired(self, lease: SubagentLease, now_ms: int) -> bool:
        return now_ms - lease.heartbeat >= self._ttl_ms

    def _next_fence(self, agent_id: str) -> int:
        fence = self._fences.get(agent_id, 0) + 1
        self._fences[agent_id] = fence
        return fence

    def _active_runs(self) -> list[SubagentActiveRun]:
        records = [
            SubagentActiveRun(
                owner_session_id=self._owner_sessions.get(agent_id, ""),
                agent_id=agent_id,
                run_id=lease.run_id,
                heartbeat=lease.heartbeat,
            )
            for agent_id, lease in self._active.items()
        ]
        records.sort(key=lambda record: record.heartbeat, reverse=True)
        return records

    async def claim(
        self,
        agent_id: str,
        run_id: str,
        owner_worker_id: str,
        owner_token: str,
        *,
        owner_session_id: str | None = None,
    ) -> SubagentLease | None:
        async with self._lock:
            now_ms = _now_epoch_ms()
            existing = self._leases.get(agent_id)
            if existing is not None:
                candidate = SubagentLease(
                    agent_id=agent_id,
                    run_id=run_id,
                    owner_worker_id=owner_worker_id,
                    owner_token=owner_token,
                    fence=existing.fence,
                    heartbeat=now_ms,
                )
                if self._expired(existing, now_ms) or not _same_attempt(existing, candidate):
                    return None
                self._leases[agent_id] = candidate
                self._active[agent_id] = candidate
                if owner_session_id is not None:
                    self._owner_sessions[agent_id] = owner_session_id
                return candidate

            if agent_id in self._active:
                return None
            lease = SubagentLease(
                agent_id=agent_id,
                run_id=run_id,
                owner_worker_id=owner_worker_id,
                owner_token=owner_token,
                fence=self._next_fence(agent_id),
                heartbeat=now_ms,
            )
            self._leases[agent_id] = lease
            self._active[agent_id] = lease
            self._owner_sessions[agent_id] = owner_session_id or ""
            return lease

    async def validate(self, lease: SubagentLease) -> bool:
        async with self._lock:
            current = self._leases.get(lease.agent_id)
            return (
                current is not None
                and _same_attempt(current, lease)
                and not self._expired(current, _now_epoch_ms())
            )

    async def heartbeat(self, lease: SubagentLease) -> bool:
        async with self._lock:
            current = self._leases.get(lease.agent_id)
            now_ms = _now_epoch_ms()
            if (
                current is None
                or not _same_attempt(current, lease)
                or self._expired(current, now_ms)
            ):
                return False
            refreshed = replace(current, heartbeat=now_ms)
            self._leases[lease.agent_id] = refreshed
            self._active[lease.agent_id] = refreshed
            return True

    async def takeover(
        self,
        expired_lease: SubagentLease,
        new_run_id: str,
        new_token: str,
        *,
        new_owner_worker_id: str | None = None,
        owner_session_id: str | None = None,
    ) -> SubagentLease | None:
        async with self._lock:
            agent_id = expired_lease.agent_id
            active = self._active.get(agent_id)
            if active is None or not _same_attempt(active, expired_lease):
                return None
            current = self._leases.get(agent_id)
            now_ms = _now_epoch_ms()
            if current is not None and (
                not _same_attempt(current, expired_lease)
                or not self._expired(current, now_ms)
            ):
                return None
            lease = SubagentLease(
                agent_id=agent_id,
                run_id=new_run_id,
                owner_worker_id=new_owner_worker_id or expired_lease.owner_worker_id,
                owner_token=new_token,
                fence=self._next_fence(agent_id),
                heartbeat=now_ms,
            )
            self._leases[agent_id] = lease
            self._active[agent_id] = lease
            if owner_session_id is not None:
                self._owner_sessions[agent_id] = owner_session_id
            return lease

    async def get(self, agent_id: str) -> SubagentLease | None:
        async with self._lock:
            return self._leases.get(agent_id) or self._active.get(agent_id)

    async def release(self, lease: SubagentLease) -> bool:
        async with self._lock:
            current = self._leases.get(lease.agent_id)
            if current is None:
                current = self._active.get(lease.agent_id)
            if current is None or not _same_attempt(current, lease):
                return False
            self._leases.pop(lease.agent_id, None)
            self._active.pop(lease.agent_id, None)
            self._owner_sessions.pop(lease.agent_id, None)
            return True

    async def list_active(self) -> list[SubagentActiveRun]:
        async with self._lock:
            return self._active_runs()


_CLAIM_SCRIPT = """
local lease_key = KEYS[1]
local fence_key = KEYS[2]
local active_key = KEYS[3]
local active_index_key = KEYS[4]
local agent_id = ARGV[1]
local run_id = ARGV[2]
local owner_worker_id = ARGV[3]
local owner_token = ARGV[4]
local owner_session_id = ARGV[5]
local payload = ARGV[6]
local ttl_ms = tonumber(ARGV[7])
local heartbeat = tonumber(ARGV[8])

local existing = redis.call('get', lease_key)
if existing then
    local decoded = cjson.decode(existing)
    if decoded['agent_id'] ~= agent_id or decoded['run_id'] ~= run_id
        or decoded['owner_worker_id'] ~= owner_worker_id
        or decoded['owner_token'] ~= owner_token then
        return 0
    end
    if owner_session_id ~= '' then
        decoded['owner_session_id'] = owner_session_id
    end
    decoded['heartbeat'] = heartbeat
    payload = cjson.encode(decoded)
    redis.call('set', lease_key, payload, 'PX', ttl_ms)
    redis.call('set', active_key, payload)
    redis.call('zadd', active_index_key, heartbeat, agent_id)
    return tonumber(decoded['fence'])
end

if redis.call('get', active_key) then
    return 0
end

local decoded = cjson.decode(payload)
local fence = redis.call('incr', fence_key)
decoded['fence'] = fence
payload = cjson.encode(decoded)
redis.call('set', lease_key, payload, 'PX', ttl_ms)
redis.call('set', active_key, payload)
redis.call('zadd', active_index_key, heartbeat, agent_id)
return fence
"""

_HEARTBEAT_SCRIPT = """
local lease_key = KEYS[1]
local active_key = KEYS[3]
local active_index_key = KEYS[4]
local agent_id = ARGV[1]
local run_id = ARGV[2]
local owner_worker_id = ARGV[3]
local owner_token = ARGV[4]
local fence = tonumber(ARGV[5])
local ttl_ms = tonumber(ARGV[6])
local heartbeat = tonumber(ARGV[7])

local existing = redis.call('get', lease_key)
if not existing then
    return 0
end
local decoded = cjson.decode(existing)
if decoded['agent_id'] ~= agent_id or decoded['run_id'] ~= run_id
    or decoded['owner_worker_id'] ~= owner_worker_id
    or decoded['owner_token'] ~= owner_token
    or tonumber(decoded['fence']) ~= fence then
    return 0
end
decoded['heartbeat'] = heartbeat
local payload = cjson.encode(decoded)
redis.call('set', lease_key, payload, 'PX', ttl_ms)
redis.call('set', active_key, payload)
redis.call('zadd', active_index_key, heartbeat, agent_id)
return 1
"""

_TAKEOVER_SCRIPT = """
local lease_key = KEYS[1]
local fence_key = KEYS[2]
local active_key = KEYS[3]
local active_index_key = KEYS[4]
local agent_id = ARGV[1]
local old_run_id = ARGV[2]
local old_owner_worker_id = ARGV[3]
local old_owner_token = ARGV[4]
local old_fence = tonumber(ARGV[5])
local new_run_id = ARGV[6]
local new_token = ARGV[7]
local ttl_ms = tonumber(ARGV[8])
local heartbeat = tonumber(ARGV[9])
local new_owner_worker_id = ARGV[10]
local owner_session_id = ARGV[11]

if redis.call('get', lease_key) then
    return 0
end
local active = redis.call('get', active_key)
if not active then
    return 0
end
local decoded = cjson.decode(active)
if decoded['agent_id'] ~= agent_id or decoded['run_id'] ~= old_run_id
    or decoded['owner_worker_id'] ~= old_owner_worker_id
    or decoded['owner_token'] ~= old_owner_token
    or tonumber(decoded['fence']) ~= old_fence then
    return 0
end

local fence = redis.call('incr', fence_key)
decoded['run_id'] = new_run_id
decoded['owner_worker_id'] = new_owner_worker_id
if owner_session_id ~= '' then
    decoded['owner_session_id'] = owner_session_id
end
decoded['owner_token'] = new_token
decoded['fence'] = fence
decoded['heartbeat'] = heartbeat
local payload = cjson.encode(decoded)
redis.call('set', lease_key, payload, 'PX', ttl_ms)
redis.call('set', active_key, payload)
redis.call('zadd', active_index_key, heartbeat, agent_id)
return fence
"""

_RELEASE_SCRIPT = """
local lease_key = KEYS[1]
local active_key = KEYS[3]
local active_index_key = KEYS[4]
local agent_id = ARGV[1]
local run_id = ARGV[2]
local owner_worker_id = ARGV[3]
local owner_token = ARGV[4]
local fence = tonumber(ARGV[5])

local payload = redis.call('get', lease_key)
if not payload then
    payload = redis.call('get', active_key)
end
if not payload then
    return 0
end
local decoded = cjson.decode(payload)
if decoded['agent_id'] ~= agent_id or decoded['run_id'] ~= run_id
    or decoded['owner_worker_id'] ~= owner_worker_id
    or decoded['owner_token'] ~= owner_token
    or tonumber(decoded['fence']) ~= fence then
    return 0
end
redis.call('del', lease_key)
redis.call('del', active_key)
redis.call('zrem', active_index_key, agent_id)
return 1
"""


class RedisSubagentLeaseStore:
    def __init__(
        self,
        redis_client: Any,
        *,
        key_prefix: str = "prismclaw:",
        ttl_seconds: float = 30.0,
    ) -> None:
        self._redis = redis_client
        self._prefix = key_prefix
        self._ttl_ms = max(1, int(ttl_seconds * 1000))

    def _lease_key(self, agent_id: str) -> str:
        return f"{self._prefix}subagent-lease:{agent_id}"

    def _fence_key(self, agent_id: str) -> str:
        return f"{self._prefix}subagent-lease:fence:{agent_id}"

    def _active_key(self, agent_id: str) -> str:
        return f"{self._prefix}subagent-active:{agent_id}"

    def _active_index_key(self) -> str:
        return f"{self._prefix}subagent-active:index"

    def _keys(self, agent_id: str) -> tuple[str, str, str, str]:
        return (
            self._lease_key(agent_id),
            self._fence_key(agent_id),
            self._active_key(agent_id),
            self._active_index_key(),
        )

    async def claim(
        self,
        agent_id: str,
        run_id: str,
        owner_worker_id: str,
        owner_token: str,
        *,
        owner_session_id: str | None = None,
    ) -> SubagentLease | None:
        now_ms = _now_epoch_ms()
        candidate = SubagentLease(
            agent_id=agent_id,
            run_id=run_id,
            owner_worker_id=owner_worker_id,
            owner_token=owner_token,
            fence=0,
            heartbeat=now_ms,
        )
        session_id = owner_session_id or ""
        fence = await self._redis.eval(
            _CLAIM_SCRIPT,
            4,
            *self._keys(agent_id),
            agent_id,
            run_id,
            owner_worker_id,
            owner_token,
            session_id,
            _lease_payload(candidate, session_id),
            self._ttl_ms,
            now_ms,
        )
        if not fence:
            return None
        return replace(candidate, fence=int(fence))

    async def validate(self, lease: SubagentLease) -> bool:
        current = _parse_lease(await self._redis.get(self._lease_key(lease.agent_id)))
        return current is not None and _same_attempt(current, lease)

    async def heartbeat(self, lease: SubagentLease) -> bool:
        result = await self._redis.eval(
            _HEARTBEAT_SCRIPT,
            4,
            *self._keys(lease.agent_id),
            lease.agent_id,
            lease.run_id,
            lease.owner_worker_id,
            lease.owner_token,
            lease.fence,
            self._ttl_ms,
            _now_epoch_ms(),
        )
        return bool(result)

    async def takeover(
        self,
        expired_lease: SubagentLease,
        new_run_id: str,
        new_token: str,
        *,
        new_owner_worker_id: str | None = None,
        owner_session_id: str | None = None,
    ) -> SubagentLease | None:
        now_ms = _now_epoch_ms()
        owner_worker_id = new_owner_worker_id or expired_lease.owner_worker_id
        fence = await self._redis.eval(
            _TAKEOVER_SCRIPT,
            4,
            *self._keys(expired_lease.agent_id),
            expired_lease.agent_id,
            expired_lease.run_id,
            expired_lease.owner_worker_id,
            expired_lease.owner_token,
            expired_lease.fence,
            new_run_id,
            new_token,
            self._ttl_ms,
            now_ms,
            owner_worker_id,
            owner_session_id or "",
        )
        if not fence:
            return None
        return SubagentLease(
            agent_id=expired_lease.agent_id,
            run_id=new_run_id,
            owner_worker_id=owner_worker_id,
            owner_token=new_token,
            fence=int(fence),
            heartbeat=now_ms,
        )

    async def get(self, agent_id: str) -> SubagentLease | None:
        raw = await self._redis.get(self._active_key(agent_id))
        lease = _parse_lease(raw)
        if lease is not None:
            return lease
        return _parse_lease(await self._redis.get(self._lease_key(agent_id)))

    async def release(self, lease: SubagentLease) -> bool:
        result = await self._redis.eval(
            _RELEASE_SCRIPT,
            4,
            *self._keys(lease.agent_id),
            lease.agent_id,
            lease.run_id,
            lease.owner_worker_id,
            lease.owner_token,
            lease.fence,
        )
        return bool(result)

    async def list_active(self) -> list[SubagentActiveRun]:
        agent_ids = await self._redis.zrange(self._active_index_key(), 0, -1)
        records: list[SubagentActiveRun] = []
        for raw_agent_id in agent_ids or []:
            agent_id = (
                raw_agent_id.decode() if isinstance(raw_agent_id, bytes) else str(raw_agent_id)
            )
            raw = await self._redis.get(self._active_key(agent_id))
            lease = _parse_lease(raw)
            owner_session_id = _owner_session_id(raw)
            if lease is None or owner_session_id is None:
                continue
            records.append(
                SubagentActiveRun(
                    owner_session_id=owner_session_id,
                    agent_id=lease.agent_id,
                    run_id=lease.run_id,
                    heartbeat=lease.heartbeat,
                )
            )
        records.sort(key=lambda record: record.heartbeat, reverse=True)
        return records
