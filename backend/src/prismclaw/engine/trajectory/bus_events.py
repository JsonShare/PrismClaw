"""Core event types published by the agent loop onto SessionBus.

These are the **typed contracts** between core (publisher) and downstream
subscribers (audit trajectory, token meter projection, context-pressure
meter etc.).

The design mirrors 会话事件映射 (63 event types) but the PrismClaw
Phase 0' (解耦) set is intentionally minimal — it covers the 5 trajectory
snapshot layers plus Turn/Iteration lifecycle gates (turn/start vs
step/start equivalents). Subscribers other than trajectory will likely
need NEW event types later; extend HERE (not in subscriber-owned modules),
then re-subscribe from the subscriber side. Core must NOT hard-code
assumptions about which subscribers need which events.

Every event type is a frozen pydantic BaseModel for two reasons:
1. Payload immutability — a subscriber cannot mutate the data another
 subscriber is about to read (the same reason does `deepFreeze` on
   every appended event).
2. Validated fields — publishers pass Python primitives; pydantic coerces
   `tuple[int, ...]` / `tuple[str, ...]` from any iterable. This avoids
   silent shape-mismatch bugs between core publisher and subscriber.

CRITICAL RULE FOR SUBSCRIBERS:
    Treat every field as a **shallow snapshot copy**. Do not mutate
    references to list/dict values you receive here. When in doubt, copy
    them before passing to downstream state that re-uses them (projection
    state, serializer, etc.).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from prismclaw.engine.execution.control.kernel import TerminalReason

# ----------------------------------------------------------------------------
# Turn / Iteration lifecycle events
#
# 对应： turn/start → SessionTurnStarted
#                  step/start  →  SessionIterationStarted
#                  turn/end    →  SessionTurnFinished
# emits step/start (iteration) inside a single turn — we mirror this
# because a user turn may trigger multiple LLM iterations (tool-call loop).
# ----------------------------------------------------------------------------


class SessionTurnStarted(BaseModel):
    """Published once per RunRequest = once per user turn.

    `turn_counter` is the same int used as the Redis ZSET score factor
    (`turn_counter * 1_000_000`). Must be monotonically increasing for the
    session; core resolves it from tree.turn_counter or session-owned
    monotonic counter. Must NOT collide between turns of same session.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    session_id: str
    turn_counter: int
    request_id: str | None = None
    started_at_ms: int


class SessionIterationStarted(BaseModel):
    """Published at the start of every LLM iteration (every while-loop pass).

    This is the strict monotonic "start_new_record()" gate — trajectory
    projection uses it to push a new blank RequestAuditRecord onto its
    `state.pending` slot.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    session_id: str
    turn_counter: int
    iteration: int  # monotonically increasing within the turn
    started_at_ms: int


class SessionIterationFinished(BaseModel):
    """Published after a complete iteration has produced an LLM response.

    `packer_final` Layer ④ snapshot is guaranteed to have been published
    BEFORE this event. Projection uses this to move `state.pending` to
    `state.records` iff all expected layers populated, and reports gap count
    otherwise (mirroring `TrajectoryAggregator.complete_iteration()`).
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    session_id: str
    turn_counter: int
    iteration: int
    finished_at_ms: int


class SessionTurnFinished(BaseModel):
    """Published at the end of the user turn.

    Projection uses this as the signal to flush state → Redis (equivalent
    to the old flush_to_redis on turn exit).

    `terminated_by` carries the final turn-end reason from agent_loop's
    state.ctx.terminated_by (closed vocabulary: ``TerminalReason``).
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    session_id: str
    turn_counter: int
    finished_at_ms: int
    terminated_by: TerminalReason | None = None


# ----------------------------------------------------------------------------
# 5 trajectory layer events (in capture order = agent-loop causal order)
#
#   Layer 0.5  ProjectorInputDone          tree.order raw input
#   Layer ①    ProjectorOutputDone         MessageProjector.build_runtime_messages()
#   Layer ②    AssembleEntryDone           assemble_coordinator.assemble()
#   Layer ③    PipelineOutDone             PreRequestPipeline.run() projection
#   Layer ④    PackerFinalDone             request_packager.pack_projection()
#
# All layer events share session_id/turn_counter/iteration so the
# projection can associate them to the correct `pending` record slot.
# ----------------------------------------------------------------------------


class ProjectorInputDone(BaseModel):
    """Layer 0.5: raw SessionTree.order entries AS-IS before projector runs.

    Core publisher responsibility: take `list(tree.order or [])` and
    snapshot each entry's `entry.type`, `entry.id`, `entry.seq`,
    `entry.parent_id` as frozen tuples — don't pass raw Entry references
 that could be mutated by subsequent steps (deepFreeze equivalent
    at publish time via pydantic frozen + coerced tuples).
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    session_id: str
    turn_counter: int
    iteration: int

    order_count: int
    order_entry_types: tuple[str, ...] = ()
    order_head_seq: tuple[int, ...] = ()
    order_tail_seq: tuple[int, ...] = ()
    order_tail_types: tuple[str, ...] = ()


