from __future__ import annotations

"""Session-related shared types and context state.

Consolidates session_types.py (MessageEntry, SessionEvent, etc.) and
session_state.py (SessionContextState, etc.) into a single module
to reduce file fragmentation in contracts/agent/.
"""

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from typing_extensions import TypedDict

from pydantic import BaseModel, ConfigDict, Field

from prismclaw.schema.agent.data_types import ContentBlock


# ── Helper functions (local copies to avoid contracts → kernel coupling) ──

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_ms_epoch() -> int:
    """Return current UTC time as epoch milliseconds (DSH time-seeking)."""
    return int(time.time() * 1000)


# ── TypedDict / dataclass types (pre-existing) ──────────────────────────

class TurnRuntimeMetricsSnapshot(TypedDict):
    request_mode: str
    dynamic_payload: int
    input_tokens: int
    cache_read_tokens: int
    result_payload_reduction: int


class AgentMessageDict(TypedDict, total=False):
    role: str
    content: Any
    tool_calls: list[dict[str, Any]]
    tool_call_id: str
    name: str
    __entry_id__: str


@dataclass(frozen=True)
class SystemReminderProjection:
    kind: Literal["text", "budget_warning", "history_compaction_summary"] = "text"
    text: str | None = None
    total_usd_cost: float | None = None
    budget_usd: float | None = None
    summary_text: str | None = None
    role: Literal["user"] = "user"
    entry_id: str | None = None

    @classmethod
    def text_reminder(
        cls,
        text: str,
        *,
        role: Literal["user"] = "user",
        entry_id: str | None = None,
    ) -> "SystemReminderProjection":
        return cls(
            kind="text",
            text=text,
            role=role,
            entry_id=entry_id,
        )

    @classmethod
    def budget_warning(
        cls,
        *,
        total_usd_cost: float,
        budget_usd: float,
    ) -> "SystemReminderProjection":
        return cls(
            kind="budget_warning",
            total_usd_cost=total_usd_cost,
            budget_usd=budget_usd,
        )

    @classmethod
    def history_compaction_summary(
        cls,
        *,
        summary_text: str,
        entry_id: str | None = None,
    ) -> "SystemReminderProjection":
        return cls(
            kind="history_compaction_summary",
            summary_text=summary_text,
            entry_id=entry_id,
        )


ProjectionInputMessage = AgentMessageDict | SystemReminderProjection


# ── Session data models (moved from kernel/session/types.py) ─────────────

class SessionHeader(BaseModel):
    type: Literal["session"] = "session"
    version: int = 3
    id: str
    workspace_id: str
    agent_id: str
    created_at: str = Field(default_factory=_now_iso)
    parent_session: str | None = None
    session_key: str = ""
    last_interaction_at: str | None = Field(default_factory=_now_iso)
    idle_minutes_override: int | None = None
    model_override: str | None = None
    title: str | None = None
    message_count: int = 0  # 冗余字段：会话中 message 类型 entry 的数量，写入时递增


class SessionHistorySummary(BaseModel):
    session_id: str
    created_at: str
    message_count: int
    last_message_at: str | None
    title: str | None = None  # 冗余字段：直接从 header 读取，避免额外加载
    parent_session_id: str | None = None  # 冗余字段：header.parent_session


class SessionEntryBase(BaseModel):
    id: str
    parent_id: str | None
    timestamp: str = Field(default_factory=_now_iso)


class MessageEntry(SessionEntryBase):
    type: Literal["message"] = "message"
    role: Literal["user", "assistant", "tool"]
    content: str | list[ContentBlock]
    enriched_content: str | list[ContentBlock] | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    partial: bool = False
    # tool-result failure flag (DSH parity — drives the red timeline span
    # and the ledger error marker). Set by the tool execution loop when the
    # ToolResult carries is_error.
    is_error: bool = False
    # tool cell → tool name (used for schema_detail resolution in the
    # trajectory projector). Set by the tool execution loop.
    tool_name: str | None = None
    # 任意业务标记（向后兼容，默认 None）。当前用途：HITL 澄清追问答案标记为
    # {"kind": "clarification_answer", "ask_tool_call_id": ...}，供
    # _build_display_messages 识别并抑制为独立气泡（答案已内嵌到提问步骤的时间线）。
    metadata: dict | None = None


class SessionEventType(str, Enum):
    """Session event types — aligned with DSH SessionEventMap.

    Lifecycle: turn → step → message → tool → result
    """

    # Turn / Step lifecycle
    TURN_STARTED = "turn/start"
    TURN_COMPLETED = "turn/end"
    STEP_STARTED = "step/start"
    STEP_COMPLETED = "step/end"

    # Model-visible surface (LLM sees these)
    USER_MESSAGE = "user/message"
    ASSISTANT_CHUNK = "assistant/chunk"
    ASSISTANT_MESSAGE = "assistant/message"

    # Tool execution
    TOOL_RESULT = "tool/result"

    # Session / request metadata (log-only or snapshot)
    REQUEST_HEADER = "request/header"
    REQUEST_CONTEXT = "request/context"
    TODO_WRITE = "todo/write"
    SESSION_CREATED = "session_created"
    SESSION_DELETED = "session_deleted"
    SESSION_END_SEED = "session/end-seed"

    # PrismClaw-specific extensions
    ENTRY_UPDATED = "entry_updated"   # compaction / microcompaction patch


