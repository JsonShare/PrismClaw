"""TrajectoryAuditProjection — pure 3-function fold over bus events.

DIRECTLY mirrors `ProjectionDefinition<K,S>` from (see
packages/session/session-projection/src/index.ts §5 L42-L73, plus
`TokenMeter`'s usage-projection apply switch at usage-projection.ts L107-140
/ L163-206).

Schema:
    S (state) = dict of
        version: int              (= STATE_VERSION = 1, bumps = state-layout change
 triggers full re-fold like stateVersion++)
        session_id: str
        records: list[RequestAuditRecord]          # committed (iteration finished)
        pending: RequestAuditRecord | None         # iteration currently being written
        turn_counter: int | None
        last_iter: int | None
        gap_count: int                             # iterations where ≥1 layer missed
        orphan_count: int                          # finish events without matching start
        last_flush_records: int                    # for dirty-tracking on_change

    K (event types) = 9 types declared in bus_events (4 lifecycle + 5 layers).

Three pure functions:
 init(session_id) → new blank state (no input state; init(null))
    apply(state, event)     → new state. Must NOT mutate input state in-place!
                              If state unchanged, it MUST return the SAME
                              reference (Object.is / Python `is` check). This
                              is the dirty-signal we use for on_changed
                              tracking — identical-ref means no downstream
 work. 镜像 Object.is() fast-path.
    view(state, *, limit, ascending) → TrajectorySnapshot (final output form,
                              same model as before). Pure: given same state +
                              same args → same snapshot bytes.

This projection is the **sole source of truth** for trajectory audit data
after the Phase 0' 解耦 refactor. The old `TrajectoryAggregator` wrapper
was removed in earlier cleanup passes (see trajectory/__init__.py).
"""
from __future__ import annotations

import copy
import logging
from typing import Any, TypeAlias, Union