class ProjectorOutputDone(BaseModel):
    """Layer ①: MessageProjector output = messages list passed downstream.

    Values exactly match old `set_projector_snapshot`'s 4 fields:
      msg_count   = source_messages count
      msg_roles   = tuple of "user" / "assistant" / "system"
      head / tail = up-to-3 human-readable head / tail strings (snippets).

    Fix F: `appended_msg_count` is the number of messages that will be
    injected downstream via `appended_messages_override` (the new user
    message + skill prompts). The projector output (msg_count) is the
    tree's history branch only — it can be 0 on a fresh turn while the
    LLM still receives 1+ appended messages. Without this field the
    panel shows "Layer ① = 0 messages" while Layers ②/③/④ show 1+,
    which looks broken but is actually correct pipeline behavior.

 `user_message_preview` carries the latest user message
    content (up to 500 chars) so the trajectory panel can show WHAT the
    user asked, not just "1 user message". `tool_results_preview` carries
    the previous iteration's tool-result content (role='tool' messages
    from source_messages), each up to 200 chars — answers "what did the
    tools return that fed into THIS iteration".
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    session_id: str
    turn_counter: int
    iteration: int

    msg_count: int = 0
    msg_roles: tuple[str, ...] = ()
    head: tuple[str, ...] = ()
    tail: tuple[str, ...] = ()
    appended_msg_count: int = 0
 # actual user/tool content previews (not just 80-char snippets)
    user_message_preview: str | None = None
    tool_results_preview: tuple[str, ...] = ()


class AssembleEntryDone(BaseModel):
    """Layer ②: `assembled.messages` AFTER assemble_coordinator.assemble()."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    session_id: str
    turn_counter: int
    iteration: int

    msg_count: int = 0
    msg_roles: tuple[str, ...] = ()
    head: tuple[str, ...] = ()
    tail: tuple[str, ...] = ()
    entry_meta_keys: tuple[str, ...] = ()


class PipelineOutDone(BaseModel):
    """Layer ③: dynamic_result.projection.projected_messages AFTER pipeline."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    session_id: str
    turn_counter: int
    iteration: int

    msg_count: int = 0
    msg_roles: tuple[str, ...] = ()
    head: tuple[str, ...] = ()
    tail: tuple[str, ...] = ()

    # Per-layer metadata — previously `set_pipeline_out_snapshot` kwargs
    budget_summary: dict[str, int] = Field(default_factory=dict)
    compressors_used: tuple[str, ...] = ()
 # compaction input/output preview (up to 500 chars) so the
    # panel can show WHAT was compressed, not just "compact_history:trim"
    # labels. None when no compaction happened this iteration.
    compaction_summary: str | None = None


class PackerFinalDone(BaseModel):
    """Layer ④: final messages that ACTUALLY go to the LLM API call."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    session_id: str
    turn_counter: int
    iteration: int

    msg_count: int = 0
    msg_roles: tuple[str, ...] = ()
    head: tuple[str, ...] = ()
    tail: tuple[str, ...] = ()

    estimated_tokens: int | None = None
    system_text_chars: int | None = None
    tool_schema_chars: int | None = None
    response_format_chars: int | None = None
 # actual system prompt + tool schema TEXT (up to 1000 chars).
    # The existing *_chars fields only carry counts — useless for "what did
    # the system prompt actually say". These give the panel real content.
    system_prompt_text: str | None = None
    tool_schema_text: str | None = None
    # Full untruncated system prompt + its sha1-16. Consumed by the
    # trajectory service to publish a kind='system' cell when (and only
    # when) the prompt content changes across turns — the per-turn injected
    # authoritative time makes the sha change every turn, which is audit
    # value ("what did the model actually see this turn"), not noise.
    system_prompt_full_text: str | None = None
    system_prompt_sha1_16: str | None = None


