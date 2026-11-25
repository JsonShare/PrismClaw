from __future__ import annotations

import logging
import secrets
import time
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, Field, TypeAdapter

from prismclaw.schema.agent.session import (
    MessageEntry as MessageEntry,
)
from prismclaw.schema.agent.session import (
    SessionContextState,
    TurnRuntimeMetricsSnapshot,
)
from prismclaw.schema.agent.session import (
    SessionEntryBase as SessionEntryBase,
)
from prismclaw.schema.agent.session import (
    SessionEvent as SessionEvent,
)
from prismclaw.schema.agent.session import (
    SessionEventType as SessionEventType,
)
from prismclaw.schema.agent.session import (
    SessionHeader as SessionHeader,
)
from prismclaw.schema.agent.task_runtime import (
    EvidenceRecord,
    ReviewDecisionRecord,
    TaskMutation,
    TaskRuntimeState,
)
from prismclaw.schema.agent.task_types import TaskSpec

logger = logging.getLogger(__name__)

_TURN_RUNTIME_METRICS_CUSTOM_TYPE = "turn_runtime_metrics"
TURN_RUNTIME_METRICS_CUSTOM_TYPE = "turn_runtime_metrics"
FORK_TASK_PROJECTION_CUSTOM_TYPE = "fork_task_projection"
HANDOFF_REQUEST_CUSTOM_TYPE = "handoff_request"

# Typed custom_type constants (replace string matching with these)
SEARCH_EVIDENCE_CUSTOM_TYPE = "search_evidence"
EXTRACT_EVIDENCE_CUSTOM_TYPE = "extract_evidence"
TASK_RUNTIME_CUSTOM_TYPE = "task_runtime_snapshot"
TASK_SPEC_CUSTOM_TYPE = "task_spec_snapshot"
EVIDENCE_LEDGER_CUSTOM_TYPE = "evidence_ledger"
REVIEW_DECISION_CUSTOM_TYPE = "review_decision_record"
TASK_MUTATION_CUSTOM_TYPE = "task_mutation"
SESSION_MEMORY_CUSTOM_TYPE = "session_memory_snapshot"
SESSION_STATE_CUSTOM_TYPE = "session_state_snapshot"
REPLACEMENT_DECISION_CUSTOM_TYPE = "replacement_decision"
RESULT_COMPACTION_REFERENCE_CUSTOM_TYPE = "result_compaction_reference"
FEEDBACK_CUSTOM_TYPE = "feedback"
OUTPUT_FILES_CUSTOM_TYPE = "output_files"
FILE_CHANGES_CUSTOM_TYPE = "file_changes"
EXECUTION_TIMELINE_CUSTOM_TYPE = "execution_timeline"
REFERENCE_SOURCES_CUSTOM_TYPE = "reference_sources"
DECISION_LEDGER_EVENT_CUSTOM_TYPE = "decision_ledger_event"
TRANSIENT_RETRY_NOTICE_CUSTOM_TYPE = "transient_retry_notice"
RUN_PROJECTION_CUSTOM_TYPE = "run_projection"
SUBAGENT_SESSION_CUSTOM_TYPE = "subagent_session"
SUBAGENT_RUN_CUSTOM_TYPE = "subagent_run"
SUBAGENT_TOOL_EFFECT_CUSTOM_TYPE = "subagent_tool_effect"

# Typed payload models for CustomEntry.data (discriminated union)


class _CustomPayloadTurnMetrics(BaseModel):
    custom_type: Literal["turn_runtime_metrics"]
    data: TurnRuntimeMetricsSnapshot


class _CustomPayloadTaskRuntime(BaseModel):
    custom_type: Literal["task_runtime_snapshot"]
    data: TaskRuntimeState


class _CustomPayloadTaskSpec(BaseModel):
    custom_type: Literal["task_spec_snapshot"]
    data: TaskSpec


class _CustomPayloadEvidenceRecord(BaseModel):
    custom_type: Literal["evidence_ledger"]
    data: EvidenceRecord


class _CustomPayloadReviewDecision(BaseModel):
    custom_type: Literal["review_decision_record"]
    data: ReviewDecisionRecord


class _CustomPayloadTaskMutation(BaseModel):
    custom_type: Literal["task_mutation"]
    data: TaskMutation


class _CustomPayloadForkTask(BaseModel):
    custom_type: Literal["fork_task_projection"]
    data: dict[str, Any]


