from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import pytest

from prismclaw.engine.subagent.runtime import lease as lease_module
from prismclaw.engine.subagent.runtime.lease import (
    LocalSubagentLeaseStore,
    RedisSubagentLeaseStore,
    SubagentActiveRun,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 1_700_000_000_000

    def advance(self, milliseconds: int) -> None:
        self.now += milliseconds


class _FakeRedis:
    """Minimal Redis command fake with scripted equivalents for the store's Lua calls."""

    def __init__(self, clock: _Clock) -> None:
        self._clock = clock
        self._values: dict[str, tuple[str, int | None]] = {}
        self._zsets: dict[str, dict[str, float]] = {}
        self.eval_calls: list[tuple[str, int, tuple[Any, ...]]] = []

    def _get(self, key: str) -> str | None:
        entry = self._values.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and expires_at <= self._clock.now:
            self._values.pop(key, None)
            return None
        return value

    def _set(self, key: str, value: Any, px: int | None = None) -> None:
        expires_at = self._clock.now + int(px) if px is not None else None
        self._values[key] = (str(value), expires_at)

    def _incr(self, key: str) -> int:
        value = int(self._get(key) or 0) + 1
        self._set(key, value)
        return value

    def _delete(self, key: str) -> None:
        self._values.pop(key, None)

    def _zadd(self, key: str, score: int, member: str) -> None:
        self._zsets.setdefault(key, {})[member] = float(score)

    def _zrem(self, key: str, member: str) -> None:
        self._zsets.get(key, {}).pop(member, None)

    async def get(self, key: str) -> str | None:
        return self._get(key)

    async def zrange(self, key: str, start: int, stop: int) -> list[str]:
        members = sorted(self._zsets.get(key, {}).items(), key=lambda item: item[1])
        values = [member for member, _ in members]
        if stop == -1:
            stop = len(values) - 1
        return values[start : stop + 1]

    def expires_at(self, key: str) -> int | None:
        entry = self._values.get(key)
        assert entry is not None
        return entry[1]

    async def eval(self, script: str, numkeys: int, *args: Any) -> int:
        self.eval_calls.append((script, numkeys, args))
        assert numkeys == 4
        keys = args[:4]
        argv = args[4:]
        if script == lease_module._CLAIM_SCRIPT:
            return self._claim(keys, argv)
        if script == lease_module._HEARTBEAT_SCRIPT:
            return self._heartbeat(keys, argv)
        if script == lease_module._TAKEOVER_SCRIPT:
            return self._takeover(keys, argv)
        if script == lease_module._RELEASE_SCRIPT:
            return self._release(keys, argv)
        raise AssertionError("unexpected Lua script")

    @staticmethod
    def _matches(data: dict[str, Any], argv: tuple[Any, ...]) -> bool:
        agent_id, run_id, worker_id, token, fence = argv[:5]
        return (
            data["agent_id"] == agent_id
            and data["run_id"] == run_id
            and data["owner_worker_id"] == worker_id
            and data["owner_token"] == token
            and int(data["fence"]) == int(fence)
        )

    def _write_attempt(
        self,
        keys: tuple[Any, ...],
        data: dict[str, Any],
        ttl_ms: int,
        heartbeat: int,
    ) -> None:
        lease_key, _, active_key, index_key = keys
        payload = json.dumps(data)
        self._set(lease_key, payload, ttl_ms)
        self._set(active_key, payload)
        self._zadd(index_key, heartbeat, data["agent_id"])

    def _claim(self, keys: tuple[Any, ...], argv: tuple[Any, ...]) -> int:
        lease_key, fence_key, active_key, _ = keys
        agent_id, run_id, worker_id, token, session_id, payload, ttl_ms, now = argv
        assert lease_key.endswith(f":{agent_id}")
        assert fence_key.endswith(f":fence:{agent_id}")
        existing = self._get(lease_key)
        if existing is not None:
            data = json.loads(existing)
            if not self._matches(data, (agent_id, run_id, worker_id, token, data["fence"])):
                return 0
            if session_id:
                data["owner_session_id"] = session_id
            data["heartbeat"] = now
            self._write_attempt(keys, data, ttl_ms, now)
            return int(data["fence"])
        if self._get(active_key) is not None:
            return 0
        data = json.loads(payload)
        data["fence"] = self._incr(fence_key)
        self._write_attempt(keys, data, ttl_ms, now)
        return int(data["fence"])

    def _heartbeat(self, keys: tuple[Any, ...], argv: tuple[Any, ...]) -> int:
        lease_key = keys[0]
        ttl_ms, now = argv[-2:]
        existing = self._get(lease_key)
        if existing is None:
            return 0
        data = json.loads(existing)
        if not self._matches(data, argv):
            return 0
        data["heartbeat"] = now
        self._write_attempt(keys, data, ttl_ms, now)
        return 1

    def _takeover(self, keys: tuple[Any, ...], argv: tuple[Any, ...]) -> int:
        lease_key, fence_key, active_key, _ = keys
        agent_id, run_id, worker_id, token, fence, new_run_id, new_token, ttl_ms, now = argv[:9]
        new_worker_id = argv[9] if len(argv) > 9 else worker_id
        owner_session_id = argv[10] if len(argv) > 10 else None
        if self._get(lease_key) is not None:
            return 0
        active = self._get(active_key)
        if active is None:
            return 0
        data = json.loads(active)
        if not self._matches(data, (agent_id, run_id, worker_id, token, fence)):
            return 0
        data["run_id"] = new_run_id
        data["owner_worker_id"] = new_worker_id
        if owner_session_id:
            data["owner_session_id"] = owner_session_id
        data["owner_token"] = new_token
        data["fence"] = self._incr(fence_key)
        data["heartbeat"] = now
        self._write_attempt(keys, data, ttl_ms, now)
        return int(data["fence"])

    def _release(self, keys: tuple[Any, ...], argv: tuple[Any, ...]) -> int:
        lease_key, _, active_key, index_key = keys
        payload = self._get(lease_key) or self._get(active_key)
        if payload is None:
            return 0
        data = json.loads(payload)
        if not self._matches(data, argv):
            return 0
        self._delete(lease_key)
        self._delete(active_key)
        self._zrem(index_key, data["agent_id"])
        return 1


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    clock = _Clock()
    monkeypatch.setattr(lease_module, "_now_epoch_ms", lambda: clock.now)
    return clock


@pytest.fixture(params=["local", "redis"])
def store(request: pytest.FixtureRequest, clock: _Clock):
    if request.param == "local":
        return LocalSubagentLeaseStore(ttl_seconds=10)
    return RedisSubagentLeaseStore(_FakeRedis(clock), ttl_seconds=10)


async def test_claim_conflicts_and_refreshes_only_the_same_attempt(store, clock: _Clock) -> None:
    first = await store.claim(
        "agent-a",
        "run-1",
        "worker-1",
        "token-1",
        owner_session_id="session-1",
    )
    assert first is not None
    assert first.fence == 1
    assert await store.claim("agent-a", "run-2", "worker-2", "token-2") is None
    assert await store.claim("agent-a", "run-1", "worker-2", "token-1") is None

    clock.advance(1_000)
    refreshed = await store.claim("agent-a", "run-1", "worker-1", "token-1")
    assert refreshed == replace(first, heartbeat=clock.now)
    assert await store.validate(first)

    for invalid in (
        replace(first, agent_id="agent-b"),
        replace(first, run_id="other-run"),
        replace(first, owner_worker_id="other-worker"),
        replace(first, owner_token="other-token"),
        replace(first, fence=first.fence + 1),
    ):
        assert not await store.validate(invalid)


async def test_heartbeat_checks_identity_and_extends_lease(store, clock: _Clock) -> None:
    lease = await store.claim("agent-a", "run-1", "worker-1", "token-1")
    assert lease is not None
    clock.advance(9_000)
    assert await store.heartbeat(lease)

    for invalid in (
        replace(lease, agent_id="agent-b"),
        replace(lease, run_id="other-run"),
        replace(lease, owner_worker_id="other-worker"),
        replace(lease, owner_token="other-token"),
        replace(lease, fence=lease.fence + 1),
    ):
        assert not await store.heartbeat(invalid)

    clock.advance(9_000)
    assert await store.validate(lease)
    assert (await store.list_active())[0].heartbeat == 1_700_000_009_000


async def test_expired_lease_can_only_be_taken_over_with_a_new_fence(
    store, clock: _Clock
) -> None:
    lease = await store.claim("agent-a", "run-1", "worker-1", "token-1")
    assert lease is not None
    assert await store.takeover(lease, "run-early", "token-early") is None

    clock.advance(10_000)
    assert not await store.validate(lease)
    assert not await store.heartbeat(lease)
    assert await store.claim("agent-a", "run-1", "worker-1", "token-1") is None

    replacement = await store.takeover(lease, "run-2", "token-2")
    assert replacement is not None
    assert replacement.fence == lease.fence + 1
    assert replacement.run_id == "run-2"
    assert replacement.owner_token == "token-2"
    assert replacement.owner_worker_id == lease.owner_worker_id
    assert not await store.validate(lease)
    assert await store.validate(replacement)


async def test_takeover_can_change_worker_and_keep_owner_session(store, clock: _Clock) -> None:
    lease = await store.claim(
        "agent-a",
        "run-1",
        "worker-1",
        "token-1",
        owner_session_id="session-1",
    )
    assert lease is not None
    clock.advance(10_000)

    replacement = await store.takeover(
        lease,
        "run-2",
        "token-2",
        new_owner_worker_id="worker-2",
        owner_session_id="session-1",
    )
    assert replacement is not None
    assert replacement.owner_worker_id == "worker-2"
    assert await store.get("agent-a") == replacement
    assert (await store.list_active())[0].owner_session_id == "session-1"
    assert not await store.heartbeat(lease)
    assert await store.heartbeat(replacement)


async def test_old_owner_cannot_mutate_or_release_the_new_owner(store, clock: _Clock) -> None:
    lease = await store.claim("agent-a", "run-1", "worker-1", "token-1")
    assert lease is not None
    clock.advance(10_000)
    replacement = await store.takeover(lease, "run-2", "token-2")
    assert replacement is not None

    assert not await store.heartbeat(lease)
    assert not await store.release(lease)
    assert await store.takeover(lease, "run-3", "token-3") is None
    assert await store.heartbeat(replacement)
    assert await store.validate(replacement)
    assert [record.run_id for record in await store.list_active()] == ["run-2"]

    assert await store.release(replacement)
    assert await store.list_active() == []
    assert not await store.release(lease)


async def test_different_agents_can_run_in_parallel(store) -> None:
    first, second = await asyncio.gather(
        store.claim("agent-a", "run-a", "worker-a", "token-a"),
        store.claim("agent-b", "run-b", "worker-b", "token-b"),
    )
    assert first is not None and second is not None
    assert first.fence == second.fence == 1
    assert await store.validate(first)
    assert await store.validate(second)
    assert {record.agent_id for record in await store.list_active()} == {"agent-a", "agent-b"}

    assert await store.release(first)
    assert not await store.validate(first)
    assert await store.validate(second)


async def test_active_directory_survives_expiry_and_tracks_takeover(
    store, clock: _Clock
) -> None:
    lease = await store.claim(
        "agent-a",
        "run-1",
        "worker-1",
        "token-1",
        owner_session_id="session-1",
    )
    assert lease is not None
    assert await store.list_active() == [
        SubagentActiveRun(
            owner_session_id="session-1",
            agent_id="agent-a",
            run_id="run-1",
            heartbeat=clock.now,
        )
    ]

    clock.advance(1_000)
    assert await store.heartbeat(lease)
    assert (await store.list_active())[0].heartbeat == clock.now
    clock.advance(10_000)
    assert not await store.validate(lease)
    assert len(await store.list_active()) == 1

    replacement = await store.takeover(lease, "run-2", "token-2")
    assert replacement is not None
    assert await store.list_active() == [
        SubagentActiveRun(
            owner_session_id="session-1",
            agent_id="agent-a",
            run_id="run-2",
            heartbeat=clock.now,
        )
    ]
    assert await store.release(replacement)
    assert await store.list_active() == []


async def test_release_requires_full_identity_and_fence_stays_monotonic(store) -> None:
    lease = await store.claim("agent-a", "run-1", "worker-1", "token-1")
    assert lease is not None
    for invalid in (
        replace(lease, agent_id="agent-b"),
        replace(lease, run_id="other-run"),
        replace(lease, owner_worker_id="other-worker"),
        replace(lease, owner_token="other-token"),
        replace(lease, fence=lease.fence + 1),
    ):
        assert not await store.release(invalid)
    assert len(await store.list_active()) == 1

    assert await store.release(lease)
    assert await store.takeover(lease, "run-late", "token-late") is None
    next_lease = await store.claim("agent-a", "run-2", "worker-1", "token-2")
    assert next_lease is not None
    assert next_lease.fence == lease.fence + 1


async def test_redis_lua_arguments_and_persistent_counter(clock: _Clock) -> None:
    redis = _FakeRedis(clock)
    store = RedisSubagentLeaseStore(redis, key_prefix="test:", ttl_seconds=10)
    lease = await store.claim(
        "agent-a",
        "run-1",
        "worker-1",
        "token-1",
        owner_session_id="session-1",
    )
    assert lease is not None

    script, numkeys, args = redis.eval_calls[0]
    assert script == lease_module._CLAIM_SCRIPT
    assert numkeys == 4
    assert args[:4] == (
        "test:subagent-lease:agent-a",
        "test:subagent-lease:fence:agent-a",
        "test:subagent-active:agent-a",
        "test:subagent-active:index",
    )
    assert args[4:9] == ("agent-a", "run-1", "worker-1", "token-1", "session-1")
    assert args[-2:] == (10_000, clock.now)
    assert redis.expires_at("test:subagent-lease:agent-a") == clock.now + 10_000
    assert redis.expires_at("test:subagent-active:agent-a") is None
    assert redis.expires_at("test:subagent-lease:fence:agent-a") is None

    clock.advance(10_000)
    replacement = await store.takeover(lease, "run-2", "token-2")
    assert replacement is not None
    assert replacement.fence == 2
    assert redis.expires_at("test:subagent-lease:fence:agent-a") is None