# ----------------------------------------------------------------------------
# LLM response-side events
#
# These are NOT "layer" events (they describe what the LLM produced, not what
# was assembled upstream). They are added to ALL_EVENTS so the blanket
# subscriber in TrajectoryAuditService.handle_event routes them through
# projection.apply(), but they are NOT in ALL_LAYER_EVENTS.
#
# Publish timing (runner.py):
#   LLMResponseReceived  →  right after `response = LLMResponse(...)` is
#                           constructed (success path); before `break`.
#   LLMRequestFailed    →  in each except branch of the LLM call site
#                           (AgentTimeoutError / AgentAbortedError /
#                           LLMError final failure); retry paths do NOT
#                           publish — only the final outcome per (turn, iter)
#                           is recorded.
# ----------------------------------------------------------------------------


class LLMResponseReceived(BaseModel):
    """LLM 成功响应事件 — response 构造后 publish.

    不传 llm_provider — provider 解析在 LiteLLMClient 内部,
    runner 不可见. 消费者按 model 前缀推断 provider.

 `assistant_text_preview` carries the LLM response text
    (up to 500 chars) so the panel can show WHAT the assistant answered,
    not just "model=X, output_tokens=Y". `tool_calls_summary` carries
    one string per emitted tool call in `name(args[:200])` format —
    answers "which tools were called and with what arguments".
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    session_id: str
    turn_counter: int
    iteration: int

    model: str = ""
    finish_reason: str | None = None
    usage_input_tokens: int | None = None
    usage_output_tokens: int | None = None
    usage_cache_read_tokens: int | None = None
    usage_cache_write_tokens: int | None = None
    usage_reasoning_tokens: int | None = None
    ttft_ms: float | None = None        # real first-token time minus _llm_start
    total_ms: float | None = None       # _llm_end - _llm_start
    completed_at_ms: int
 # actual content previews (not just token counts)
    assistant_text_preview: str | None = None
    tool_calls_summary: tuple[str, ...] = ()


class LLMRequestFailed(BaseModel):
    """LLM 失败事件 — LLM 调用异常时 publish.

    重构清理: 删除 partial_chunks / partial_text_chars / attempt 字段.
      * partial_*: 在 llm.py 内部 nonlocal 维护, runner 作用域不可见;
        部分正文的持久化由消费方（Web 收尾路径）负责，事件层不携带.
      * attempt: 无消费者. 事件层携带但 RequestAuditRecord model 无该字段,
        apply 不写, read_snapshot_v2 返回的 model_dump 不含, API 读不到.
        重试审计若将来需要, 应走 metrics counter, 不在事件层提前设计.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    session_id: str
    turn_counter: int
    iteration: int

    model: str = ""
    error_code: str = ""               # "timeout" / "aborted" / LLMError.code
    error_message: str = ""            # truncated to 256 chars (see validator below)
    ttft_ms: float | None = None       # if a first token arrived before failure; None otherwise
    total_ms: float | None = None      # _llm_start to failure moment
    failed_at_ms: int

    @field_validator("error_message")
    @classmethod
    def _truncate_error_message(cls, v: str) -> str:
        if not isinstance(v, str):
            return ""
        return v[:256]


# ----------------------------------------------------------------------------
# Conversation-tree node events ( — per-conversation-event cells)
#
# derives trajectory cells from conversation-tree nodes (one cell per
# node).  PrismClaw's SessionTree.append() is the canonical "a node was
# added" boundary, so we publish ConversationNodeAdded here for the
# cell-projector to consume. This is the 对齐 data source for the
# trajectory timeline — NOT per-LLM-iteration records (those stay in
# RequestAuditRecord as a separate "LLM call audit" panel).
# ----------------------------------------------------------------------------