class _CustomPayloadHandoff(BaseModel):
    custom_type: Literal["handoff_request"]
    data: dict[str, Any]


class _CustomPayloadSearch(BaseModel):
    custom_type: Literal["search_evidence"]
    data: dict[str, Any]


class _CustomPayloadExtract(BaseModel):
    custom_type: Literal["extract_evidence"]
    data: dict[str, Any]


class _CustomPayloadSessionMemory(BaseModel):
    custom_type: Literal["session_memory_snapshot"]
    data: SessionContextState


class _CustomPayloadSessionState(BaseModel):
    custom_type: Literal["session_state_snapshot"]
    data: SessionContextState


class _CustomPayloadReplacementDecision(BaseModel):
    custom_type: Literal["replacement_decision"]
    data: dict[str, Any]


class _CustomPayloadFeedback(BaseModel):
    custom_type: Literal["feedback"]
    data: dict[str, Any]


class _CustomPayloadOutputFiles(BaseModel):
    custom_type: Literal["output_files"]
    data: dict[str, Any]


class _CustomPayloadFileChanges(BaseModel):
    custom_type: Literal["file_changes"]
    data: dict[str, Any]


class _CustomPayloadExecutionTimeline(BaseModel):
    custom_type: Literal["execution_timeline"]
    data: dict[str, Any]


class _CustomPayloadReferenceSources(BaseModel):
    custom_type: Literal["reference_sources"]
    data: dict[str, Any]


class _CustomPayloadResultCompactionReference(BaseModel):
    custom_type: Literal["result_compaction_reference"]
    data: dict[str, Any]


class _CustomPayloadDecisionLedgerEvent(BaseModel):
    custom_type: Literal["decision_ledger_event"]
    data: dict[str, Any]


class _CustomPayloadTransientRetryNotice(BaseModel):
    custom_type: Literal["transient_retry_notice"]
    data: dict[str, Any]


class _CustomPayloadFactSheet(BaseModel):
    custom_type: Literal["fact_sheet"]
    facts: dict[str, str]

    @property
    def data(self) -> dict[str, str]:
        return self.facts


class RunProjectionPayload(BaseModel):
    """后端运行态快照（内存权威源的序列化形态）。

    运行态的权威源是内存 SessionQueue 投影：GET /run 与 WS run.snapshot
    实时构建本 payload。树里的 run_projection custom entry 是 flusher 的
    周期审计留档（崩溃取证，≤5s 滞后），不再作为查询数据源。
    """

    conversation_id: str
    state: str = "active"
    streaming_text: str = ""
    timeline_nodes: list[dict[str, Any]] = []
    task_runtime: dict[str, Any] = {}
    phase: str | None = None
    phase_at: int | None = None
    # 待用户回答的 HITL 澄清态（AskUserQuestion 暂停中）。非空时前端
    # hydrate 须重建提问卡片；用户作答 / 作废后由后端清除。
    clarification: dict[str, Any] | None = None
    updated_at: int = 0


class _CustomPayloadRunProjection(BaseModel):
    custom_type: Literal["run_projection"]
    data: RunProjectionPayload


# 透传型快照：data 是各自运行时模块写入的原始结构，schema 层不重复建模。
class _CustomPayloadCacheStrategy(BaseModel):
    custom_type: Literal["cache_strategy_snapshot"]
    data: dict[str, Any]


class _CustomPayloadParallelTaskRuntime(BaseModel):
    custom_type: Literal["parallel_task_runtime"]
    data: dict[str, Any]


class _CustomPayloadRunTakeoverState(BaseModel):
    custom_type: Literal["run_takeover_state"]
    data: dict[str, Any]


class _CustomPayloadContextCompactionReference(BaseModel):
    custom_type: Literal["context_compaction_reference"]
    data: dict[str, Any]


class _CustomPayloadRunSnapshot(BaseModel):
    custom_type: Literal["run_snapshot"]
    data: dict[str, Any]


class _CustomPayloadSubagentSession(BaseModel):
    custom_type: Literal["subagent_session"]
    data: dict[str, Any]


class _CustomPayloadSubagentRun(BaseModel):
    custom_type: Literal["subagent_run"]
    data: dict[str, Any]


class _CustomPayloadSubagentToolEffect(BaseModel):
    custom_type: Literal["subagent_tool_effect"]
    data: dict[str, Any]


