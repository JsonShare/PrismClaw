"""Scheduled task persistence — PostgreSQL implementation.

SQLAlchemy Core (``Table`` + ``MetaData``) is used so that ``update_run``'s
dynamic ``**fields`` are validated against the table metadata structurally,
eliminating the f-string column-name injection vector of the old SQLite store.
Schema creation is idempotent via ``metadata.create_all`` under a Postgres
advisory lock (mirrors ``prismclaw.persistence.user.models.init_schema``).

Timestamps are ``DOUBLE PRECISION`` (Unix epoch seconds) — matching
``prismclaw.contracts.infra.scheduler`` which uses ``float`` throughout. A
``float → datetime`` type upgrade is out of scope for the storage migration.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Double,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from prismclaw.schema.scheduler.scheduler import ScheduledTask, ScheduledTaskRun

logger = logging.getLogger(__name__)

# Distinct advisory-lock key per subsystem to avoid cross-subsystem contention.
# userdb uses 742100; scheduler uses 742101.
_SCHED_ADVISORY_LOCK_KEY = 742101

_metadata = MetaData()

scheduled_tasks = Table(
    "pc_scheduled_tasks",
    _metadata,
    Column("id", String, primary_key=True),
    Column("user_id", String, nullable=False),
    Column("workspace_id", String, nullable=False, server_default=text("'default'")),
    Column("name", String, nullable=False),
    Column("instruction", Text, nullable=False),
    Column("cron_expr", String, nullable=False),
    Column("schedule_spec", Text, nullable=False),
    Column("enabled", Boolean, nullable=False, server_default=text("true")),
    Column("conversation_id", String),
    Column("last_fired_at", Double),
    Column(
        "last_status",
        String,
        nullable=False,
        server_default=text("'idle'"),
    ),
    Column("consecutive_failures", Integer, nullable=False, server_default=text("0")),
    Column("created_at", Double, nullable=False),
    Column("max_age_days", Integer, nullable=False, server_default=text("30")),
    Column(
        "missed_policy",
        String,
        nullable=False,
        server_default=text("'skip'"),
    ),
    CheckConstraint(
        "last_status IN ('idle','running','success','failed','disabled')",
        name="ck_pc_sched_tasks_last_status",
    ),
    CheckConstraint(
        "missed_policy IN ('skip','run_once')", name="ck_pc_sched_tasks_missed_policy"
    ),
    Index("idx_pc_sched_tasks_user_workspace", "user_id", "workspace_id"),
    Index("idx_pc_sched_tasks_enabled", "enabled", postgresql_where=text("enabled = true")),
)

scheduled_task_runs = Table(
    "pc_scheduled_task_runs",
    _metadata,
    Column("id", String, primary_key=True),
    Column("task_id", String, nullable=False),
    Column("fired_at", Double, nullable=False),
    Column("started_at", Double),
    Column("finished_at", Double),
    Column("status", String, nullable=False, server_default=text("'pending'")),
    Column("session_id", String),
    Column("error", Text),
    Column("duration_s", Double),
    CheckConstraint(
        "status IN ('pending','running','success','failed')",
        name="ck_pc_sched_runs_status",
    ),
    Index("idx_pc_sched_runs_task", "task_id", "fired_at"),
)

# Non-PK columns upserted by ``put_task`` — built once, reused per call.
_TASK_UPSERT_COLUMNS = [c.name for c in scheduled_tasks.columns if c.name != "id"]


async def init_schema(engine: AsyncEngine) -> None:
    """Idempotent schema creation under an advisory lock.

    Prevents concurrent DDL races when multiple workers start simultaneously
    against a shared database.
    """
    async with engine.begin() as conn:
        await conn.execute(text(f"SELECT pg_advisory_xact_lock({_SCHED_ADVISORY_LOCK_KEY})"))
        await conn.run_sync(_metadata.create_all)


class PostgresSchedulerStore:
    """PostgreSQL-backed scheduler store using SQLAlchemy async Core.

    All write operations run inside explicit transactions (``session.begin()``)
    so multi-statement operations (``add_run``'s run-insert + task-update) are
    atomic. Read operations use auto-commit connections.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    # ── Tasks ──────────────────────────────────────────────────────────

    async def list_tasks(
        self,
        user_id: str | None = None,
        workspace_id: str | None = None,
        enabled_only: bool = False,
    ) -> list[ScheduledTask]:
        stmt = scheduled_tasks.select().order_by(scheduled_tasks.c.created_at.desc())
        if user_id is not None:
            stmt = stmt.where(scheduled_tasks.c.user_id == user_id)
        if workspace_id is not None:
            stmt = stmt.where(scheduled_tasks.c.workspace_id == workspace_id)
        if enabled_only:
            stmt = stmt.where(scheduled_tasks.c.enabled.is_(True))
        async with self._sf() as s:
            rows = (await s.execute(stmt)).mappings().all()
        return [self._row_to_task(r) for r in rows]

    async def get_task(self, task_id: str) -> ScheduledTask | None:
        stmt = scheduled_tasks.select().where(scheduled_tasks.c.id == task_id)
        async with self._sf() as s:
            row = (await s.execute(stmt)).mappings().first()
        return self._row_to_task(row) if row else None

    async def put_task(self, task: ScheduledTask) -> None:
        """UPSERT a task (``ON CONFLICT (id) DO UPDATE`` — Postgres equivalent
        of SQLite's ``INSERT OR REPLACE``)."""
        values = self._task_to_values(task)
        stmt = pg_insert(scheduled_tasks).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[scheduled_tasks.c.id],
            set_={c: getattr(stmt.excluded, c) for c in _TASK_UPSERT_COLUMNS},
        )
        async with self._sf() as s:
            async with s.begin():
                await s.execute(stmt)

    async def delete_task(self, task_id: str) -> None:
        async with self._sf() as s:
            async with s.begin():
                await s.execute(
                    scheduled_task_runs.delete().where(
                        scheduled_task_runs.c.task_id == task_id
                    )
                )
                await s.execute(
                    scheduled_tasks.delete().where(scheduled_tasks.c.id == task_id)
                )

    async def update_status(
        self, task_id: str, status: str, consecutive_failures: int | None = None
    ) -> None:
        values: dict[str, Any] = {"last_status": status}
        if consecutive_failures is not None:
            values["consecutive_failures"] = consecutive_failures
        async with self._sf() as s:
            async with s.begin():
                await s.execute(
                    scheduled_tasks.update()
                    .where(scheduled_tasks.c.id == task_id)
                    .values(**values)
                )

    async def set_conversation(self, task_id: str, conversation_id: str) -> None:
        async with self._sf() as s:
            async with s.begin():
                await s.execute(
                    scheduled_tasks.update()
                    .where(scheduled_tasks.c.id == task_id)
                    .values(conversation_id=conversation_id)
                )

    async def count_user_tasks(self, user_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(scheduled_tasks)
            .where(scheduled_tasks.c.user_id == user_id)
        )
        async with self._sf() as s:
            return int((await s.execute(stmt)).scalar() or 0)

    # ── Runs ───────────────────────────────────────────────────────────

    async def add_run(
        self, run: ScheduledTaskRun, *, fired_task_id: str, fired_at: float
    ) -> bool:
        """Atomically insert a run and update the task's ``last_fired_at``.

        Only ``last_fired_at`` is touched on the task — ``last_status`` is owned
        by the executor (which sets ``running`` on pickup, then the terminal
        status on completion). Setting ``running`` here would leave the task
        stuck if ``dispatch_enqueue`` later fails.
        """
        stmt = pg_insert(scheduled_task_runs).values(
            id=run.id,
            task_id=run.task_id,
            fired_at=run.fired_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
            status=run.status,
            session_id=run.session_id,
            error=run.error,
            duration_s=run.duration_s,
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["id"]).returning(
            scheduled_task_runs.c.id
        )
        async with self._sf() as s:
            async with s.begin():
                inserted = (await s.execute(stmt)).first()
                if inserted is None:
                    return False
                await s.execute(
                    scheduled_tasks.update()
                    .where(scheduled_tasks.c.id == fired_task_id)
                    .values(last_fired_at=fired_at)
                )
        return True

    async def update_run(self, run_id: str, **fields: object) -> None:
        if not fields:
            return
        async with self._sf() as s:
            async with s.begin():
                await s.execute(
                    scheduled_task_runs.update()
                    .where(scheduled_task_runs.c.id == run_id)
                    .values(**fields)
                )

    async def list_runs(
        self, task_id: str, limit: int = 20, offset: int = 0
    ) -> list[ScheduledTaskRun]:
        stmt = (
            scheduled_task_runs.select()
            .where(scheduled_task_runs.c.task_id == task_id)
            .order_by(scheduled_task_runs.c.fired_at.desc())
            .limit(limit)
            .offset(offset)
        )
        async with self._sf() as s:
            rows = (await s.execute(stmt)).mappings().all()
        return [self._row_to_run(r) for r in rows]

    async def list_user_scheduled_session_task_names(
        self,
        user_id: str,
        workspace_id: str | None = None,
    ) -> dict[str, str]:
        """Return ``{session_id: task_name}`` for sessions created by scheduled runs.

        Joins ``pc_scheduled_task_runs`` against ``pc_scheduled_tasks`` to filter
        by ``user_id`` (and optionally ``workspace_id``), returning only rows
        with a non-null ``session_id``. Used by ``/api/sessions`` to tag those
        conversations as ``type='scheduled'`` (and to backfill their title with
        the task name) without modifying the session header schema.
        """
        stmt = (
            select(
                scheduled_task_runs.c.session_id,
                scheduled_tasks.c.name,
            )
            .select_from(
                scheduled_task_runs.join(
                    scheduled_tasks,
                    scheduled_task_runs.c.task_id == scheduled_tasks.c.id,
                )
            )
            .where(
                scheduled_tasks.c.user_id == user_id,
                scheduled_task_runs.c.session_id.isnot(None),
            )
        )
        if workspace_id:
            stmt = stmt.where(scheduled_tasks.c.workspace_id == workspace_id)
        async with self._sf() as s:
            rows = (await s.execute(stmt)).all()
        return {sid: name for sid, name in rows if sid and name}

    # ── Row mapping ────────────────────────────────────────────────────

    @staticmethod
    def _task_to_values(task: ScheduledTask) -> dict[str, Any]:
        return {
            "id": task.id,
            "user_id": task.user_id,
            "workspace_id": task.workspace_id,
            "name": task.name,
            "instruction": task.instruction,
            "cron_expr": task.cron_expr,
            "schedule_spec": json.dumps(task.schedule_spec, ensure_ascii=False),
            "enabled": task.enabled,
            "conversation_id": task.conversation_id,
            "last_fired_at": task.last_fired_at,
            "last_status": task.last_status,
            "consecutive_failures": task.consecutive_failures,
            "created_at": task.created_at,
            "max_age_days": task.max_age_days,
            "missed_policy": task.missed_policy,
        }

    @staticmethod
    def _row_to_task(row: Any) -> ScheduledTask:
        return ScheduledTask(
            id=row["id"],
            user_id=row["user_id"],
            workspace_id=row["workspace_id"],
            name=row["name"],
            instruction=row["instruction"],
            cron_expr=row["cron_expr"],
            schedule_spec=json.loads(row["schedule_spec"]) if row["schedule_spec"] else {},
            enabled=bool(row["enabled"]),
            conversation_id=row["conversation_id"],
            last_fired_at=row["last_fired_at"],
            last_status=row["last_status"],
            consecutive_failures=row["consecutive_failures"],
            created_at=row["created_at"],
            max_age_days=row["max_age_days"],
            missed_policy=row["missed_policy"],
        )

    @staticmethod
    def _row_to_run(row: Any) -> ScheduledTaskRun:
        return ScheduledTaskRun(
            id=row["id"],
            task_id=row["task_id"],
            fired_at=row["fired_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            status=row["status"],
            session_id=row["session_id"],
            error=row["error"],
            duration_s=row["duration_s"],
        )