class ConversationNodeAdded(BaseModel):
    """Published by bootstrap._append() every time a node is added to
 SessionTree. One event = one trajectory cell.

 mapping (trajectory-record.ts → layout.ts):
      role='user'        → kind='user'      (lane 0 / Input)
      role='assistant'   → kind='message'   (lane 1 / Model)
                           + each tool_call in entry.tool_calls → kind='tool'
                             (lane 2 / Tools) — emitted as separate
                             ConversationNodeAdded events by the runner
                             when tool results come back
      role='tool'        → kind='tool'      (lane 2 / Tools)
      role='system'      → kind='system'    (lane 0 / Input)
      compaction marker  → kind='compacted' (lane 1 / Model) — emitted
                           separately by the compaction pipeline

    `iteration` is set ONLY for assistant cells (the LLM iteration that
    produced this response).  user / tool / system cells have iteration=None.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")
    session_id: str
    entry_id: str                       # SessionTree.entries key
    seq: int                            # position in SessionTree.order
    turn: int                           # 1-based user-message counter
    role: str                           # 'user' | 'assistant' | 'tool' | 'system'
    kind: str                           # kind: 'user'|'message'|'tool'|'system'|'compacted'|'context'|'subtool'
    content_preview: str | None = None  # content text up to 500 chars
    tool_calls_summary: tuple[str, ...] = ()  # name(args[:200]) per call (assistant only)
    tool_call_id: str | None = None     # for role='tool' cells
    # full-content + flag fields carried at publish time so the
    # cell projector can persist them in the same INSERT as the cell.
    input_detail: str | None = None     # full input content (user msg / tool args)
    output_detail: str | None = None    # full output content (assistant response / tool result)
    thinking_detail: str | None = None   # reasoning text (assistant thinking / compaction)
    is_error: bool = False
    schema_detail: str | None = None    # tool schema JSON (tool only)
    started_at_ms: int
    completed_at_ms: int | None = None
    iteration: int | None = None        # assistant/tool cell → LLM iteration number
    error: str | None = None
    # Response-side metrics for assistant cells. The LLM response HAS
    # returned by the time the assistant MessageEntry is appended (the
    # response object is required to build the entry), so the runner
    # stashes a LLMMetricsSnapshot into the trajectory runtime ctx and
    # bootstrap reads it here — one event carries the complete cell,
    # the projector does a single INSERT (no secondary UPDATE path).
    model: str | None = None
    finish_reason: str | None = None
    usage_input_tokens: int | None = None
    usage_output_tokens: int | None = None
    usage_cache_read_tokens: int | None = None
    usage_cache_write_tokens: int | None = None
    usage_reasoning_tokens: int | None = None
    ttft_ms: float | None = None
    decoding_ms: float | None = None
    tool_name: str | None = None        # tool cell → tool name (schema lookup)


# Aliases that match the names uses (helps cross-reference when
# porting future logic). Not used directly by trajectory projection.
DSH_EVENT_ALIASES: dict[str, type[BaseModel]] = {
    "turn/start": SessionTurnStarted,
    "turn/end": SessionTurnFinished,
    "step/start": SessionIterationStarted,
    "step/end": SessionIterationFinished,
    "llm/response": LLMResponseReceived,
    "llm/error": LLMRequestFailed,
}

# Every event we know about. Used by subscribers that want to blanket-subscribe
# to "any layer event" (e.g. debug logger subscriber) without typing each name.
ALL_LAYER_EVENTS: tuple[type[BaseModel], ...] = (
    ProjectorInputDone,
    ProjectorOutputDone,
    AssembleEntryDone,
    PipelineOutDone,
    PackerFinalDone,
)
ALL_EVENTS: tuple[type[BaseModel], ...] = (
    SessionTurnStarted,
    SessionIterationStarted,
    SessionIterationFinished,
    SessionTurnFinished,
    *ALL_LAYER_EVENTS,
    # Response-side events are NOT layer events (they describe what
    # the LLM produced, not what was assembled upstream), but they ARE
    # routable through projection.apply() so the blanket subscriber in
    # TrajectoryAuditService.handle_event needs them registered here.
    LLMResponseReceived,
    LLMRequestFailed,
    # conversation-tree node events (per-cell trajectory source).
    # Consumed by the cell-projector, NOT the iteration-projector.
    ConversationNodeAdded,
)


def event_typename(ev: Any) -> str:
    """Human-readable type name — identical to event.type convention.

    Example: `ProjectorOutputDone(session_id=..., ...)` →
    `"ProjectorOutputDone"`.
    """
    return type(ev).__name__