CustomPayload = Annotated[
    _CustomPayloadCacheStrategy
    | _CustomPayloadTurnMetrics
    | _CustomPayloadTaskRuntime
    | _CustomPayloadTaskSpec
    | _CustomPayloadEvidenceRecord
    | _CustomPayloadReviewDecision
    | _CustomPayloadTaskMutation
    | _CustomPayloadForkTask
    | _CustomPayloadParallelTaskRuntime
    | _CustomPayloadHandoff
    | _CustomPayloadSearch
    | _CustomPayloadExtract
    | _CustomPayloadSessionMemory
    | _CustomPayloadSessionState
    | _CustomPayloadReplacementDecision
    | _CustomPayloadRunProjection
    | _CustomPayloadRunTakeoverState
    | _CustomPayloadContextCompactionReference
    | _CustomPayloadRunSnapshot
    | _CustomPayloadSubagentSession
    | _CustomPayloadSubagentRun
    | _CustomPayloadSubagentToolEffect
    | _CustomPayloadFeedback
    | _CustomPayloadOutputFiles
    | _CustomPayloadFileChanges
    | _CustomPayloadExecutionTimeline
    | _CustomPayloadReferenceSources
    | _CustomPayloadResultCompactionReference
    | _CustomPayloadDecisionLedgerEvent
    | _CustomPayloadTransientRetryNotice
    | _CustomPayloadFactSheet,
    Field(discriminator="custom_type"),
]

_CustomPayloadAdapter: TypeAdapter[CustomPayload] = TypeAdapter(CustomPayload)

CustomType = Literal[
    "cache_strategy_snapshot",
    "turn_runtime_metrics",
    "task_runtime_snapshot",
    "task_spec_snapshot",
    "evidence_ledger",
    "review_decision_record",
    "task_mutation",
    "fork_task_projection",
    "parallel_task_runtime",
    "handoff_request",
    "search_evidence",
    "extract_evidence",
    "session_memory_snapshot",
    "session_state_snapshot",
    "replacement_decision",
    "run_projection",
    "feedback",
    "output_files",
    "file_changes",
    "execution_timeline",
    "reference_sources",
    "context_compaction_reference",
    "result_compaction_reference",
    "decision_ledger_event",
    "transient_retry_notice",
    "run_snapshot",
    "run_takeover_state",
    "fact_sheet",
    "subagent_session",
    "subagent_run",
    "subagent_tool_effect",
]

# 读取侧允许遇到不认识的 custom_type（旧/新版本写入），按 None 跳过。
_KNOWN_CUSTOM_TYPES = frozenset(get_args(CustomType))


def generate_entry_id(existing_ids: set[str], max_attempts: int = 100) -> str:
    for _ in range(max_attempts):
        candidate = secrets.token_hex(4)
        if candidate not in existing_ids:
            return candidate
    return secrets.token_hex(16)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def now_ms_epoch() -> int:
    """Return current UTC time as epoch milliseconds."""
    return int(time.time() * 1000)


class CompactionEntry(SessionEntryBase):
    type: Literal["compaction"] = "compaction"
    summary: str
    first_kept_entry_id: str | None = None
    tokens_before: int
    details: dict[str, Any] | None = None


class ModelChangeEntry(SessionEntryBase):
    type: Literal["model_change"] = "model_change"
    provider: str
    model_id: str


class CustomEntry(SessionEntryBase):
    type: Literal["custom"] = "custom"
    custom_type: CustomType
    data: dict[str, Any] | None = None

    def get_typed_data(
        self,
    ) -> BaseModel | dict[str, Any] | dict[str, str] | TurnRuntimeMetricsSnapshot | None:
        """Return data as a typed model based on custom_type.

        Uses the CustomPayload discriminated union to validate and coerce
        the raw dict data into a properly typed payload. Returns None
        if validation fails or data is None.
        """
        if self.data is None:
            return None
        payload = _coerce_custom_payload(self.custom_type, self.data)
        if payload is not None:
            return payload.data
        return self.data


def _coerce_custom_payload(
    custom_type: str,
    data: dict[str, Any],
) -> CustomPayload | None:
    """Coerce a raw dict into a typed CustomPayload based on custom_type.

    Returns the typed CustomPayload if validation succeeds, None otherwise.
    This enables type-safe access to CustomEntry.data without breaking
    backward compatibility with raw dict storage.
    """
    try:
        payload: CustomPayload = _CustomPayloadAdapter.validate_python(
            {"custom_type": custom_type, "data": data}
        )
        return payload
    except Exception:
        return None


SessionEntry = Annotated[
    MessageEntry | CompactionEntry | ModelChangeEntry | CustomEntry,
    Field(discriminator="type"),
]