from prismclaw.engine.trajectory import bus_events as evmod
from prismclaw.schema.trajectory import (
    RequestAuditRecord,
    TrajectoryProjectionState,
    init_state,
    view,
)
from prismclaw.schema.trajectory import _clone_record

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fix D: llm_provider inference from model name.
#
# bus_events.LLMResponseReceived deliberately does NOT carry llm_provider
# (provider is resolved inside LiteLLMClient via resolve_credentials, which the
# runner cannot see). The bus_events.py docstring states the design contract:
# "消费者按 model 前缀推断 provider" — consumer infers from model prefix.
#
# This helper is the consumer-side inference. It maps common model name
# prefixes to canonical provider identifiers. Returns None when no rule
# matches, so the record's llm_provider stays None (not a wrong guess).
#
# Order matters: longer/more specific prefixes first (e.g. "chatglm" before
# "glm", "moonshot" before common collisions).
# ---------------------------------------------------------------------------
_LLM_PROVIDER_PREFIX_RULES: tuple[tuple[str, str], ...] = (
    ("chatglm", "glm"),
    ("glm", "glm"),
    ("qwen", "qwen"),
    ("qwq", "qwen"),
    ("deepseek", "deepseek"),
    ("moonshot", "moonshot"),
    ("kimi", "moonshot"),
    ("yi-", "01-ai"),
    ("baichuan", "baichuan"),
    ("spark", "iflytek"),
    ("ernie", "baidu"),
    ("hunyuan", "tencent"),
    ("doubao", "bytedance"),
    ("gemini", "google"),
    ("claude", "anthropic"),
    ("gpt", "openai"),
    ("chatgpt", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("llama", "meta"),
    ("codestral", "mistral"),
    ("mixtral", "mistral"),
    ("mistral", "mistral"),
    ("phi-", "microsoft"),
    ("command", "cohere"),
)


def _infer_llm_provider(model: str | None) -> str | None:
    """Infer canonical provider id from a model name by prefix match.

    Returns None if model is None/empty or no rule matches — caller should
    leave r.llm_provider unchanged (still None) so consumers can distinguish
    "no inference possible" from "explicitly OpenAI/Glm/...".
    """
    if not model or not isinstance(model, str):
        return None
    m = model.strip().lower()
    if not m:
        return None
    # Strip common provider route prefixes (litellm-style: "openai/gpt-4",
    # "anthropic/claude-3", "vertex_ai/claude-3") so inference works on the
    # actual model segment.
    if "/" in m:
        m = m.split("/", 1)[1].strip()
        if not m:
            return None
    for prefix, provider in _LLM_PROVIDER_PREFIX_RULES:
        if m.startswith(prefix):
            return provider
    return None

LayerEvent: TypeAlias = Union[
    evmod.ProjectorInputDone,
    evmod.ProjectorOutputDone,
    evmod.AssembleEntryDone,
    evmod.PipelineOutDone,
    evmod.PackerFinalDone,
]


# ---------------------------------------------------------------------------
# init / apply  (init & view are in contracts; apply stays here)
# ---------------------------------------------------------------------------


def apply(state: TrajectoryProjectionState, event: Any) -> TrajectoryProjectionState:
    """Fold `event` into `state` → NEW state. Input state is NOT mutated.

 性能：热路径上 apply 就是 switch-case + 按类型纯函数更新。
    """
    # Event → handler dispatch.
    et = type(event)
    if et is evmod.SessionTurnStarted:
        return _apply_turn_started(state, event)
    if et is evmod.SessionIterationStarted:
        return _apply_iteration_started(state, event)
    if et is evmod.SessionIterationFinished:
        return _apply_iteration_finished(state, event)
    if et is evmod.SessionTurnFinished:
        # Turn-finished doesn't structurally change audit content (still
        # same records); but it resets turn_counter awareness and signals
        # "flush window ends". State IS different → new ref.
        return _apply_turn_finished(state, event)
    # 5 layer events:
    if et is evmod.ProjectorInputDone:
        return _apply_projector_input(state, event)
    if et is evmod.ProjectorOutputDone:
        return _apply_projector_output(state, event)
    if et is evmod.AssembleEntryDone:
        return _apply_assemble_entry(state, event)
    if et is evmod.PipelineOutDone:
        return _apply_pipeline_out(state, event)
    if et is evmod.PackerFinalDone:
        return _apply_packer_final(state, event)
    # LLM response-side events (not layer events but routed through
    # projection.apply() so pending.llm_* fields get filled).
    if et is evmod.LLMResponseReceived:
        return _apply_llm_response(state, event)
    if et is evmod.LLMRequestFailed:
        return _apply_llm_failure(state, event)
    # 未知事件 → 同引用返回（无变更信号）。`return state if unchanged`。
    return state


# ---------------------------------------------------------------------------
# apply helpers  (each returns new state; does NOT mutate argument)
# ---------------------------------------------------------------------------

def _copy_state(s: TrajectoryProjectionState) -> TrajectoryProjectionState:
    """Shallow-copy fields — records list duplicated so we can append."""
    return TrajectoryProjectionState(
        version=s.version,
        session_id=s.session_id,
        records=list(s.records),  # copy list (not records themselves)
        pending=_clone_record(s.pending) if s.pending else None,
        turn_counter=s.turn_counter,
        last_iter=s.last_iter,
        gap_count=s.gap_count,
        orphan_count=s.orphan_count,
        last_flushed_count=s.last_flushed_count,
    )


def _record_has_llm_data(r: RequestAuditRecord) -> bool:
    """True iff pending has been touched by any layer/LLM event.

    Used to decide whether a pending that's being preempted should be
    persisted as an 'orphaned' record or silently dropped. Empty pending
    (created by SessionIterationStarted but no layer/LLM event ever landed
    on it) are dropped to avoid polluting the audit trail with empty rows.

    NOTE: projector_msg_count (Layer ①) must be included — a pending that
    only received ProjectorOutputDone is NOT empty and must not be
    silently dropped when preempted.
    """
    return (
        r.llm_model is not None
        or r.total_ms is not None
        or r.ttft_ms is not None
        or r.usage_input_tokens is not None
        or r.usage_output_tokens is not None
        or bool(r.projector_msg_count)
        or bool(r.assemble_entry_msg_count)
        or bool(r.pipeline_out_msg_count)
        or bool(r.packer_final_msg_count)
    )


def _ensure_pending(
    s: TrajectoryProjectionState, session_id: str, turn: int, iteration: int,
    started_ms: int,
) -> TrajectoryProjectionState:
    """Ensure state.pending is a blank RequestAuditRecord for (turn, iter).

    If pending already exists AND matches turn+iter → return same state
    (no-op). If it does NOT match → it's an orphan (previous start never
    finished). Persist it to records with status='orphaned' ONLY if it
    has LLM/layer data; otherwise silently drop it to avoid polluting the
    audit trail with empty rows that would skew statistics.
    """
    if (
        s.pending is not None
        and s.pending.turn == turn
        and s.pending.iteration == iteration
    ):
        return s  # same ref = no change
    ns = _copy_state(s)
    if ns.pending is not None:
        # Orphan — iteration start arrived, previous pending never got a
        # finish. Persist only if it carries data; empty pending would
        # create a sparse record that confuses statistics.
        orph = ns.pending
        if _record_has_llm_data(orph):
            orph.status = "orphaned"
            orph.completed_at_ms = started_ms
            ns.records.append(orph)
            ns.orphan_count += 1
        # else: silently drop — no LLM event ever touched this pending.
        ns.pending = None
    # Create new pending for this iter.
    ns.turn_counter = turn
    ns.last_iter = iteration
    ns.pending = RequestAuditRecord(
        session_id=session_id,
        iteration=iteration,
        turn=turn,
        started_at_ms=started_ms,
        status="running",
    )
    return ns


def _apply_turn_started(
    s: TrajectoryProjectionState, ev: evmod.SessionTurnStarted,
) -> TrajectoryProjectionState:
    ns = _copy_state(s)
    if ns.session_id in (None, ""):
        ns.session_id = ev.session_id
 # turn/start sets `turn_counter` — we just ensure state tracks it.
    ns.turn_counter = ev.turn_counter
    return ns


def _apply_iteration_started(
    s: TrajectoryProjectionState, ev: evmod.SessionIterationStarted,
) -> TrajectoryProjectionState:
    # If a layer event arrived before us and created the pending with a
    # synthetic start time, correct it now with the real timestamp so the
    # Gantt timeline positions the bar at its true start.
    if (
        s.pending is not None
        and s.pending.turn == ev.turn_counter
        and s.pending.iteration == ev.iteration
        and (
            s.pending.started_at_ms == 1_700_000_000_000
            or s.pending.started_at_ms < 1_700_000_000_000
        )
    ):
        ns = _copy_state(s)
        assert ns.pending is not None
        ns.pending.started_at_ms = ev.started_at_ms
        return ns
    return _ensure_pending(
        s, ev.session_id, ev.turn_counter, ev.iteration, ev.started_at_ms,
    )


def _apply_iteration_finished(
    s: TrajectoryProjectionState, ev: evmod.SessionIterationFinished,
) -> TrajectoryProjectionState:
    if s.pending is None:
        # orphan finish without start
        ns = _copy_state(s)
        ns.orphan_count += 1
        return ns
    # Commit pending → records (clone so we don't mutate old state ref).
    ns = _copy_state(s)
    rec = ns.pending
    if rec is None:
        return ns  # defensive; None'd above
    # Gap = ANY of the 3 downstream layers missing.
    # Layers checked:
    #   Layer 0.5 (projector_input)  — optional, tree is immutable across iters
    #   Layer ①    projector output   — INFORMATIONAL ONLY (see note below)
    #   Layer ②    assemble entry     — mandatory
    #   Layer ③    pipeline out       — mandatory
    #   Layer ④    packer final       — mandatory
    #
    # Layer ① (projector) is NOT included in the gap check because its
    # msg_count reflects ONLY the tree's history branch — on a fresh turn
    # with empty history this is legitimately 0, even though the LLM
    # receives 1+ messages injected downstream via the assemble layer
    # (the user's new message, skill prompts, etc.). Treating proj=0 as
    # a gap would falsely flag every fresh-turn iteration as "error".
    # The projector layer is still SURFACED in the snapshot for diagnosis;
    # it just doesn't count toward gap status.
    #
    # Fix F note: projector_appended_msg_count (messages injected via
    # appended_messages_override) is also informational — it's 0 when
    # the user's message enters via the assemble layer rather than the
    # override path, which is the common case for fresh-turn chats.
    gap = False
    if not rec.assemble_entry_msg_count:
        gap = True
    if not rec.pipeline_out_msg_count:
        gap = True
    if not rec.packer_final_msg_count:
        gap = True
    if gap:
        ns.gap_count += 1
    rec.completed_at_ms = ev.finished_at_ms
    rec.status = "error" if gap else "ok"
    ns.records.append(rec)
    ns.pending = None
    return ns


def _apply_turn_finished(
    s: TrajectoryProjectionState, ev: evmod.SessionTurnFinished,
) -> TrajectoryProjectionState:
    # If pending still exists → commit it as status="orphaned" (turn ended
    # without an explicit SessionIterationFinished for the in-flight iter).
    #
    # NOTE: This is the *expected* teardown path for the last iteration of a
    # turn when Fix E's turn-finished callback failed to publish an explicit
    # SessionIterationFinished. It is NOT an anomaly orphan, so we do NOT
    # bump state.orphan_count — that counter tracks only anomaly orphans
    # (case 1: iter N+1 start preempts still-pending iter N via
    # _ensure_pending; case 2: SessionIterationFinished arrives with no
    # matching pending via _apply_iteration_finished). Bumping it here would
    # falsely report every turn's last-iter teardown as an anomaly.
    #
    # The record's status="orphaned" is still surface-visible via view()'s
    # recomputed `orphan_cnt = sum(r.status == "orphaned")`, so consumers
    # still see the orphan record in the snapshot — they just don't see a
    # falsely-inflated anomaly counter.
    #
    # DATA-HYGIENE: If pending has no LLM/layer data (no event ever touched
    # it), silently drop instead of appending — empty records pollute the
    # audit trail, double the statistics, and confuse the front-end list
    # with sparse rows. Only persist orphans that carry actual data.
    ns = _copy_state(s)
    if ns.pending is not None:
        orph = ns.pending
        if _record_has_llm_data(orph):
            orph.status = "orphaned"
            orph.completed_at_ms = ev.finished_at_ms
            ns.records.append(orph)
        # else: silently drop — no data ever landed on this pending.
        ns.pending = None
    ns.turn_counter = None
    return ns


def _apply_layer_event(
    s: TrajectoryProjectionState,
    ev: LayerEvent,
    updater,
) -> TrajectoryProjectionState:
    """Generic layer-event applier: ensure pending exists, then run updater(rec).

    If pending doesn't exist for this iteration, create it via _ensure_pending
    with a synthetic started_at = ev.*_at_ms or fallback 0.
    乱序到达的事件（layer 在 step/start 之前）是合法场景，这里做容错。
    """
    # Ensure pending for (turn, iter)
    if (
        s.pending is None
        or s.pending.turn != ev.turn_counter
        or s.pending.iteration != ev.iteration
    ):
        # Synthetic start time: 0 if unknown; coerces RequestAuditRecord
        # validation if real start event never arrives.
        started = getattr(ev, "started_at_ms", None) or 1_700_000_000_000
        ns = _ensure_pending(
            s, ev.session_id, ev.turn_counter, ev.iteration, started,
        )
    else:
        ns = _copy_state(s)
    # Apply updater to the record that's been freshly copied in ns
    assert ns.pending is not None, "_ensure_pending guarantees pending != None"
    updater(ns.pending, ev)
    return ns


def _apply_projector_input(
    s: TrajectoryProjectionState, ev: evmod.ProjectorInputDone,
) -> TrajectoryProjectionState:
    def up(r: RequestAuditRecord, e: evmod.ProjectorInputDone):
        r.projector_input_order_count = e.order_count
        r.projector_input_entry_types = tuple(e.order_entry_types)
        r.projector_input_head_seq = tuple(e.order_head_seq)
        r.projector_input_tail_seq = tuple(e.order_tail_seq)
        r.projector_input_tail_types = tuple(e.order_tail_types)
    return _apply_layer_event(s, ev, up)


def _apply_projector_output(
    s: TrajectoryProjectionState, ev: evmod.ProjectorOutputDone,
) -> TrajectoryProjectionState:
    def up(r: RequestAuditRecord, e: evmod.ProjectorOutputDone):
        r.projector_msg_count = e.msg_count
        r.projector_msg_roles = tuple(e.msg_roles)
        r.projector_head = tuple(e.head)
        r.projector_tail = tuple(e.tail)
        # Fix F: surface appended_msg_count so the panel can show
        # "history=0 + appended=N → total N" instead of misleading "0".
        r.projector_appended_msg_count = e.appended_msg_count
 # actual content previews
        r.projector_user_message_preview = e.user_message_preview
        r.projector_tool_results_preview = tuple(e.tool_results_preview)
    return _apply_layer_event(s, ev, up)


def _apply_assemble_entry(
    s: TrajectoryProjectionState, ev: evmod.AssembleEntryDone,
) -> TrajectoryProjectionState:
    def up(r: RequestAuditRecord, e: evmod.AssembleEntryDone):
        r.assemble_entry_msg_count = e.msg_count
        r.assemble_entry_roles = tuple(e.msg_roles)
        r.assemble_entry_head = tuple(e.head)
        r.assemble_entry_tail = tuple(e.tail)
        r.assemble_entry_meta_keys = tuple(e.entry_meta_keys)
        # hash for N11 metadata delta debug (cheap, 16 hex)
        import hashlib
        r.assemble_entry_meta_values_hash = hashlib.sha1(
            ",".join(e.entry_meta_keys).encode("utf-8"),
        ).hexdigest()[:16]
    return _apply_layer_event(s, ev, up)


def _apply_pipeline_out(
    s: TrajectoryProjectionState, ev: evmod.PipelineOutDone,
) -> TrajectoryProjectionState:
    def up(r: RequestAuditRecord, e: evmod.PipelineOutDone):
        r.pipeline_out_msg_count = e.msg_count
        r.pipeline_out_roles = tuple(e.msg_roles)
        r.pipeline_out_head = tuple(e.head)
        r.pipeline_out_tail = tuple(e.tail)
        # Deep-copy dict so future mutations of caller's budget_summary don't
        # retroactively change projection state (we freeze via pydantic frozen
        # event but dict values inside are still mutable — defensive copy).
        r.pipeline_out_budget_summary = copy.deepcopy(e.budget_summary)
        r.pipeline_out_compressors_used = tuple(e.compressors_used)
        r.pipeline_out_compaction_happened = bool(e.compressors_used) or (
            r.pipeline_out_msg_count > 0
            and (r.assemble_entry_msg_count - r.pipeline_out_msg_count) > 0
        )
 # compaction content preview
        r.pipeline_out_compaction_summary = e.compaction_summary
    return _apply_layer_event(s, ev, up)


def _apply_packer_final(
    s: TrajectoryProjectionState, ev: evmod.PackerFinalDone,
) -> TrajectoryProjectionState:
    def up(r: RequestAuditRecord, e: evmod.PackerFinalDone):
        r.packer_final_msg_count = e.msg_count
        r.packer_final_roles = tuple(e.msg_roles)
        r.packer_final_head = tuple(e.head)
        r.packer_final_tail = tuple(e.tail)
        r.packer_final_tokens = e.estimated_tokens
        if e.system_text_chars is not None:
            r.packer_final_system_prompt_len = int(e.system_text_chars)
        if e.tool_schema_chars is not None:
            r.packer_final_tool_schema_total_chars = int(e.tool_schema_chars)
        if e.response_format_chars is not None:
            r.packer_final_response_format_text = (
                f"<chars={e.response_format_chars}>"
            )
 # actual text previews
        r.packer_final_system_prompt_text = e.system_prompt_text
        r.packer_final_tool_schema_text = e.tool_schema_text
    return _apply_layer_event(s, ev, up)


# ---------------------------------------------------------------------------
# LLM response-side apply handlers
#
# Both reuse the generic _apply_layer_event framework — it handles
# _ensure_pending (so out-of-order arrival like "LLMResponseReceived before
# SessionIterationStarted" creates a synthetic pending, 镜像
# foldEvent tolerance) and clones the record so projection state remains
# immutable.
#
# Publish timing invariants (runner.py):
#   LLMResponseReceived  →  after `response = LLMResponse(...)` constructed;
#                           before `break`. SessionIterationFinished (L2871)
#                           fires later → pending is still alive → fields land
#                           on the same record that gets committed.
#   LLMRequestFailed    →  in each except branch of the LLM call site before
#                           `yield ErrorEvent`. Same pending invariant holds.
# ---------------------------------------------------------------------------

def _apply_llm_response(
    s: TrajectoryProjectionState, ev: evmod.LLMResponseReceived,
) -> TrajectoryProjectionState:
    """成功路径: 填充 pending 的 9 个响应字段 + packer_final_model (v1.0 bug fix).

    Fix D: 同时按 model 前缀推断 llm_provider (设计契约要求 — 详见
    bus_events.LLMResponseReceived docstring 与 _infer_llm_provider).
    """
    from prismclaw.engine.trajectory.metrics import (
        Stage, StageTimer, WriteMode, inc_write,
    )
    with StageTimer(Stage.LLM_RESPONSE_APPLY):
        inc_write(WriteMode.LLM_RESPONSE_RECEIVED)

        def up(r: RequestAuditRecord, e: evmod.LLMResponseReceived):
            r.llm_model = e.model
            # Fix D: provider 不在事件层 (runner 看不到 _resolve_credentials
            # 解析结果), 由消费者按 model 前缀推断. 失败时保持 None.
            _inferred = _infer_llm_provider(e.model)
            if _inferred is not None:
                r.llm_provider = _inferred
            r.finish_reason = e.finish_reason
            r.usage_input_tokens = e.usage_input_tokens
            r.usage_output_tokens = e.usage_output_tokens
            r.usage_cache_read_tokens = e.usage_cache_read_tokens
            r.usage_cache_write_tokens = e.usage_cache_write_tokens
            r.usage_reasoning_tokens = e.usage_reasoning_tokens
            r.ttft_ms = int(e.ttft_ms) if e.ttft_ms is not None else None
            r.total_ms = int(e.total_ms) if e.total_ms is not None else None
            # fix v1.0 bug: packer_final_model 从未被 PackerFinalDone 填充
            # (PackerFinalDone 事件本身没有 model 字段), 一直是空字符串.
            # 用 LLM 响应的 model 字段顺手补上, 让审计记录可看到实际使用的
            # 模型 (与 llm_model 同值, 但消费者可能从 packer_final_*
            # 字段组读取, 所以两个地方都要有).
            r.packer_final_model = e.model
 # actual response content previews
            r.assistant_text_preview = e.assistant_text_preview
            r.tool_calls_summary = tuple(e.tool_calls_summary)
        return _apply_layer_event(s, ev, up)


def _apply_llm_failure(
    s: TrajectoryProjectionState, ev: evmod.LLMRequestFailed,
) -> TrajectoryProjectionState:
    """失败路径: 填充 pending 的 finish_reason='error' + error 信息.

    Fix D: 失败路径也按 model 前缀推断 llm_provider, 与成功路径一致.
    """
    from prismclaw.engine.trajectory.metrics import (
        Stage, StageTimer, WriteMode, inc_write,
    )
    with StageTimer(Stage.LLM_FAILURE_APPLY):
        inc_write(WriteMode.LLM_REQUEST_FAILED)

        def up(r: RequestAuditRecord, e: evmod.LLMRequestFailed):
            r.llm_model = e.model
            _inferred = _infer_llm_provider(e.model)
            if _inferred is not None:
                r.llm_provider = _inferred
            r.finish_reason = "error"
            r.error = e.error_code or None      # RequestAuditRecord.error 字段
            r.error_stack = e.error_message or None
            r.packer_final_model = e.model       # v1.0 bug fix (see above)
            # usage 字段在失败时不填 (litellm 失败时无 usage 返回)
            r.ttft_ms = int(e.ttft_ms) if e.ttft_ms is not None else None
            r.total_ms = int(e.total_ms) if e.total_ms is not None else None
        return _apply_layer_event(s, ev, up)
