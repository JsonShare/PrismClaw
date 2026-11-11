"""T0-1 验收测试（SDD D1）：dream dispatch 消费组 NOGROUP 自愈。

覆盖：
1. NOGROUP 一次 → 自动重建 + 重试成功；
2. 冷却窗口内重复 NOGROUP → 不再重建（防风暴），窗口过后恢复重建；
3. 组在 claim 与 ack 之间丢失 → ack 降级审计、不抛异常。
"""

from __future__ import annotations

from typing import Any

import pytest

from prismclaw.engine.dream.dispatch.dispatch import (
    DreamDispatchJob,
    RedisDreamDispatchQueue,
)


class _RedisError(Exception):
    pass


class _FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class _FakePipeline:
    def __init__(self, parent: "_FakeRedis") -> None:
        self._parent = parent

    def xack(self, *args: Any) -> "_FakePipeline":
        return self

    def xdel(self, *args: Any) -> "_FakePipeline":
        return self

    def delete(self, *args: Any) -> "_FakePipeline":
        return self

    async def execute(self) -> None:
        if not self._parent.group_exists:
            raise _RedisError("NOGROUP No such key 'stream' or consumer group 'dream-dispatch'")


class _FakeRedis:
    def __init__(self, clock: _FakeClock) -> None:
        self.group_exists = False
        self.stream_len = 5
        self.xgroup_create_calls = 0
        self.audit_events: list[dict[str, Any]] = []
        self._clock = clock

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    async def xgroup_create(self, key: str, group: str, id: Any = None, mkstream: bool = False) -> bool:
        if self.group_exists:
            raise _RedisError("BUSYGROUP Consumer Group name already exists")
        self.group_exists = True
        self.xgroup_create_calls += 1
        return True

    async def xreadgroup(self, group: str, consumer: str, streams: Any, count: int = 10, block: int = 0) -> list[Any]:
        if not self.group_exists:
            raise _RedisError(
                "NOGROUP No such key 'stream' or consumer group 'dream-dispatch'"
            )
        return []

    async def xautoclaim(self, *args: Any, **kwargs: Any) -> list[Any]:
        if not self.group_exists:
            raise _RedisError(
                "NOGROUP No such key 'stream' or consumer group 'dream-dispatch'"
            )
        return [[], []]

    async def xlen(self, key: str) -> int:
        return self.stream_len

    async def xadd(self, key: str, fields: dict[str, Any], maxlen: int = 0, approximate: bool = False) -> str:
        if key.endswith("audit"):
            self.audit_events.append(dict(fields))
        return "1-0"


def _queue(redis: _FakeRedis, clock: _FakeClock) -> RedisDreamDispatchQueue:
    return RedisDreamDispatchQueue(redis, time_fn=clock)


@pytest.mark.asyncio
async def test_nogroup_rebuilds_group_and_retries() -> None:
    clock = _FakeClock()
    redis = _FakeRedis(clock)
    queue = _queue(redis, clock)

    # 首次读取建组
    assert await queue.read("consumer") == []
    assert redis.xgroup_create_calls == 1

    # 组被销毁 → 自愈重建 + 重试成功
    redis.group_exists = False
    assert await queue.read("consumer") == []
    assert redis.xgroup_create_calls == 2
    assert redis.group_exists is True
    rebuild_events = [e for e in redis.audit_events if e.get("event") == "group_rebuilt"]
    assert len(rebuild_events) == 1
    assert rebuild_events[0]["stream_len"] == "5"


@pytest.mark.asyncio
async def test_rebuild_cooldown_suppresses_storm() -> None:
    clock = _FakeClock()
    redis = _FakeRedis(clock)
    queue = _queue(redis, clock)

    await queue.read("consumer")  # 首次建组
    redis.group_exists = False
    assert await queue.read("consumer") == []  # 触发重建（t=0）
    assert redis.xgroup_create_calls == 2

    # 冷却窗口内（+10s）再次 NOGROUP → 不重建
    redis.group_exists = False
    clock.t = 10.0
    assert await queue.read("consumer") == []
    assert redis.xgroup_create_calls == 2

    # 冷却过后（+40s）→ 恢复重建
    redis.group_exists = False
    clock.t = 40.0
    assert await queue.read("consumer") == []
    assert redis.xgroup_create_calls == 3


@pytest.mark.asyncio
async def test_ack_tolerates_group_loss() -> None:
    clock = _FakeClock()
    redis = _FakeRedis(clock)
    queue = _queue(redis, clock)

    job = DreamDispatchJob(
        stream_id="1-0", session_key="s1", job_kind="light", enqueued_at=0.0
    )
    redis.group_exists = False  # claim 之后组被销毁
    await queue.ack(job)  # 必须不抛
    events = [e.get("event") for e in redis.audit_events]
    assert "ack_group_missing" in events