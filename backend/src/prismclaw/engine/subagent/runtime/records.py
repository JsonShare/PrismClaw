from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from prismclaw.schema.session.event_logger import SessionEventLogger
from prismclaw.schema.session.types import (
    SUBAGENT_RUN_CUSTOM_TYPE,
    SUBAGENT_SESSION_CUSTOM_TYPE,
    CustomEntry,
    SessionTree,
    generate_entry_id,
    now_iso,
)
from prismclaw.schema.storage.storage_types import SessionStore

CURRENT_AGENT_SESSION_SCHEMA_VERSION = "subagent_session.v1"
CURRENT_AGENT_RUN_SCHEMA_VERSION = "subagent_run.v1"

AgentLifecycle = Literal["one_shot", "persistent"]
AgentSessionStatus = Literal[
    "idle",
    "running",
    "interrupted",
    "recovery_blocked",
    "closed",
]
AgentRunStatus = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "interrupted",
]


def new_agent_id() -> str:
    return f"ag:{uuid.uuid4()}"


def new_run_id() -> str:
    return f"ar:{uuid.uuid4()}"


class AgentSessionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["subagent_session.v1"] = CURRENT_AGENT_SESSION_SCHEMA_VERSION
    agent_id: str
    owner_session_id: str
    child_session_id: str
    agent_name: str
    agent_source: str
    lifecycle: AgentLifecycle = "one_shot"
    status: AgentSessionStatus = "idle"
    active_run_id: str | None = None
    current_fence: int = 0
    definition_snapshot: dict[str, Any] = Field(default_factory=dict)
    definition_fingerprint: str = ""
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    closed_at: str | None = None


class AgentRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["subagent_run.v1"] = CURRENT_AGENT_RUN_SCHEMA_VERSION
    run_id: str
    agent_id: str
    owner_session_id: str
    child_session_id: str
    task_id: str | None = None
    attempt: int = 1
    resumed_from_run_id: str | None = None
    fence: int = 0
    owner_worker_id: str = ""
    status: AgentRunStatus = "queued"
    safe_boundary: str | None = None
    recovery_reason: str | None = None
    result_envelope: dict[str, Any] | None = None
    error_summary: str | None = None
    started_at: str | None = None
    finished_at: str | None = None


def _fold_records[RecordT: AgentSessionRecord | AgentRunRecord](
    tree: SessionTree | None,
    *,
    custom_type: str,
    model_cls: type[RecordT],
    identity_field: str,
    fence_field: str,
) -> list[RecordT]:
    if tree is None:
        return []
    latest: dict[str, tuple[tuple[int, int], RecordT]] = {}
    for entry in tree.get_branch():
        if not isinstance(entry, CustomEntry) or entry.custom_type != custom_type:
            continue
        if not isinstance(entry.data, dict):
            continue
        try:
            record = model_cls.model_validate(entry.data)
        except Exception:
            continue
        identity = getattr(record, identity_field)
        rank = (getattr(record, fence_field), entry.seq)
        current = latest.get(identity)
        if current is None or rank >= current[0]:
            latest[identity] = (rank, record)
    return [
        record
        for _rank, record in sorted(latest.values(), key=lambda item: item[0])
    ]


def agent_session_records_for_tree(
    tree: SessionTree | None,
) -> list[AgentSessionRecord]:
    return _fold_records(
        tree,
        custom_type=SUBAGENT_SESSION_CUSTOM_TYPE,
        model_cls=AgentSessionRecord,
        identity_field="agent_id",
        fence_field="current_fence",
    )


def agent_run_records_for_tree(tree: SessionTree | None) -> list[AgentRunRecord]:
    return _fold_records(
        tree,
        custom_type=SUBAGENT_RUN_CUSTOM_TYPE,
        model_cls=AgentRunRecord,
        identity_field="run_id",
        fence_field="fence",
    )


def latest_agent_session_for_tree(
    tree: SessionTree | None,
    *,
    agent_id: str,
) -> AgentSessionRecord | None:
    return next(
        (record for record in agent_session_records_for_tree(tree) if record.agent_id == agent_id),
        None,
    )


def latest_agent_run_for_tree(
    tree: SessionTree | None,
    *,
    run_id: str,
) -> AgentRunRecord | None:
    return next(
        (record for record in agent_run_records_for_tree(tree) if record.run_id == run_id),
        None,
    )


class SessionSubagentRecordStore:
    """Store Agent/Run projections as CustomEntry values on the parent tree."""

    def __init__(self, session_store: SessionStore) -> None:
        self._session_store = session_store
        self._event_logger = SessionEventLogger()

    async def append(self, record: AgentSessionRecord | AgentRunRecord) -> None:
        if isinstance(record, AgentSessionRecord):
            custom_type = SUBAGENT_SESSION_CUSTOM_TYPE
        elif isinstance(record, AgentRunRecord):
            custom_type = SUBAGENT_RUN_CUSTOM_TYPE
        else:
            raise TypeError(f"unsupported subagent record: {type(record).__name__}")

        session_id = record.owner_session_id
        tree = await self._session_store.load(session_id)
        if tree is None:
            raise ValueError(f"subagent owner session {session_id!r} was not found")
        entry = CustomEntry(
            id=generate_entry_id(tree.all_entry_ids()),
            parent_id=tree.leaf_id,
            timestamp=now_iso(),
            custom_type=custom_type,
            data=record.model_dump(mode="json"),
        )
        event = self._event_logger.entry_updated(session_id, entry, leaf_id=entry.id)
        await self._session_store.commit_events([event])

    async def get_session(
        self,
        *,
        session_id: str,
        agent_id: str,
    ) -> AgentSessionRecord | None:
        tree = await self._session_store.load(session_id)
        return latest_agent_session_for_tree(tree, agent_id=agent_id)

    async def list_sessions(
        self,
        *,
        session_id: str,
        lifecycle: AgentLifecycle | None = None,
        statuses: set[AgentSessionStatus] | None = None,
    ) -> list[AgentSessionRecord]:
        tree = await self._session_store.load(session_id)
        records = agent_session_records_for_tree(tree)
        if lifecycle is not None:
            records = [record for record in records if record.lifecycle == lifecycle]
        if statuses is not None:
            records = [record for record in records if record.status in statuses]
        return records

    async def get_run(
        self,
        *,
        session_id: str,
        run_id: str,
    ) -> AgentRunRecord | None:
        tree = await self._session_store.load(session_id)
        return latest_agent_run_for_tree(tree, run_id=run_id)

    async def list_runs(
        self,
        *,
        session_id: str,
        agent_id: str | None = None,
    ) -> list[AgentRunRecord]:
        tree = await self._session_store.load(session_id)
        records = agent_run_records_for_tree(tree)
        if agent_id is not None:
            records = [record for record in records if record.agent_id == agent_id]
        return records
