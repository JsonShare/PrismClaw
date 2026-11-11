from __future__ import annotations

import time
import uuid
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from prismclaw.schema.session.types import CustomEntry, generate_entry_id, now_iso
from prismclaw.schema.storage.storage_types import SessionStore
from prismclaw.schema.session.event_logger import SessionEventLogger

_event_logger = SessionEventLogger()

CURRENT_PARALLEL_TASK_SCHEMA_VERSION = "parallel_task.v2"
PARALLEL_TASK_RUNTIME_CUSTOM_TYPE = "parallel_task_runtime"

ParallelTaskKind = Literal["subagent", "fork"]
ParallelTaskStatus = Literal[
    "scheduled",
    "running",
    "completed",
    "failed",
    "cancelled",
]
NotificationState = Literal["pending", "emitted", "suppressed"]
PARALLEL_TASK_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


class ParallelTaskRuntimeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["parallel_task.v2"] = CURRENT_PARALLEL_TASK_SCHEMA_VERSION
    task_id: str
    owner_session_id: str
    source_call_id: str | None = None
    kind: ParallelTaskKind
    status: ParallelTaskStatus
    created_at_epoch_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    started_at_epoch_ms: int | None = None
    completed_at_epoch_ms: int | None = None
    result_summary: str | None = None
    error_summary: str | None = None
    notification_state: NotificationState = "pending"
    managed_task_id: str | None = None
    run_id: str | None = None
    agent_name: str | None = None
    agent_source: str | None = None
    projection_payload: dict[str, Any] = Field(default_factory=dict)


class ParallelTaskStore(Protocol):
    async def append(self, record: ParallelTaskRuntimeRecord) -> None: ...

    async def get(
        self,
        *,
        session_id: str,
        task_id: str,
    ) -> ParallelTaskRuntimeRecord | None: ...

    async def list_for_session(
        self,
        *,
        session_id: str,
        include_terminal: bool = True,
        kinds: set[ParallelTaskKind] | None = None,
    ) -> list[ParallelTaskRuntimeRecord]: ...

    async def cancel(
        self,
        *,
        session_id: str,
        task_id: str,
        error_summary: str | None = None,
    ) -> ParallelTaskRuntimeRecord | None: ...


def new_parallel_task_id() -> str:
    return f"pt:{uuid.uuid4().hex[:12]}"


def parse_parallel_task_record(data: Any) -> ParallelTaskRuntimeRecord | None:
    if isinstance(data, ParallelTaskRuntimeRecord):
        return data
    if not isinstance(data, dict):
        return None
    kind_raw = data.get("kind")
    if kind_raw == "background_subagent":
        data = {**data, "kind": "subagent"}
    elif kind_raw == "fork":
        pass
    try:
        return ParallelTaskRuntimeRecord.model_validate(data)
    except Exception:
        return None


def _branch_entries(tree: Any) -> list[Any]:
    if tree is None:
        return []
    get_branch = getattr(tree, "get_branch", None)
    if callable(get_branch):
        try:
            branch = get_branch()
        except Exception:
            branch = None
        if isinstance(branch, list):
            return branch
        if branch is not None:
            return list(branch)
    entries = getattr(tree, "entries", None)
    order = getattr(tree, "order", None)
    if not isinstance(entries, dict) or not isinstance(order, list):
        return []
    return [entries[entry_id] for entry_id in order if entry_id in entries]


def parallel_task_records_for_tree(
    tree: Any | None,
    *,
    include_terminal: bool = True,
    kinds: set[ParallelTaskKind] | None = None,
) -> list[ParallelTaskRuntimeRecord]:
    if tree is None:
        return []
    latest: dict[str, ParallelTaskRuntimeRecord] = {}
    for entry in reversed(_branch_entries(tree)):
        if not isinstance(entry, CustomEntry):
            continue
        if entry.custom_type != PARALLEL_TASK_RUNTIME_CUSTOM_TYPE:
            continue
        record = parse_parallel_task_record(entry.data)
        if record is None or record.task_id in latest:
            continue
        if kinds is not None and record.kind not in kinds:
            continue
        if not include_terminal and record.status in PARALLEL_TASK_TERMINAL_STATUSES:
            continue
        latest[record.task_id] = record
    return list(reversed(list(latest.values())))


