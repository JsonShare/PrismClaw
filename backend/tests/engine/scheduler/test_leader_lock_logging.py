"""leader 锁身份嵌入与抢锁失败日志契约。

多 worker 下 N-1 个 follower 每秒抢锁失败是设计内常态：
- 日志级别必须是 INFO（不是 WARNING），并携带当前持锁者（holder）；
- 发射是变化驱动的：同一持锁者只记一次，翻转/复得后重新记录；
- 锁 value 嵌入 owner 身份（"{owner}:{random}"），CAS 语义不变。
"""
from __future__ import annotations

import logging
import re
from types import SimpleNamespace

import pytest

from prismclaw.engine.scheduler.engine import SchedulerEngine
from prismclaw.persistence.lock.redis import RedisLockManager
from prismclaw.schema.shared.exceptions import LockAcquireError

_OWNER_A = "worker:host-a:1111:aa"
_OWNER_B = "worker:host-b:2222:bb"


# ── RedisLockManager：owner 嵌入与解析 ──────────────────────────────────


class _FakeRedis:
    """实现 RedisLockManager 用到的最小命令面（set/get/eval Lua 语义）。"""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def set(self, key: str, value: str, nx: bool = False, px: int | None = None):
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    async def get(self, key: str):
        return self.data.get(key)

    async def eval(self, script: str, _numkeys: int, *args):
        key, token = args[0], args[1]
        if self.data.get(key) != token:
            return 0
        if "del" in script:
            del self.data[key]
        return 1


def _mgr(fake: _FakeRedis) -> RedisLockManager:
    return RedisLockManager(fake)


async def test_acquire_with_owner_embeds_identity_in_token() -> None:
    fake = _FakeRedis()
    token = await _mgr(fake).acquire("k", ttl_ms=5000, owner=_OWNER_A)
    assert token.startswith(f"{_OWNER_A}:")
    suffix = token.rsplit(":", 1)[1]
    assert re.fullmatch(r"[0-9a-f]{16}", suffix) is not None


async def test_acquire_without_owner_keeps_legacy_token() -> None:
    fake = _FakeRedis()
    token = await _mgr(fake).acquire("k", ttl_ms=5000)
    assert re.fullmatch(r"[0-9a-f]{32}", token) is not None


async def test_owner_of_parses_multicolon_owner() -> None:
    fake = _FakeRedis()
    mgr = _mgr(fake)
    await mgr.acquire("k", ttl_ms=5000, owner=_OWNER_A)
    assert await mgr.owner_of("k") == _OWNER_A


async def test_owner_of_legacy_token_and_absent_lock_return_none() -> None:
    fake = _FakeRedis()
    mgr = _mgr(fake)
    assert await mgr.owner_of("k") is None  # 无锁
    fake.data["prismclaw:k"] = "a" * 32  # 旧格式纯随机 token
    assert await mgr.owner_of("k") is None


async def test_renew_and_release_cas_with_owner_token() -> None:
    fake = _FakeRedis()
    mgr = _mgr(fake)
    token = await mgr.acquire("k", ttl_ms=5000, owner=_OWNER_A)
    assert await mgr.renew("k", token, ttl_ms=5000) is True
    assert await mgr.renew("k", "wrong-token", ttl_ms=5000) is False
    assert await mgr.release("k", token) is True
    assert "prismclaw:k" not in fake.data
    assert await mgr.release("k", token) is False


async def test_second_acquire_fails_with_lock_acquire_error() -> None:
    fake = _FakeRedis()
    mgr = _mgr(fake)
    await mgr.acquire("k", ttl_ms=5000, owner=_OWNER_A)
    with pytest.raises(LockAcquireError):
        await mgr.acquire("k", ttl_ms=5000, owner=_OWNER_B)


# ── SchedulerEngine：失败日志的级别 / 内容 / 变化驱动 ────────────────────