class SessionEvent(BaseModel):
    """Immutable session event — core of Event Sourcing.

    Aligned with DSH SessionEvent type:
    - seq: monotonic sequence number (Redis INCR, cross-worker safe)
    - time: epoch milliseconds (int, sortable, time-range query support)
    - type: event type from SessionEventMap
    - data: flexible dict payload (Event Sourcing — not enforced at runtime)
    - ignorable: reader may safely skip when type is unknown
    - source_event_seqs: seqs of earlier events this cites (traceability)

    B/S architecture:
    - session_id is implicit in the Event Log key, kept for convenience
    - Events are append-only — once written, never modified
    """

    seq: int
    type: SessionEventType
    session_id: str
    time: int = Field(default_factory=_now_ms_epoch)
    data: dict[str, Any] = Field(default_factory=dict)
    ignorable: bool = False
    source_event_seqs: list[int] = Field(default_factory=list)


# ── Session context state (moved from kernel/agent.py) ───────────────────

SessionContextItemKind = Literal[
    "fact",
    "constraint",
    "decision",
    "todo",
    "assumption",
    "question",
]


class SessionContextItem(BaseModel):
    id: str
    kind: SessionContextItemKind
    content: str
    status: Literal["active", "superseded", "uncertain"] = "active"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_refs: list[str] = Field(default_factory=list)


class SessionStateUpdate(BaseModel):
    primary_goal: str | None = None
    facts: list[SessionContextItem] = Field(default_factory=list)
    constraints: list[SessionContextItem] = Field(default_factory=list)
    decisions: list[SessionContextItem] = Field(default_factory=list)
    todos: list[SessionContextItem] = Field(default_factory=list)
    assumptions: list[SessionContextItem] = Field(default_factory=list)
    open_questions: list[SessionContextItem] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)


class GoalRecord(BaseModel):
    goal_id: str
    objective: str
    status: str = "active"
    max_goal_rounds: int = 20
    current_round: int = 0
    blocked_count: int = 0


class TodoItem(BaseModel):
    id: str
    content: str
    status: str = "pending"


