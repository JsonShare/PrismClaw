from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from prismclaw.schema.session.event_logger import SessionEventLogger
from prismclaw.schema.session.types import (
    CustomEntry,
    SessionEntry,
    SessionTree,
    generate_entry_id,
    now_iso,
)
from prismclaw.schema.storage.storage_types import SessionStore

_event_logger = SessionEventLogger()
logger = logging.getLogger(__name__)

CURRENT_PARALLEL_TASK_SCHEMA_VERSION: Literal["parallel_task.v3"] = "parallel_task.v3"
PARALLEL_TASK_RUNTIME_CUSTOM_TYPE = "parallel_task_runtime"

ParallelTaskKind = Literal["subagent", "fork"]
ParallelTaskStatus = Literal[
    "scheduled",
    "running",
    "completed",
    "failed",
    "cancelled",
]
PARALLEL_TASK_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


class ParallelTaskRuntimeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["parallel_task.v3"] = CURRENT_PARALLEL_TASK_SCHEMA_VERSION
    task_id: str
    # ``(agent_id, task_id)`` 组合唯一：同一 task 只属于一个 agent，
    # 但同一 agent 可以有多个 task（每次 Run 一个）。
    agent_id: str
    owner_session_id: str
    source_call_id: str | None = None
    kind: ParallelTaskKind
    status: ParallelTaskStatus
    created_at_epoch_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    started_at_epoch_ms: int | None = None
    completed_at_epoch_ms: int | None = None
    result_summary: str | None = None
    error_summary: str | None = None
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
        agent_id: str,
        task_id: str,
    ) -> ParallelTaskRuntimeRecord | None: ...

    async def list_for_session(
        self,
        *,
        session_id: str,
        include_terminal: bool = True,
        kinds: set[ParallelTaskKind] | None = None,
    ) -> list[ParallelTaskRuntimeRecord]: ...


def new_parallel_task_id() -> str:
    return f"pt:{uuid.uuid4().hex[:12]}"


def parse_parallel_task_record(data: object) -> ParallelTaskRuntimeRecord | None:
    if isinstance(data, ParallelTaskRuntimeRecord):
        return data
    if not isinstance(data, dict):
        return None
    try:
        return ParallelTaskRuntimeRecord.model_validate(data)
    except Exception as exc:
        logger.warning("parallel task record parse failed: %s", exc)
        return None


def _branch_entries(tree: SessionTree) -> list[SessionEntry]:
    if tree is None:
        return []
    return list(tree.get_branch())


def parallel_task_records_for_tree(
    tree: SessionTree | None,
    *,
    include_terminal: bool = True,
    kinds: set[ParallelTaskKind] | None = None,
) -> list[ParallelTaskRuntimeRecord]:
    if tree is None:
        return []
    latest: dict[tuple[str, str], ParallelTaskRuntimeRecord] = {}
    for entry in reversed(_branch_entries(tree)):
        if not isinstance(entry, CustomEntry):
            continue
        if entry.custom_type != PARALLEL_TASK_RUNTIME_CUSTOM_TYPE:
            continue
        record = parse_parallel_task_record(entry.data)
        if record is None:
            continue
        key = (record.agent_id, record.task_id)
        if key in latest:
            continue
        if kinds is not None and record.kind not in kinds:
            continue
        if not include_terminal and record.status in PARALLEL_TASK_TERMINAL_STATUSES:
            continue
        latest[key] = record
    return list(reversed(list(latest.values())))


def latest_parallel_task_for_tree(
    tree: SessionTree | None,
    *,
    agent_id: str,
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
        if record is not None and record.agent_id == agent_id and record.task_id == task_id:
            return record
    return None


def task_owner_agent_id(
    tree: SessionTree | None,
    *,
    task_id: str,
) -> str | None:
    """返回已登记该 task_id 的 agent_id；用于识别组合唯一被违反。"""
    if tree is None:
        return None
    for entry in reversed(_branch_entries(tree)):
        if not isinstance(entry, CustomEntry):
            continue
        if entry.custom_type != PARALLEL_TASK_RUNTIME_CUSTOM_TYPE:
            continue
        record = parse_parallel_task_record(entry.data)
        if record is not None and record.task_id == task_id:
            return record.agent_id
    return None


class SessionParallelTaskStore:
    """session-store-backed 实现。

    并发控制由 ``session_store.commit_events`` 内部的会话锁负责；
    本层不再单独加锁，避免与 commit_events 争用同一把不可重入锁。
    """

    def __init__(self, session_store: SessionStore) -> None:
        self._session_store = session_store

    async def append(self, record: ParallelTaskRuntimeRecord) -> None:
        """按 ``(owner_session_id, agent_id, task_id)`` 组合键 upsert。

        同键重复写入覆盖（追加新条目即成为最新投影）；同 ``task_id`` 但
        ``agent_id`` 不同则违反组合唯一，直接拒绝。
        """
        session_id = record.owner_session_id
        tree = await self._session_store.load(session_id)
        if tree is None:
            raise ValueError(f"parallel task session {session_id!r} was not found")
        owner_agent_id = task_owner_agent_id(tree, task_id=record.task_id)
        if owner_agent_id is not None and owner_agent_id != record.agent_id:
            raise ValueError(
                f"parallel task {record.task_id!r} already belongs to agent "
                f"{owner_agent_id!r}, cannot be reassigned to {record.agent_id!r}"
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
        agent_id: str,
        task_id: str,
    ) -> ParallelTaskRuntimeRecord | None:
        tree = await self._session_store.load(session_id)
        return latest_parallel_task_for_tree(
            tree,
            agent_id=agent_id,
            task_id=task_id,
        )

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