def latest_parallel_task_for_tree(
    tree: Any | None,
    *,
    task_id: str,
) -> ParallelTaskRuntimeRecord | None:
    if tree is None:
        return None
    for entry in reversed(_branch_entries(tree)):
        if not isinstance(entry, CustomEntry):
            continue
        if entry.custom_type != PARALLEL_TASK_RUNTIME_CUSTOM_TYPE:
            continue
        record = parse_parallel_task_record(entry.data)
        if record is not None and record.task_id == task_id:
            return record
    return None


class SessionParallelTaskStore:
    def __init__(
        self,
        session_store: SessionStore,
        lock_manager: Any = None,
        *,
        lock_key_prefix: str = "session-lock:",
    ) -> None:
        self._session_store = session_store
        self._lock = lock_manager
        self._lock_key_prefix = lock_key_prefix

    def _lock_key(self, session_id: str) -> str:
        return f"{self._lock_key_prefix}{{{session_id}}}"

    async def append(self, record: ParallelTaskRuntimeRecord) -> None:
        session_id = record.owner_session_id
        lock_key = self._lock_key(session_id)

        if self._lock is not None:
            token = await self._lock.acquire(lock_key, ttl_ms=30_000)
            try:
                await self._append_locked(record, session_id)
            finally:
                await self._lock.release(lock_key, token)
        else:
            await self._append_locked(record, session_id)

    async def _append_locked(
        self, record: ParallelTaskRuntimeRecord, session_id: str
    ) -> None:
        tree = await self._session_store.load(session_id)
        if tree is None:
            raise ValueError(
                f"parallel task session {session_id!r} was not found"
            )
        entry = CustomEntry(
            id=generate_entry_id(set(tree.entries.keys())),
            parent_id=tree.leaf_id,
            timestamp=now_iso(),
            custom_type=PARALLEL_TASK_RUNTIME_CUSTOM_TYPE,
            data=record.model_dump(mode="json"),
        )
        event = _event_logger.entry_updated(tree.header.id, entry, leaf_id=entry.id)
        await self._session_store.commit_events([event])

    async def get(
        self,
        *,
        session_id: str,
        task_id: str,
    ) -> ParallelTaskRuntimeRecord | None:
        tree = await self._session_store.load(session_id)
        return latest_parallel_task_for_tree(tree, task_id=task_id)

    async def list_for_session(
        self,
        *,
        session_id: str,
        include_terminal: bool = True,
        kinds: set[ParallelTaskKind] | None = None,
    ) -> list[ParallelTaskRuntimeRecord]:
        tree = await self._session_store.load(session_id)
        return parallel_task_records_for_tree(
            tree,
            include_terminal=include_terminal,
            kinds=kinds,
        )

    async def cancel(
        self,
        *,
        session_id: str,
        task_id: str,
        error_summary: str | None = None,
    ) -> ParallelTaskRuntimeRecord | None:
        current = await self.get(session_id=session_id, task_id=task_id)
        if current is None:
            return None
        if current.status in PARALLEL_TASK_TERMINAL_STATUSES:
            return current
        cancelled = build_parallel_task_terminal_record(
            current,
            status="cancelled",
            error_summary=error_summary or "cancelled_by_user",
        )
        await self.append(cancelled)
        return cancelled


def build_parallel_task_terminal_record(
    current: ParallelTaskRuntimeRecord,
    *,
    status: Literal["completed", "failed", "cancelled"],
    result_summary: str | None = None,
    error_summary: str | None = None,
    projection_payload: dict[str, Any] | None = None,
) -> ParallelTaskRuntimeRecord:
    return current.model_copy(
        update={
            "status": status,
            "completed_at_epoch_ms": int(time.time() * 1000),
            "result_summary": result_summary,
            "error_summary": error_summary,
            "projection_payload": dict(projection_payload or current.projection_payload),
        },
        deep=True,
    )