class SessionContextState(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: str
    primary_goal: str | None = None
    facts: list[SessionContextItem] = Field(default_factory=list)
    constraints: list[SessionContextItem] = Field(default_factory=list)
    decisions: list[SessionContextItem] = Field(default_factory=list)
    todos: list[SessionContextItem] = Field(default_factory=list)
    todo_list: list[TodoItem] = Field(default_factory=list)
    goals: list[GoalRecord] = Field(default_factory=list)
    assumptions: list[SessionContextItem] = Field(default_factory=list)
    open_questions: list[SessionContextItem] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    recent_messages: list[str] = Field(default_factory=list)

    def record_recent_message(self, role: str, content: str) -> None:
        entry = f"[{role}] {content}"
        if entry in self.recent_messages:
            self.recent_messages.remove(entry)
        self.recent_messages.append(entry)
        if len(self.recent_messages) > 6:
            self.recent_messages = self.recent_messages[-6:]

    def has_content(self) -> bool:
        return bool(
            self.primary_goal
            or any(item.status == "active" for item in self.facts)
            or any(item.status == "active" for item in self.constraints)
            or any(item.status == "active" for item in self.decisions)
            or any(item.status == "active" for item in self.todos)
            or any(item.status != "completed" for item in self.todo_list)
            or any(goal.status == "active" for goal in self.goals)
            or any(item.status == "active" for item in self.assumptions)
            or any(item.status == "active" for item in self.open_questions)
            or self.evidence
            or self.recent_messages
        )

    def apply_update(self, update: SessionStateUpdate) -> None:
        if update.primary_goal:
            self.primary_goal = update.primary_goal
        self.facts = _merge_session_items(self.facts, update.facts)
        self.constraints = _merge_session_items(self.constraints, update.constraints)
        self.decisions = _merge_session_items(self.decisions, update.decisions)
        self.todos = _merge_session_items(self.todos, update.todos)
        self.assumptions = _merge_session_items(self.assumptions, update.assumptions)
        self.open_questions = _merge_session_items(
            self.open_questions, update.open_questions
        )
        if update.evidence:
            existing_keys = {
                f"{e.get('source_path', '')}:{e.get('quoted_text', '')}"
                for e in self.evidence
            }
            for item in update.evidence:
                key = f"{item.get('source_path', '')}:{item.get('quoted_text', '')}"
                if key not in existing_keys:
                    self.evidence.append(item)
                    existing_keys.add(key)


# ── Internal merge helpers ──

_STATE_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_STATE_STOPWORDS = {
    "the", "a", "an", "to", "for", "of", "and", "or", "with",
    "on", "in", "by", "is", "are", "be", "this", "that", "it",
    "we", "do", "does", "done",
}
_POSITIVE_ACTION_HINTS = {
    "enable", "enabled", "keep", "use", "using", "add",
    "allow", "require", "prefer", "ship", "migrate",
}
_NEGATIVE_ACTION_HINTS = {
    "disable", "disabled", "remove", "drop", "avoid",
    "forbid", "reject", "deprecate", "stop", "block",
}


def _merge_session_items(
    existing: list[SessionContextItem], incoming: list[SessionContextItem]
) -> list[SessionContextItem]:
    merged: dict[str, SessionContextItem] = {item.id: item for item in existing}
    for item in incoming:
        current = merged.get(item.id)
        if current is None:
            if item.status == "superseded":
                merged = _apply_superseding_item(merged, item)
                merged[item.id] = item
                continue
            merged = _supersede_conflicting_items(merged, item)
            merged[item.id] = item
            continue
        refs = list(dict.fromkeys([*current.source_refs, *item.source_refs]))
        merged_item = current.model_copy(
            update={
                "content": item.content or current.content,
                "status": item.status or current.status,
                "confidence": max(current.confidence, item.confidence),
                "source_refs": refs,
            }
        )
        merged = _supersede_conflicting_items(merged, merged_item, ignore_ids={item.id})
        merged[item.id] = merged_item
    return list(merged.values())


def _apply_superseding_item(
    merged: dict[str, SessionContextItem],
    item: SessionContextItem,
) -> dict[str, SessionContextItem]:
    for existing_id, existing_item in list(merged.items()):
        if not _items_share_topic(existing_item, item):
            continue
        if existing_item.status != "active":
            continue
        merged[existing_id] = existing_item.model_copy(update={"status": "superseded"})
    return merged


def _supersede_conflicting_items(
    merged: dict[str, SessionContextItem],
    item: SessionContextItem,
    *,
    ignore_ids: set[str] | None = None,
) -> dict[str, SessionContextItem]:
    if item.status != "active":
        return merged
    ignored = ignore_ids or set()
    for existing_id, existing_item in list(merged.items()):
        if existing_id in ignored:
            continue
        if existing_item.status != "active":
            continue
        if _items_conflict(existing_item, item):
            merged[existing_id] = existing_item.model_copy(update={"status": "superseded"})
    return merged


def _items_conflict(existing: SessionContextItem, incoming: SessionContextItem) -> bool:
    if existing.kind != incoming.kind:
        return False
    if not _items_share_topic(existing, incoming):
        return False
    existing_polarity = _state_item_polarity(existing.content)
    incoming_polarity = _state_item_polarity(incoming.content)
    if existing_polarity != 0 and incoming_polarity != 0 and existing_polarity != incoming_polarity:
        return True
    if incoming.kind in {"todo", "question"} and incoming.status == "superseded":
        return True
    return False


def _items_share_topic(left: SessionContextItem, right: SessionContextItem) -> bool:
    left_tokens = _state_topic_tokens(left.content)
    right_tokens = _state_topic_tokens(right.content)
    if not left_tokens or not right_tokens:
        return False
    overlap = left_tokens & right_tokens
    return len(overlap) >= 2 or (
        len(overlap) >= 1 and min(len(left_tokens), len(right_tokens)) <= 2
    )


def _state_topic_tokens(text: str) -> set[str]:
    tokens = {token for token in _STATE_TOKEN_RE.findall(text.lower()) if token not in _STATE_STOPWORDS}
    return {token for token in tokens if len(token) > 1}


def _state_item_polarity(text: str) -> int:
    tokens = _state_topic_tokens(text)
    if tokens & _POSITIVE_ACTION_HINTS and tokens & _NEGATIVE_ACTION_HINTS:
        return 0
    if tokens & _POSITIVE_ACTION_HINTS:
        return 1
    if tokens & _NEGATIVE_ACTION_HINTS:
        return -1
    return 0


__all__ = [
    # TypedDict / dataclass types
    "TurnRuntimeMetricsSnapshot",
    "AgentMessageDict",
    "SystemReminderProjection",
    "ProjectionInputMessage",
    # Session data models
    "SessionHeader",
    "SessionHistorySummary",
    "SessionEntryBase",
    "MessageEntry",
    "SessionEventType",
    "SessionEvent",
    # Session context state
    "SessionContextItem",
    "SessionContextItemKind",
    "SessionStateUpdate",
    "GoalRecord",
    "TodoItem",
    "SessionContextState",
]
