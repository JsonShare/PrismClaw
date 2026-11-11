"""定时任务调度状态机结构性护栏。

固化 docs/architecture/定时任务调度状态机_架构重构方案.md 第 8 节验收：
范式降级后「读后即 ACK / 无租约 / 无执行超时 / 触发幂等」这些契约即使被删掉
也不会有运行时报错，因此用真实源码文本 + 最小运行时探针把它们钉住。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import pytest
from croniter import croniter

from prismclaw.engine.scheduler.dispatch import RedisSchedulerDispatchQueue
from prismclaw.engine.scheduler.engine import SchedulerEngine
from prismclaw.engine.scheduler.executor import ScheduledRunExecutor
from prismclaw.persistence.session.base import _generate_session_id
from prismclaw.schema.scheduler import (
    RunStatus,
    ScheduledTask,
    ScheduledTaskRun,
    SchedulerDispatchJob,
    TaskStatus,
    make_run_id,
)

_BACKEND = Path(__file__).resolve().parents[3]
_SCHED_SRC = _BACKEND / "src/prismclaw/engine/scheduler"
_STORE_SRC = _BACKEND / "src/prismclaw/persistence/scheduler/store.py"
_FIELDS = {
    "run_id": "r1",
    "task_id": "t1",
    "task_name": "每日AI新闻整理",
    "instruction": "整理今日AI新闻",
    "user_id": "u1",
    "workspace_id": "default",
    "fired_at": "1.0",
}
_SETTINGS = SimpleNamespace(jitter_fraction=0.1, jitter_cap_seconds=60)
_FIRE_SLOT = 1788055200.0


def _task() -> ScheduledTask:
    return ScheduledTask(
        id="t1",
        user_id="u1",
        workspace_id="default",
        name="每日AI新闻整理",
        instruction="整理今日AI新闻",
        cron_expr="0 10 * * *",
        created_at=1788000000.0,
    )


async def _noop_enqueue(_job: SchedulerDispatchJob) -> None:
    return None


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class _InMemoryStore:
    """以主键冲突语义复刻 add_run 的 ON CONFLICT DO NOTHING。"""

    def __init__(self) -> None:
        self.runs: dict[str, dict] = {}

    async def add_run(
        self, run: ScheduledTaskRun, *, fired_task_id: str, fired_at: float
    ) -> bool:
        if run.id in self.runs:
            return False
        self.runs[run.id] = {"status": "pending"}
        return True

    async def update_run(self, run_id: str, **fields: object) -> None:
        self.runs.setdefault(run_id, {}).update(fields)


class _FakeRedis:
    """infra/redis.py 用 decode_responses=True，所以 XREADGROUP 返回 str 键/值。"""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.reads = 0

    async def xgroup_create(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def xadd(self, *_args: object, **_kwargs: object) -> str:
        return "1-0"

    async def xack(self, *_args: object, **_kwargs: object) -> None:
        self.events.append("xack")

    async def xreadgroup(self, *_args: object, **_kwargs: object) -> list:
        self.reads += 1
        if self.reads == 1:
            return [["stream", [("1-0", dict(_FIELDS))]]]
        await asyncio.sleep(0.02)
        raise asyncio.CancelledError()


class _ExecutorStore:
    def __init__(self) -> None:
        self.runs: dict[str, dict] = {}

    async def update_run(self, run_id: str, **fields: object) -> None:
        self.runs.setdefault(run_id, {}).update(fields)

    async def update_status(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def get_task(self, _task_id: str) -> SimpleNamespace:
        return SimpleNamespace(consecutive_failures=0, enabled=True)

    async def put_task(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def set_conversation(self, *_args: object, **_kwargs: object) -> None:
        return None


class _FakeSessionStore:
    async def create_new_session(
        self,
        *,
        session_key: str,
        workspace_id: str,
        agent_id: str,
        title: str,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            header=SimpleNamespace(id=_generate_session_id(session_key))
        )


def _engine(store: object, enqueue: object) -> SchedulerEngine:
    return SchedulerEngine(store=store, dispatch_enqueue=enqueue, settings=_SETTINGS)


# ── 结构性护栏：被删掉不会报错的契约 ────────────────────────────────────


def test_executor_source_has_no_lease_or_timeout() -> None:
    src = _read(_SCHED_SRC / "executor.py")
    for forbidden in (
        "wait_for",
        "set(nx=True",
        "execution_timeout_seconds",
        "get_run(",
        "xautoclaim",
        '"timeout"',
        "contextlib",
    ):
        assert forbidden not in src, f"executor.py still contains {forbidden!r}"


def test_dispatch_source_has_no_work_queue_mechanism() -> None:
    src = _read(_SCHED_SRC / "dispatch.py")
    for forbidden in (
        "xautoclaim",
        "dead-letter",
        "_reclaim_stale",
        "_handle_failure",
        "attempt_count",
        "stream_id",
        "max_retries",
        "dedupe_ttl_seconds",
        "nx=True",
    ):
        assert forbidden not in src, f"dispatch.py still contains {forbidden!r}"


def test_add_run_is_the_single_idempotency_gate() -> None:
    src = _read(_STORE_SRC)
    assert "async def add_run(" in src
    assert "on_conflict_do_nothing" in src


def test_run_status_enums_have_no_timeout() -> None:
    assert "timeout" not in get_args(RunStatus)
    assert "timeout" not in get_args(TaskStatus)


def test_make_run_id_is_deterministic() -> None:
    assert make_run_id("t1", 1000.0) == make_run_id("t1", 1000.0)
    assert make_run_id("t1", 1000.0) != make_run_id("t1", 1001.0)
    assert len(make_run_id("t1", 1000.0)) == 16


def test_cron_fire_slot_is_raw_timestamp_not_jittered() -> None:
    """_tick 与 _recover_missed 必须对同一 fire 算出同一 id。"""
    task = _task()
    eng = _engine(_InMemoryStore(), _noop_enqueue)
    raw = eng._cron_fire_timestamp(task)
    anchor = task.last_fired_at or task.created_at
    recovered = croniter(
        task.cron_expr, datetime.fromtimestamp(anchor).astimezone()
    ).get_next(datetime).timestamp()
    jittered = eng._compute_next_fire(task)
    assert raw == recovered
    assert raw != jittered
    assert make_run_id(task.id, raw) == make_run_id(task.id, recovered)


# ── 运行时护栏 ──────────────────────────────────────────────────────────


async def test_same_fire_slot_produces_one_run_and_one_enqueue() -> None:
    store = _InMemoryStore()
    enqueued: list[str] = []

    async def enqueue(job: SchedulerDispatchJob) -> None:
        enqueued.append(job.run_id)

    eng = _engine(store, enqueue)
    await eng._dispatch(_task(), 1788000001.0, _FIRE_SLOT)
    await eng._dispatch(_task(), 1788000002.0, _FIRE_SLOT)
    assert len(store.runs) == 1
    assert enqueued == list(store.runs)


async def test_enqueue_failure_marks_run_failed() -> None:
    store = _InMemoryStore()

    async def boom(_job: SchedulerDispatchJob) -> None:
        raise RuntimeError("redis down")

    eng = _engine(store, boom)
    await eng._dispatch(_task(), 1788000001.0, _FIRE_SLOT)
    (run,) = store.runs.values()
    assert run["status"] == "failed"
    assert run["error"] == "dispatch_enqueue_failed"


async def test_dispatch_acks_before_handler_runs() -> None:
    redis = _FakeRedis()

    async def handler(_job: SchedulerDispatchJob) -> None:
        redis.events.append("handler")

    queue = RedisSchedulerDispatchQueue(redis)
    with contextlib.suppress(asyncio.CancelledError):
        await queue.consume(handler, block_ms=0)
    await asyncio.sleep(0)
    assert redis.events == ["xack", "handler"]


async def test_dispatch_logs_unexpected_handler_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis = _FakeRedis()

    async def handler(_job: SchedulerDispatchJob) -> None:
        raise RuntimeError("state machine blew up")

    queue = RedisSchedulerDispatchQueue(redis)
    with caplog.at_level(logging.ERROR, logger="prismclaw.engine.scheduler.dispatch"):
        with contextlib.suppress(asyncio.CancelledError):
            await queue.consume(handler, block_ms=0)
        await asyncio.sleep(0.02)
    assert any(
        record.getMessage() == "scheduler.dispatch.handler_error" and record.exc_info
        for record in caplog.records
    )


async def test_cancelled_run_stays_running() -> None:
    """at-most-once：停机不自动补跑，也不把 run 伪装成 failed。"""
    store = _ExecutorStore()

    async def cancelled(**_kwargs: object) -> None:
        raise asyncio.CancelledError()

    app = SimpleNamespace(
        state=SimpleNamespace(
            redis_client=None,
            runner_deps=SimpleNamespace(session_store=_FakeSessionStore()),
        )
    )
    executor = ScheduledRunExecutor(
        app=app,
        store=store,
        settings=SimpleNamespace(max_consecutive_failures=3),
        broadcast=None,
        run_chat=cancelled,
    )
    job = SchedulerDispatchJob(
        run_id="r1",
        task_id="t1",
        task_name="n",
        instruction="go",
        user_id="u1",
        workspace_id="default",
        fired_at=0.0,
    )
    with contextlib.suppress(asyncio.CancelledError):
        await executor.execute(job)
    assert store.runs["r1"]["status"] == "running"
    assert "finished_at" not in store.runs["r1"]
