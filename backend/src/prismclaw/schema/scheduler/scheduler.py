"""Scheduler contracts.

Protocol for task scheduling — store, engine, and dispatch interfaces.
Canonical definitions of scheduler data types live here so that the
contracts layer (L0) does not depend on the engine layer (L1).
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal, Protocol

# ── Constants ────────────────────────────────────────────────────────

INVALIDATE_CHANNEL = "prismclaw:scheduler:invalidate"

# ── Type aliases ─────────────────────────────────────────────────────

TaskStatus = Literal["idle", "running", "success", "failed", "disabled"]
RunStatus = Literal["pending", "running", "success", "failed"]
MissedPolicy = Literal["skip", "run_once"]

# ── Helper ───────────────────────────────────────────────────────────


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def make_run_id(task_id: str, fire_slot: float) -> str:
    """一次 fire 的确定性主键。相同 fire_slot 永远得到同一条 run。"""
    return hashlib.sha256(f"{task_id}:{fire_slot}".encode()).hexdigest()[:16]


# ── Data models ──────────────────────────────────────────────────────


@dataclass(slots=True)
class ScheduledTask:
    """A user-defined recurring task."""

    id: str = field(default_factory=_new_id)
    user_id: str = ""
    workspace_id: str = "default"
    name: str = ""
    instruction: str = ""
    cron_expr: str = ""
    schedule_spec: dict = field(default_factory=dict)
    enabled: bool = True
    conversation_id: str | None = None
    last_fired_at: float | None = None
    last_status: TaskStatus = "idle"
    consecutive_failures: int = 0
    created_at: float = field(default_factory=time.time)
    max_age_days: int = 30
    missed_policy: MissedPolicy = "skip"


@dataclass(slots=True)
class ScheduledTaskRun:
    """A single execution record of a scheduled task."""

    id: str
    task_id: str = ""
    fired_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    status: RunStatus = "pending"
    session_id: str | None = None
    error: str | None = None
    duration_s: float | None = None


# ── Dispatch job ─────────────────────────────────────────────────────


@dataclass(slots=True)
class SchedulerDispatchJob:
    """Enqueued job consumed by the scheduler worker loop."""

    run_id: str
    task_id: str
    task_name: str
    instruction: str
    user_id: str
    workspace_id: str
    fired_at: float


# ── Protocols ────────────────────────────────────────────────────────


class SchedulerStoreProtocol(Protocol):
    """Protocol for scheduler persistence — duck-typing compatible with scheduler/store.SchedulerStore."""

    async def list_tasks(
        self,
        user_id: str | None = None,
        workspace_id: str | None = None,
        enabled_only: bool = False,
    ) -> list[ScheduledTask]:
        """List scheduled tasks, optionally filtered."""
        ...

    async def get_task(self, task_id: str) -> ScheduledTask | None:
        """Get a single task by ID."""
        ...

    async def put_task(self, task: ScheduledTask) -> None:
        """Create or update a task."""
        ...

    async def delete_task(self, task_id: str) -> None:
        """Delete a task."""
        ...

    async def update_status(
        self,
        task_id: str,
        status: str,
        consecutive_failures: int | None = None,
    ) -> None:
        """Update a task's execution status."""
        ...

    async def set_conversation(self, task_id: str, conversation_id: str) -> None:
        """Bind a task to a conversation."""
        ...

    async def add_run(self, run: ScheduledTaskRun, *, fired_task_id: str, fired_at: float) -> bool:
        """Record a task run."""
        ...

    async def update_run(self, run_id: str, **fields: object) -> None:
        """Update a run's fields."""
        ...

    async def list_runs(
        self, task_id: str, limit: int = 20, offset: int = 0
    ) -> list[ScheduledTaskRun]:
        """List runs for a task."""
        ...

    async def list_user_scheduled_session_task_names(
        self,
        user_id: str,
        workspace_id: str | None = None,
    ) -> dict[str, str]:
        """Return ``{session_id: task_name}`` for scheduled-run sessions of this user.

        Used by the web session list to tag conversations as ``type='scheduled'``
        and to backfill their title with the task name, without modifying the
        session header schema. A single session can be returned at most once.
        """
        ...

    async def count_user_tasks(self, user_id: str) -> int:
        """Count tasks belonging to a user."""
        ...


class SchedulerEngineProtocol(Protocol):
    """Protocol for the scheduler engine — duck-typing compatible with scheduler/engine.SchedulerEngine."""

    async def start(self) -> None:
        """Start the scheduler."""
        ...

    def stop(self) -> None:
        """Stop the scheduler."""
        ...

    def invalidate_cache(self, task_id: str | None = None) -> None:
        """Invalidate the scheduler cache for a task or all tasks."""
        ...


__all__ = [
    "INVALIDATE_CHANNEL",
    "make_run_id",
    "SchedulerDispatchJob",
    "SchedulerEngineProtocol",
    "SchedulerStoreProtocol",
    "ScheduledTask",
    "ScheduledTaskRun",
    "TaskStatus",
    "RunStatus",
    "MissedPolicy",
]