def _event_to_message_entry(
    data: dict[str, Any],
    *,
    role: Literal["user", "assistant", "tool"],
    seq: int = 0,
) -> MessageEntry | None:
    """Build a typed MessageEntry from an event's data payload.

    This is the typed counterpart to build_user_message_entry /
    build_assistant_message_entry / build_tool_result_entry in event_logger.py.
    """
    entry_id = data.get("entry_id") or ""
    if not entry_id:
        role_prefix = {"user": "msg_user", "assistant": "msg_asst", "tool": "msg_tool"}.get(
            role, "msg"
        )
        entry_id = f"{role_prefix}_{data.get('type', '')}"

    raw_timestamp = data.get("timestamp")
    timestamp = raw_timestamp if isinstance(raw_timestamp, str) and raw_timestamp else now_iso()

    if role == "tool":
        return MessageEntry(
            id=entry_id,
            parent_id=data.get("parent_id"),
            timestamp=timestamp,
            seq=seq,
            role="tool",
            content=data.get("content", ""),
            enriched_content=data.get("enriched_content"),
            tool_calls=None,
            tool_call_id=data.get("tool_call_id"),
            partial=False,
            is_error=bool(data.get("is_error") or False),
            tool_name=data.get("tool_name"),
            metadata=data.get("metadata"),
        )

    # assistant role: reconstruct tool_calls from inline event data so
    # SOP extraction / replay can match candidates by tool_call.id.
    # Falls back to None when absent (older events predate inline tool_calls).
    raw_tool_calls = data.get("tool_calls")
    if not isinstance(raw_tool_calls, list):
        raw_tool_calls = None
    return MessageEntry(
        id=entry_id,
        parent_id=data.get("parent_id"),
        timestamp=timestamp,
        seq=seq,
        role=role,
        content=data.get("content", ""),
        enriched_content=data.get("enriched_content"),
        tool_calls=raw_tool_calls,
        tool_call_id=None,
        partial=False,
        client_message_id=data.get("client_message_id"),
        metadata=data.get("metadata"),
    )


def _repair_parent_chain(tree: SessionTree) -> None:
    """Legacy-data repair for sessions persisted before message events
    carried parent_id.

    Symptom being repaired: every entry loaded back with parent_id=None
    breaks get_branch()'s leaf→root walk — the branch collapses to a single
    entry, so build_runtime_messages() sends the LLM only [last entry of the
    previous turn, current user message] — i.e. cross-turn "amnesia".

    For linear sessions the append order IS the branch order, so entries
    whose parent_id is None are relinked from the order list. Entries with
    an existing parent_id are never touched. Entries absent from order
    (e.g. CompactionEntry committed via entry_updated, which updates the
    entries hash but not order) are re-attached:

    - if an ordered entry references them as parent, they sit between that
      child and the child's order-predecessor (mid-chain position);
    - otherwise they are newest (e.g. the leaf) and attach to the last
      ordered entry.

    Both rules only ever link an unordered entry into the ordered linear
    chain, which is acyclic by construction — no repair can create a cycle.
    """
    if not tree.order:
        return
    order_set = set(tree.order)
    prev_id: str | None = None
    for eid in tree.order:
        entry = tree.entries.get(eid)
        if entry is None:
            continue
        if entry.parent_id == eid:
            entry.parent_id = None
        if entry.parent_id is None and prev_id is not None:
            entry.parent_id = prev_id
        prev_id = eid

    # Earliest ordered child per unordered entry id (first reference wins).
    first_child_of: dict[str, str] = {}
    for eid in tree.order:
        entry = tree.entries.get(eid)
        if entry is None or entry.parent_id is None or entry.parent_id in order_set:
            continue
        first_child_of.setdefault(entry.parent_id, eid)

    last_ordered = tree.order[-1]
    for entry in tree.entries.values():
        if entry.id in order_set or entry.parent_id is not None:
            continue
        if entry.parent_id == entry.id:
            entry.parent_id = None
        child = first_child_of.get(entry.id)
        if child is not None:
            idx = tree.order.index(child)
            entry.parent_id = tree.order[idx - 1] if idx > 0 else None
        elif entry.id != last_ordered:
            entry.parent_id = last_ordered


