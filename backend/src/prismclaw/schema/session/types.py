from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone

from typing import Annotated, Any, Literal, TYPE_CHECKING, Union

from pydantic import BaseModel, Field, TypeAdapter

from prismclaw.schema.content import (
    CompactionArtifact,
)
from prismclaw.schema.agent.data_types import ContentBlock
from prismclaw.schema.agent.session import (
    AgentMessageDict,
    MessageEntry,
    SessionContextState,
    SessionEntryBase,
    SessionEvent,
    SessionEventType,
    SessionHeader,
    SystemReminderProjection,
    TurnRuntimeMetricsSnapshot,
)
from prismclaw.schema.agent.task_types import TaskSpec
from prismclaw.schema.agent.task_runtime import (
    EvidenceRecord,
    ReviewDecisionRecord,
    ReviewRequirements,
    TaskMutation,
    TaskRuntimeState,
)

if TYPE_CHECKING:
    from prismclaw.schema.content.replacement_state import ReplacementDecisionState

_TURN_RUNTIME_METRICS_CUSTOM_TYPE = "turn_runtime_metrics"
TURN_RUNTIME_METRICS_CUSTOM_TYPE = "turn_runtime_metrics"
FORK_TASK_PROJECTION_CUSTOM_TYPE = "fork_task_projection"
HANDOFF_REQUEST_CUSTOM_TYPE = "handoff_request"

# Typed custom_type constants (replace string matching with these)
SEARCH_EVIDENCE_CUSTOM_TYPE = "search_evidence"
EXTRACT_EVIDENCE_CUSTOM_TYPE = "extract_evidence"
TASK_RUNTIME_CUSTOM_TYPE = "task_runtime_snapshot"
TASK_SPEC_CUSTOM_TYPE = "task_spec_snapshot"
REVIEW_REQUIREMENTS_CUSTOM_TYPE = "review_requirements_snapshot"
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
DECISION_LEDGER_EVENT_CUSTOM_TYPE = "decision_ledger_event"
TRANSIENT_RETRY_NOTICE_CUSTOM_TYPE = "transient_retry_notice"
RUN_PROJECTION_CUSTOM_TYPE = "run_projection"

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


class _CustomPayloadReviewRequirements(BaseModel):
    custom_type: Literal["review_requirements_snapshot"]
    data: ReviewRequirements


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
    def data(self) -> dict[str, str]:  # type: ignore[override]
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
    timeline_nodes: list[dict] = []
    task_runtime: dict = {}
    phase: str | None = None
    phase_at: int | None = None
    # 待用户回答的 HITL 澄清态（AskUserQuestion 暂停中）。非空时前端
    # hydrate 须重建提问卡片；用户作答 / 作废后由后端清除。
    clarification: dict | None = None
    updated_at: int = 0


class _CustomPayloadRunProjection(BaseModel):
    custom_type: Literal["run_projection"]
    data: RunProjectionPayload


CustomPayload = Annotated[
    Union[
        _CustomPayloadTurnMetrics,
        _CustomPayloadTaskRuntime,
        _CustomPayloadTaskSpec,
        _CustomPayloadReviewRequirements,
        _CustomPayloadEvidenceRecord,
        _CustomPayloadReviewDecision,
        _CustomPayloadTaskMutation,
        _CustomPayloadForkTask,
        _CustomPayloadHandoff,
        _CustomPayloadSearch,
        _CustomPayloadExtract,
        _CustomPayloadSessionMemory,
        _CustomPayloadSessionState,
        _CustomPayloadReplacementDecision,
        _CustomPayloadRunProjection,
        _CustomPayloadFeedback,
        _CustomPayloadOutputFiles,
        _CustomPayloadFileChanges,
        _CustomPayloadExecutionTimeline,
        _CustomPayloadResultCompactionReference,
        _CustomPayloadDecisionLedgerEvent,
        _CustomPayloadTransientRetryNotice,
        _CustomPayloadFactSheet,
    ],
    Field(discriminator="custom_type"),
]

_CustomPayloadAdapter = TypeAdapter(CustomPayload)

