"""dream dispatch 死信根因修复的回归测试。

背景（2026-10-22 死信排查结论）：三条死信均由以下机制叠加产生——
1. 会话锁被占时 `return False` 被当任务失败，5s/10s 快速重试烧完配额进死信；
2. 消息空闲回收阈值误用 worker 死亡判定常量（90s），长任务执行中被回收重投；
3. 调度器走 dispatch 队列入队前不查活跃 worker，长任务执行期间被重复入队撞锁。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest

from prismclaw.engine.dream.dispatch.lock import DreamExecutionLease
from prismclaw.engine.dream.dispatch.scheduler import DreamScheduler
from prismclaw.schema.dream.dream_types import DreamSessionState


class _FakeLease:
    def ensure_valid(self) -> None:
        return

    async def release(self) -> None:
        return


class _AlwaysBusyLockProvider:
    """模拟会话锁被另一个执行者持有。"""

    async def try_acquire(self, session_key: str, *, owner_label: str) -> DreamExecutionLease | None:
        return None


class _FakeStateStore:
    def __init__(self) -> None:
        self._states: dict[str, DreamSessionState] = {}

    async def get(self, session_key: str) -> DreamSessionState | None:
        return self._states.get(session_key)

    async def put(self, state: DreamSessionState) -> None:
        self._states[state.session_key] = state

    async def list_states(self) -> list[DreamSessionState]:
        return list(self._states.values())


@dataclass
class _FakeTaskInfo:
    name: str
    state: str


class _FakeTaskManager:
    def __init__(self) -> None:
        self.tasks: dict[str, list[_FakeTaskInfo]] = {}
        self.spawned: list[str] = []

    def list_tasks(self, *, category: str, owner: str) -> list[_FakeTaskInfo]:
        return self.tasks.get(owner, [])

    def spawn(self, name, coro, **kwargs) -> None:
        self.spawned.append(name)
        coro.close()  # 测试不真正执行协程，立即关闭避免 never-awaited 警告


class _RecordingEngine:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run_light(self, session_key: str) -> dict[str, Any]:
        self.calls.append(f"light:{session_key}")
        return {}

    async def run_nightly(self, session_key: str, *, include_light: bool = False) -> dict[str, Any]:
        self.calls.append(f"nightly:{session_key}")
        return {}


class _NullLockProvider:
    async def try_acquire(self, session_key: str, *, owner_label: str) -> DreamExecutionLease:
        return _FakeLease()


@dataclass
class _DreamSettingsFake:
    enabled: bool = True
    idle_check_interval_seconds: int = 300
    idle_threshold_seconds: int = 1800
    nightly_check_interval_seconds: int = 1800
    nightly_timezone: str = "local"
    nightly_start_hour_local: int = 1
    nightly_end_hour_local: int = 5
    nightly_catch_up_grace_minutes: int = 90
    nightly_max_sessions: int = 10
    multi_worker_scan_policy: str = "leader_only"


class _FakeDispatchQueue:
    """记录 enqueue 调用；可通过 busy_sessions 模拟去重标记已存在。"""

    def __init__(self, busy_sessions: set[str] | None = None) -> None:
        self.enqueued: list[str] = []
        self.busy_sessions = busy_sessions or set()

    async def enqueue(self, job) -> bool:
        if job.session_key in self.busy_sessions:
            return False
        self.enqueued.append(f"{job.job_kind}:{job.session_key}")
        return True


def _make_scheduler(
    *,
    lock_provider,
    task_manager,
    queue=None,
) -> DreamScheduler:
    return DreamScheduler(
        dreaming_engine=_RecordingEngine(),
        settings=_DreamSettingsFake(),
        state_store=_FakeStateStore(),
        lock_provider=lock_provider,
        task_manager=task_manager,
        dispatch_queue=queue,
    )


@pytest.mark.asyncio
async def test_lock_busy_is_success_not_failure():
    """修复1：会话锁被占时任务返回成功（ack 丢弃），不再触发重试/死信。"""
    scheduler = _make_scheduler(
        lock_provider=_AlwaysBusyLockProvider(),
        task_manager=_FakeTaskManager(),
    )
    assert await scheduler._run_light_worker("web:admin") is True
    assert await scheduler._run_nightly_worker(
        "web:admin",
        nightly_window=type(
            "W", (), {"start_at": datetime.now(), "end_at": datetime.now()}
        )(),
    ) is True


@pytest.mark.asyncio
async def test_no_enqueue_when_dispatch_task_running():
    """修复3：dispatch consumer 的任务执行中（dream-dispatch:* running）时，
    调度器不再重复入队（此前仅本地 dream-worker:* 前缀被识别）。"""
    task_manager = _FakeTaskManager()
    task_manager.tasks["web:admin"] = [
        _FakeTaskInfo(name="dream-dispatch:light:web:admin", state="running"),
    ]
    queue = _FakeDispatchQueue()
    scheduler = _make_scheduler(
        lock_provider=_NullLockProvider(),
        task_manager=task_manager,
        queue=queue,
    )
    assert await scheduler._enqueue_light("web:admin") is False
    assert queue.enqueued == []


@pytest.mark.asyncio
async def test_local_worker_prefix_still_blocks_enqueue():
    """修复3（回归保护）：本地 dream-worker:* running 时同样不入队。"""
    task_manager = _FakeTaskManager()
    task_manager.tasks["web:admin"] = [
        _FakeTaskInfo(name="dream-worker:nightly:web:admin", state="running"),
    ]
    queue = _FakeDispatchQueue()
    scheduler = _make_scheduler(
        lock_provider=_NullLockProvider(),
        task_manager=task_manager,
        queue=queue,
    )
    window = type("W", (), {"start_at": datetime.now(), "end_at": datetime.now()})()
    assert await scheduler._enqueue_nightly("web:admin", nightly_window=window) is False
    assert queue.enqueued == []


@pytest.mark.asyncio
async def test_enqueue_proceeds_when_no_active_worker():
    """修复3（回归保护）：无活跃任务时入队行为不变。"""
    task_manager = _FakeTaskManager()
    task_manager.tasks["web:admin"] = [
        _FakeTaskInfo(name="dream-dispatch:light:web:admin", state="done"),
    ]
    queue = _FakeDispatchQueue()
    scheduler = _make_scheduler(
        lock_provider=_NullLockProvider(),
        task_manager=task_manager,
        queue=queue,
    )
    assert await scheduler._enqueue_light("web:admin") is True
    assert queue.enqueued == ["light:web:admin"]