def _parse_entry(data: dict[str, Any]) -> SessionEntry | None:
    """Validate and construct a typed SessionEntry from a raw dict.

    Replaces the untyped parse_entry() in event_logger.py with proper
    discriminated union validation.

    未知 custom_type 视为不可识别扩展并跳过：会话 event log 是只追加的，
    旧/新版本写入的类型不应让读取方报错。调用方按既有 None 语义处理。
    """
    entry_type = data.get("type")
    if entry_type == "message":
        return MessageEntry.model_validate(data)
    if entry_type == "compaction":
        return CompactionEntry.model_validate(data)
    if entry_type == "model_change":
        return ModelChangeEntry.model_validate(data)
    if entry_type == "custom":
        custom_type = data.get("custom_type")
        if custom_type not in _KNOWN_CUSTOM_TYPES:
            logger.debug("skip unknown custom entry type=%s", custom_type)
            return None
        return CustomEntry.model_validate(data)
    return None


class SessionTree(BaseModel):
    header: SessionHeader
    entries: dict[str, SessionEntry] = Field(default_factory=dict)
    order: list[str] = Field(default_factory=list)
    leaf_id: str | None = None

    @classmethod
    def from_events(cls, events: list[SessionEvent]) -> SessionTree:
        """Rebuild SessionTree from SessionEvent stream (event-sourcing projection).

        Processes events in seq order to reconstruct the current state.
        This is the primary read-time projection path — SessionEvent is the
        source of truth, SessionTree is a derived read model.
        """
        header: SessionHeader | None = None
        entries: dict[str, SessionEntry] = {}
        order: list[str] = []
        leaf_id: str | None = None

        for event in events:
            data = event.data
            etype = event.type

            if etype == SessionEventType.SESSION_CREATED:
                if "header" in data:
                    header = SessionHeader.model_validate(data["header"])
            elif etype == SessionEventType.REQUEST_HEADER:
                if "header" in data and header is not None:
                    header = header.model_copy(update=data["header"])
                    # Partial header updates (e.g. last_interaction_at) arriving
                    # before any SESSION_CREATED must not crash the replay —
                    # the caller falls back to the stored header key.
            elif etype == SessionEventType.USER_MESSAGE:
                entry = _event_to_message_entry(data, role="user", seq=event.seq)
                if entry:
                    entries[entry.id] = entry
                    order.append(entry.id)
                    leaf_id = entry.id
            elif etype == SessionEventType.ASSISTANT_MESSAGE:
                entry = _event_to_message_entry(data, role="assistant", seq=event.seq)
                if entry:
                    entries[entry.id] = entry
                    order.append(entry.id)
                    leaf_id = entry.id
            elif etype == SessionEventType.TOOL_RESULT:
                entry = _event_to_message_entry(data, role="tool", seq=event.seq)
                if entry:
                    entries[entry.id] = entry
                    order.append(entry.id)
                    leaf_id = entry.id
            elif etype == SessionEventType.ENTRY_UPDATED and "entry" in data:
                updated_entry = _parse_entry(data["entry"])
                if updated_entry:
                    updated_entry.seq = event.seq
                    entries[updated_entry.id] = updated_entry
                if "leaf_id" in data:
                    leaf_id = data["leaf_id"]

        if header is None:
            header = SessionHeader(id="", workspace_id="", agent_id="")

        tree = cls(
            header=header,
            entries=entries,
            order=order,
            leaf_id=leaf_id,
        )
        _repair_parent_chain(tree)
        return tree

    def append(self, entry: SessionEntry) -> str:
        if entry.id in self.entries:
            raise ValueError(f"entry id {entry.id} already exists")
        self.entries[entry.id] = entry
        self.order.append(entry.id)
        self.leaf_id = entry.id
        return entry.id

    def get_entry(self, entry_id: str) -> SessionEntry | None:
        return self.entries.get(entry_id)

    def get_children(self, parent_id: str | None) -> list[SessionEntry]:
        return [e for e in self.entries.values() if e.parent_id == parent_id]

    def get_branch(self, from_id: str | None = None) -> list[SessionEntry]:
        start = from_id or self.leaf_id
        if start is None:
            return []
        path: list[SessionEntry] = []
        seen: set[str] = set()
        current: str | None = start
        while current is not None:
            if current in seen:
                # Cycle guard: corrupted parent links must degrade to a
                # truncated branch, never an infinite loop.
                break
            seen.add(current)
            entry = self.entries.get(current)
            if entry is None:
                break
            path.append(entry)
            current = entry.parent_id
        path.reverse()
        return path

    def all_entry_ids(self) -> set[str]:
        return set(self.entries.keys())