CustomType = Literal[
    "cache_strategy_snapshot",
    "turn_runtime_metrics",
    "task_runtime_snapshot",
    "task_spec_snapshot",
    "review_requirements_snapshot",
    "evidence_ledger",
    "review_decision_record",
    "task_mutation",
    "fork_task_projection",
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
    "context_compaction_reference",
    "result_compaction_reference",
    "decision_ledger_event",
    "transient_retry_notice",
    "run_snapshot",
    "fact_sheet",
]


def generate_entry_id(existing_ids: set[str], max_attempts: int = 100) -> str:
    for _ in range(max_attempts):
        candidate = secrets.token_hex(4)
        if candidate not in existing_ids:
            return candidate
    return secrets.token_hex(16)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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

    def get_typed_data(self) -> Any | None:
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
        payload = _CustomPayloadAdapter.validate_python(
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
) -> MessageEntry | None:
    """Build a typed MessageEntry from an event's data payload.

    This is the typed counterpart to build_user_message_entry /
    build_assistant_message_entry / build_tool_result_entry in event_logger.py.
    """
    entry_id = data.get("entry_id") or ""
    if not entry_id:
        role_prefix = {"user": "msg_user", "assistant": "msg_asst", "tool": "msg_tool"}.get(role, "msg")
        entry_id = f"{role_prefix}_{data.get('type', '')}"

    raw_timestamp = data.get("timestamp")
    timestamp = raw_timestamp if isinstance(raw_timestamp, str) and raw_timestamp else now_iso()

    if role == "tool":
        return MessageEntry(
            id=entry_id,
            parent_id=data.get("parent_id"),
            timestamp=timestamp,
            role="tool",
            content=data.get("content", ""),
            enriched_content=data.get("enriched_content"),
            tool_calls=None,
            tool_call_id=data.get("tool_call_id"),
            partial=False,
            is_error=bool(data.get("is_error") or False),
            tool_name=data.get("tool_name"),
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
        role=role,
        content=data.get("content", ""),
        enriched_content=data.get("enriched_content"),
        tool_calls=raw_tool_calls,
        tool_call_id=None,
        partial=False,
    )