class _FakeLock:
    """可编程锁：fail=True 时 acquire 抛错，holder 是 owner_of 的返回值。"""

    def __init__(self, holder: str | None, *, fail: bool = True) -> None:
        self.holder = holder
        self.fail = fail
        self.owner_of_calls = 0

    async def acquire(self, key: str, ttl_ms: int = 30_000, *, owner: str = "") -> str:
        if self.fail:
            raise LockAcquireError(key)
        return f"{owner}:" + "a" * 16

    async def owner_of(self, key: str) -> str | None:
        self.owner_of_calls += 1
        return self.holder

    async def renew(self, key: str, token: str, ttl_ms: int = 30_000) -> bool:
        return True

    async def release(self, key: str, token: str) -> bool:
        return True


class _BoomOwnerLock(_FakeLock):
    async def owner_of(self, key: str) -> str | None:
        self.owner_of_calls += 1
        raise RuntimeError("redis read failed")


def _engine(lock: _FakeLock) -> SchedulerEngine:
    settings = SimpleNamespace(
        leader_lock_key="prismclaw:scheduler:leader",
        leader_lock_ttl_ms=5000,
        tick_interval_seconds=1.0,
    )

    async def _enqueue(_job) -> None:
        return None

    return SchedulerEngine(
        store=SimpleNamespace(),
        dispatch_enqueue=_enqueue,
        settings=settings,
        lock_manager=lock,
        worker_id=_OWNER_A,
    )


def _failed_records(caplog: pytest.LogCaptureFixture):
    return [
        r
        for r in caplog.records
        if r.getMessage() == "scheduler.lock.acquire_failed"
    ]


async def test_acquire_failed_logs_info_with_holder_and_worker(
    caplog: pytest.LogCaptureFixture,
) -> None:
    eng = _engine(_FakeLock(holder=_OWNER_B))
    with caplog.at_level(logging.INFO, logger="prismclaw.engine.scheduler.engine"):
        assert await eng._ensure_leader() is False
    (record,) = _failed_records(caplog)
    assert record.levelno == logging.INFO  # 常态，不是告警
    assert record.holder == _OWNER_B
    assert record.worker == _OWNER_A


async def test_same_holder_logs_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    eng = _engine(_FakeLock(holder=_OWNER_B))
    with caplog.at_level(logging.INFO, logger="prismclaw.engine.scheduler.engine"):
        for _ in range(5):
            assert await eng._ensure_leader() is False
    assert len(_failed_records(caplog)) == 1


async def test_holder_change_logs_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    lock = _FakeLock(holder=_OWNER_B)
    eng = _engine(lock)
    with caplog.at_level(logging.INFO, logger="prismclaw.engine.scheduler.engine"):
        await eng._ensure_leader()
        lock.holder = "worker:host-c:3333:cc"  # leader 翻转
        await eng._ensure_leader()
    records = _failed_records(caplog)
    assert [r.holder for r in records] == [_OWNER_B, "worker:host-c:3333:cc"]


async def test_regaining_leadership_arms_reporting_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    lock = _FakeLock(holder=_OWNER_B)
    eng = _engine(lock)
    with caplog.at_level(logging.INFO, logger="prismclaw.engine.scheduler.engine"):
        await eng._ensure_leader()  # 失败（holder=B）
        lock.fail = False
        assert await eng._ensure_leader() is True  # 本进程成为 leader
        assert eng._lock_token is not None
        lock.fail = True
        eng._lock_token = None  # 模拟 heartbeat_lost 后丢锁
        await eng._ensure_leader()  # 同一 holder=B 再次失败 → 重新记录
    records = _failed_records(caplog)
    assert [r.holder for r in records] == [_OWNER_B, _OWNER_B]
    acquired = [
        r for r in caplog.records if r.getMessage() == "scheduler.lock.acquired"
    ]
    assert len(acquired) == 1
    assert acquired[0].worker == _OWNER_A


async def test_holder_lookup_error_reports_unknown_and_does_not_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    eng = _engine(_BoomOwnerLock(holder=None))
    with caplog.at_level(logging.INFO, logger="prismclaw.engine.scheduler.engine"):
        assert await eng._ensure_leader() is False  # 不因读锁异常崩溃
    (record,) = _failed_records(caplog)
    assert record.levelno == logging.INFO
    assert record.holder == "unknown"