def _repair_parent_chain(tree: "SessionTree") -> None:
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
    """
    entry_type = data.get("type")
    if entry_type == "message":
        return MessageEntry.model_validate(data)
    if entry_type == "compaction":
        return CompactionEntry.model_validate(data)
    if entry_type == "model_change":
        return ModelChangeEntry.model_validate(data)
    if entry_type == "custom":
        return CustomEntry.model_validate(data)
    return None


class SessionTree(BaseModel):
    header: SessionHeader
    entries: dict[str, SessionEntry] = Field(default_factory=dict)
    order: list[str] = Field(default_factory=list)
    leaf_id: str | None = None
    todos: list[dict[str, Any]] = Field(default_factory=list)

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
        todos: list[dict[str, Any]] = []

        for event in events:
            data = event.data
            etype = event.type

            if etype == SessionEventType.SESSION_CREATED:
                if "header" in data:
                    header = SessionHeader.model_validate(data["header"])
            elif etype == SessionEventType.REQUEST_HEADER:
                if "header" in data:
                    if header is not None:
                        header = header.model_copy(update=data["header"])
                    # Partial header updates (e.g. last_interaction_at) arriving
                    # before any SESSION_CREATED must not crash the replay —
                    # the caller falls back to the stored header key.
            elif etype == SessionEventType.USER_MESSAGE:
                entry = _event_to_message_entry(data, role="user")
                if entry:
                    entries[entry.id] = entry
                    order.append(entry.id)
                    leaf_id = entry.id
            elif etype == SessionEventType.ASSISTANT_MESSAGE:
                entry = _event_to_message_entry(data, role="assistant")
                if entry:
                    entries[entry.id] = entry
                    order.append(entry.id)
                    leaf_id = entry.id
            elif etype == SessionEventType.TOOL_RESULT:
                entry = _event_to_message_entry(data, role="tool")
                if entry:
                    entries[entry.id] = entry
                    order.append(entry.id)
                    leaf_id = entry.id
            elif etype == SessionEventType.ENTRY_UPDATED:
                if "entry" in data:
                    entry = _parse_entry(data["entry"])
                    if entry:
                        entries[entry.id] = entry
                    if "leaf_id" in data:
                        leaf_id = data["leaf_id"]
            elif etype == SessionEventType.TODO_WRITE:
                todos = data.get("todos", [])

        if header is None:
            header = SessionHeader(id="", workspace_id="", agent_id="")

        tree = cls(
            header=header,
            entries=entries,
            order=order,
            leaf_id=leaf_id,
            todos=todos,
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

    def _proj(self):
        from prismclaw.schema.session.projection import SessionProjectionEngine
        return SessionProjectionEngine(self)

    def get_latest_task_runtime(self) -> TaskRuntimeState | None:
        return self._proj().task.get_latest_task_runtime()

    def get_latest_task_spec(self) -> TaskSpec | None:
        return self._proj().task.get_latest_task_spec()

    def get_latest_review_requirements(self) -> ReviewRequirements | None:
        return self._proj().task.get_latest_review_requirements()

    def get_latest_review_decision(self) -> ReviewDecisionRecord | None:
        return self._proj().task.get_latest_review_decision()

    def get_evidence_ledger(self) -> list[EvidenceRecord]:
        return self._proj().task.get_evidence_ledger()

    def get_history_compaction_count(self) -> int:
        return self._proj().history_compaction.get_count()

    def get_latest_history_compaction_reduction(self) -> int:
        return self._proj().history_compaction.get_latest_reduction()

    def get_turn_runtime_metrics_snapshots(self) -> list[TurnRuntimeMetricsSnapshot]:
        return self._proj().cache.get_turn_metrics_snapshots()

    def get_replacement_decision_state(self) -> ReplacementDecisionState:
        return self._proj().replacement.get_state()

    def build_runtime_messages(self) -> list[AgentMessageDict]:
        return self._proj().messages.build_runtime_messages()

    def build_runtime_meta_messages(self) -> list[SystemReminderProjection]:
        return self._proj().messages.build_runtime_meta_messages()

    def get_persisted_artifacts(self) -> dict[str, CompactionArtifact]:
        return self._proj().replacement.get_persisted_artifacts()

    def get_latest_session_memory_state(self) -> SessionContextState | None:
        return self._proj().get_latest_session_memory_state()


def _effective_fork_task_projection_entries(
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    effective_reversed: list[dict[str, Any]] = []
    seen_task_ids: set[str] = set()
    for entry in reversed(entries):
        task_id = entry.get("task_id")
        if isinstance(task_id, str) and task_id:
            if task_id in seen_task_ids:
                continue
            seen_task_ids.add(task_id)
        effective_reversed.append(dict(entry))
    effective_reversed.reverse()
    return effective_reversed


def _latest_custom_entry_index(
    tree: SessionTree,
    *,
    custom_type: str,
) -> int | None:
    branch = tree.get_branch()
    for index in range(len(branch) - 1, -1, -1):
        entry = branch[index]
        if isinstance(entry, CustomEntry) and entry.custom_type == custom_type:
            return index
    return None


def _message_entry_to_dict(
    entry: MessageEntry,
    *,
    content_override: str | list[ContentBlock] | None = None,
) -> AgentMessageDict:
    content: Any
    if content_override is not None:
        entry_content = content_override
    elif entry.enriched_content is not None:
        entry_content = entry.enriched_content
    else:
        entry_content = entry.content
    if entry.role == "tool" and isinstance(entry_content, str):
        content = [{"type": "text", "text": entry_content}]
    elif isinstance(entry_content, list):
        content = _content_blocks_to_llm(entry_content)
    else:
        content = entry_content
    msg: AgentMessageDict = {
        "role": entry.role,
        "content": content,
        "__entry_id__": entry.id,  # type: ignore
    }
    if entry.tool_calls:
        msg["tool_calls"] = entry.tool_calls
    if entry.tool_call_id:
        msg["tool_call_id"] = entry.tool_call_id
    return msg


def _collect_custom_models(
    branch: list[SessionEntry],
    custom_type: str,
    model_cls: type[BaseModel],
) -> list[Any]:
    models: list[Any] = []
    for entry in branch:
        if not isinstance(entry, CustomEntry) or entry.custom_type != custom_type:
            continue
        if not isinstance(entry.data, dict):
            continue
        try:
            models.append(model_cls.model_validate(entry.data))
        except Exception:
            continue
    return models


def _find_latest_custom_model(
    branch: list[SessionEntry],
    custom_type: str,
    model_cls: type[BaseModel],
) -> Any | None:
    for entry in reversed(branch):
        if not isinstance(entry, CustomEntry) or entry.custom_type != custom_type:
            continue
        if not isinstance(entry.data, dict):
            continue
        try:
            return model_cls.model_validate(entry.data)
        except Exception:
            continue
    return None


def _as_optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None




def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item:
            result.append(item)
    return result




def _collect_turn_runtime_metrics_snapshots(
    branch: list[SessionEntry],
) -> list[TurnRuntimeMetricsSnapshot]:
    snapshots: list[TurnRuntimeMetricsSnapshot] = []
    for entry in branch:
        if not isinstance(entry, CustomEntry) or entry.custom_type != _TURN_RUNTIME_METRICS_CUSTOM_TYPE:
            continue
        snapshot = _coerce_turn_runtime_metrics_snapshot(entry.data)
        if snapshot is not None:
            snapshots.append(snapshot)
    return snapshots


def _coerce_turn_runtime_metrics_snapshot(data: Any) -> TurnRuntimeMetricsSnapshot | None:
    if not isinstance(data, dict):
        return None
    request_mode = data.get("request_mode")
    dynamic_payload = data.get("dynamic_payload")
    input_tokens = data.get("input_tokens")
    cache_read_tokens = data.get("cache_read_tokens")
    result_payload_reduction = data.get("result_payload_reduction")
    if not isinstance(request_mode, str):
        return None
    if not isinstance(dynamic_payload, int):
        return None
    if not isinstance(input_tokens, int):
        return None
    if not isinstance(cache_read_tokens, int):
        return None
    if not isinstance(result_payload_reduction, int):
        return None
    snapshot = TurnRuntimeMetricsSnapshot(
        request_mode=request_mode,
        dynamic_payload=dynamic_payload,
        input_tokens=input_tokens,
        cache_read_tokens=cache_read_tokens,
        result_payload_reduction=result_payload_reduction,
    )
    return snapshot


def _history_compaction_reduction(entry: CompactionEntry) -> int:
    tokens_before = entry.tokens_before if entry.tokens_before > 0 else 0
    tokens_after: int | None = None
    details = entry.details or {}
    raw_tokens_after = details.get("tokens_after")
    if isinstance(raw_tokens_after, int):
        tokens_after = raw_tokens_after
    elif isinstance(raw_tokens_after, float):
        tokens_after = int(raw_tokens_after)
    if tokens_after is None:
        return 0
    return max(0, tokens_before - max(0, tokens_after))
def _content_blocks_to_llm(blocks: list[ContentBlock]) -> list[dict[str, Any]]:
    from prismclaw.schema.content.content_types import FileBlock, ImageBlock, TextBlock
    from prismclaw.schema.shared.utils import file_reference_text

    result: list[dict[str, Any]] = []
    for block in blocks:
        if isinstance(block, ImageBlock):
            result.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{block.mime_type};base64,{block.data}"},
                }
            )
        elif isinstance(block, TextBlock):
            if not block.text or not block.text.strip():
                continue
            result.append({"type": "text", "text": block.text})
        elif isinstance(block, FileBlock):
            if not block.path or not block.path.strip():
                continue
            result.append(
                {
                    "type": "text",
                    "text": file_reference_text(
                        block.path.strip(),
                        name=block.name,
                        mime_type=block.mime_type,
                    ),
                }
            )
    return result

