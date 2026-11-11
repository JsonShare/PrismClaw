from __future__ import annotations

import asyncio
import logging

from dataclasses import dataclass
from typing import Any, Callable, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from prismclaw.schema.session import SessionTree
    # 类型注解所需，runtime 通过 deps.ports 访问。
    from prismclaw.engine.execution.query.state import QueryLoopState
    from prismclaw.engine.execution.checkpoint import CompressionRuntimeState
    from prismclaw.engine.task_profile.service import TaskCompactionPolicy

from prismclaw.engine.compression.budget_orchestrator import HistoryCompactionAssessment
from prismclaw.engine.compression.compaction import (
    estimate_messages_tokens,
)
from prismclaw.engine.compression.state_store import take_checkpoint
from prismclaw.engine.compression.hardening import carry_forward_missing_sections
from prismclaw.engine.foundation.metrics import emit
from prismclaw.schema.agent.context import ContextCompactor
from prismclaw.engine.state.state_protocol import normalize_state_payload
from prismclaw.schema.session.event_logger import SessionEventLogger
from prismclaw.schema.agent.session import SessionContextState, SessionStateUpdate
from prismclaw.schema.content.compaction import CompactionBoundary, CompactionPreservedSegment, CompactResult
from prismclaw.schema.session.projection import SessionProjectionEngine
from prismclaw.schema.session.types import CompactionEntry, CustomEntry, SESSION_MEMORY_CUSTOM_TYPE
from prismclaw.schema.session.types import generate_entry_id, now_iso
from prismclaw.schema.content.compaction import parse_compaction_artifact

# ── Re-export from sub-modules (external import compatibility) ──
from prismclaw.engine.compression._autocompact_breaker import (  # noqa: F401
    AutocompactBreaker,
    _resolve_breaker,
    _persist_breaker_state,
    _persist_summary_text,
    _emit_breaker_metric,
    _METRIC_AUTOCOMPACT_TRIGGER,
    _METRIC_AUTOCOMPACT_SUCCESS,
    _METRIC_AUTOCOMPACT_FAILURE,
    _METRIC_AUTOCOMPACT_GAUGE,
    _METRIC_AUTOCOMPACT_OPEN,
    _MAX_PTL_RETRIES,
    _PROMPT_TOO_LONG_REASON,
)
from prismclaw.engine.compression._compact_prompt import (  # noqa: F401
    NO_TOOLS_PREAMBLE,
    inject_no_tools_preamble,
    _resolve_no_tools_preamble_enabled,
    _ANALYSIS_BLOCK_RE,
    strip_analysis_block,
    _resolve_prompt_template,
    resolve_compact_prompt,
    resolve_strip_analysis,
    _METRIC_COMPACT_PREAMBLE_INJECTED,
    _METRIC_COMPACT_TOOL_CALL_REJECTED,
    _METRIC_COMPACT_ANALYSIS_STRIPPED,
    _METRIC_COMPACT_PROMPT_TOKENS,
    _METRIC_COMPACT_OUTPUT_TOKENS,
)
from prismclaw.engine.compression._soft_budget import (  # noqa: F401
    SoftBudgetExhausted,
    SoftBudgetVerdict,
    evaluate_soft_budget,
    render_budget_warning,
    maybe_raise_soft_budget_exhausted,
    inject_budget_warning,
    _METRIC_SOFT_BUDGET_WARN,
    _METRIC_SOFT_BUDGET_TERMINATE,
    _METRIC_SOFT_BUDGET_RATIO,
)
from prismclaw.engine.compression._ptl_retry import (  # noqa: F401
    group_messages_by_api_round,
    truncate_head_for_ptl_retry,
    _parse_ptl_token_gap,
    _compact_with_ptl_retry,
)

logger = logging.getLogger(__name__)

_event_logger = SessionEventLogger()


@dataclass
class CompactionOutcome:
    ok: bool
    compacted: bool
    error_code: str | None = None
    error_message: str | None = None
    result: Any = None


class _CompactionDeps(Protocol):
    context_compactor: ContextCompactor
    session_store: Any
    config: Any
    # Optional per-session autocompact breaker.  When absent, the
    # coordinator creates a fresh in-memory breaker that lives for
    # the duration of the run.
    autocompact_breaker: "AutocompactBreaker | None"
    # Callback to publish a compaction trajectory node (replaces
    # lazy import of engine.agent.run.bootstrap.publish_compaction_node).
    on_compaction_node: Callable[..., None] | None
    # 依赖反转 ports——compression 通过此协议访问
    # execution/hooks/security 能力，避免反向 import。
    ports: Any


async def try_compaction(
    *,
    deps: _CompactionDeps,
    tree: Any,
    request: Any,
    query_loop_state: QueryLoopState,
    compression_runtime: CompressionRuntimeState,
    history_budget: int,
    abort_event: asyncio.Event,
    force: bool,
    threshold_override: float | None = None,
    keep_recent_tokens_override: int | None = None,
) -> CompactionOutcome:
    # Surface a circuit-breaker open *before* we touch the
    # checkpoint / dispatch hooks.  This keeps the failure mode
    # identical to the design: "after N consecutive failures, raise
    # before doing more work".  The breaker state is shared across
    # turns so a session that has been thrashing on
    # ``prompt_too_long`` gets a clean error fast on subsequent
    # turns too.
    #
    # Kill switch (``enable_autocompact_breaker``, default on).
    # When the operator sets it to False we skip the breaker check
    # entirely (legacy behavior: every ``try_compaction`` call
    # attempts compaction regardless of prior failures).
    breaker = _resolve_breaker(deps, compression_runtime)
    # deps.config.context.compaction.safety 由 pydantic schema 保证字段
    # 齐全（默认值见 CompactionSafetyConfig），无需防御式 getattr 深链。
    safety_cfg = deps.config.context.compaction.safety
    breaker_enabled = bool(safety_cfg.enable_autocompact_breaker)
    threshold = int(safety_cfg.max_consecutive_failures)
    if breaker_enabled and breaker.is_open(threshold):
        _emit_breaker_metric(_METRIC_AUTOCOMPACT_OPEN, breaker=breaker)
        deps.ports.raise_circuit_breaker(
            message=(
                f"自动压缩连续失败 {threshold} 次，需要人工介入"
                "（调高 max_consecutive_failures、检查工具结果的增长情况，或重启会话）"
            )
        )

    base_messages = _query_messages_for_compaction(query_loop_state, tree=tree)

    compaction_ctx = deps.ports.build_compaction_context(
        session_id=request.session_id,
        workspace_id=request.workspace_id,
        agent_id=request.agent_id,
        message_count=len(base_messages),
        tokens_before=estimate_messages_tokens(base_messages),
    )

    checkpoint = take_checkpoint(tree)
    await deps.ports.notify_before_compaction(compaction_ctx)
    pre_compact_result = await deps.ports.invoke_compaction_hooks(
        event="PRE_COMPACT",
        ctx=compaction_ctx,
    )
    if pre_compact_result is not None and pre_compact_result.outcome == "BLOCKING":
        logger.warning(
            "PRE_COMPACT hook blocked compaction: %s",
            pre_compact_result.system_message,
        )
        return CompactionOutcome(
            ok=False,
            compacted=False,
            error_code="hook_blocked",
            error_message=pre_compact_result.system_message or "PRE_COMPACT hook blocked",
        )

    _emit_breaker_metric(_METRIC_AUTOCOMPACT_TRIGGER, breaker=breaker)
    compact_result = await _compact_with_ptl_retry(
        deps=deps,
        request=request,
        base_messages=base_messages,
        history_budget=history_budget,
        force=force,
        abort_event=abort_event,
        model=deps.config.context.compaction.model,
        threshold_override=threshold_override,
        keep_recent_tokens_override=keep_recent_tokens_override,
        breaker=breaker,
        checkpoint=checkpoint,
        compaction_ctx=compaction_ctx,
    )

    compaction_ctx.tokens_before = compact_result.tokens_before
    await deps.ports.notify_after_compaction(compaction_ctx, compact_result)
    post_compact_result = await deps.ports.invoke_compaction_hooks(
        event="POST_COMPACT",
        ctx=compaction_ctx,
        payload={
            "context": compaction_ctx,
            "result": compact_result,
        },
    )
    if post_compact_result is not None and post_compact_result.outcome != "SUCCESS":
        logger.warning(
            "POST_COMPACT hook returned outcome=%s system_message=%s",
            post_compact_result.outcome,
            post_compact_result.system_message,
        )

    if not compact_result.ok:
        checkpoint.restore_into(tree)
        breaker.increment()
        _persist_breaker_state(compression_runtime, breaker)
        _emit_breaker_metric(
            _METRIC_AUTOCOMPACT_FAILURE,
            error_type=str(compact_result.reason_code or "compaction_failed"),
            breaker=breaker,
        )
        _emit_breaker_metric(_METRIC_AUTOCOMPACT_GAUGE, breaker=breaker)
        return CompactionOutcome(
            ok=False,
            compacted=False,
            error_code=compact_result.reason_code or "compaction_failed",
            error_message=compact_result.reason or "compaction failed",
            result=compact_result,
        )

    # Active soft-degrade check after compaction has had a chance to
    # shrink the prompt.  If the post-compaction token usage still
    # crosses the terminate ratio, raise SoftBudgetExhausted so the
    # runner stops dispatching new turns.  Overflow-rescue callers
    # pass force=True and skip this guard because they are already
    # dealing with an overflow.
    if not force:
        soft_budget_cfg = safety_cfg.soft_budget
        soft_budget_on = bool(safety_cfg.enable_soft_budget)
        if soft_budget_on and soft_budget_cfg is not None:
            try:
                token_usage_after = compact_result.tokens_after
                if token_usage_after is None:
                    post_messages = (compact_result.details or {}).get("messages", [])
                    token_usage_after = estimate_messages_tokens(post_messages)
                effective_window = int(
                    deps.ports.get_effective_context_window(
                        getattr(deps.config, "effective_model", None) or "",
                        max_output_tokens_for_summary=deps.config.context.compaction.history.summary_output_token_cap,
                        fallback=deps.config.context.budget.window_tokens,
                    )
                )
                verdict = evaluate_soft_budget(
                    token_usage=token_usage_after,
                    effective_window=effective_window,
                    soft_budget=soft_budget_cfg,
                )
                maybe_raise_soft_budget_exhausted(verdict, enabled=soft_budget_on)
            except SoftBudgetExhausted:
                raise
            except Exception as exc:
                logger.debug("soft_budget.post_check_skipped err=%s", exc)

    if compact_result.compacted and compact_result.validation is not None and not compact_result.validation.ok:
        # The reference implementation's compaction has no hard semantic fidelity
        # gate. Downgrade to a non-blocking warning: the compaction is still
        # accepted and committed, but the loss is recorded for audit instead
        # of rolling back the work (which would drop a successful compaction
        # purely because a non-essential invariant was not restated). This
        # keeps the loop fully automatic — no human gate, no breaker trip.
        logger.warning(
            "semantic fidelity warning (non-blocking): %s",
            ", ".join(compact_result.validation.missing_invariants[:4]) or "validation failed",
        )
        _emit_breaker_metric(_METRIC_AUTOCOMPACT_GAUGE, breaker=breaker)

    if compact_result.compacted and compact_result.summary:
        # ── C-1 deterministic carry-forward ──
        # If a prior compaction exists, ensure sections critical for
        # chain integrity (Pending Tasks / voided entries) are not
        # silently dropped by the LLM.  This is a structural guarantee
        # that does NOT depend on LLM behaviour.
        prior_summary = _find_prior_compaction_summary(tree)
        if prior_summary:
            anchored = carry_forward_missing_sections(
                compact_result.summary, prior_summary,
            )
            if anchored != compact_result.summary:
                logger.info(
                    "carry-forward anchor applied: summary grew %d→%d chars",
                    len(compact_result.summary),
                    len(anchored),
                )
                compact_result = _patch_summary(compact_result, anchored)

        comp_entry_id = generate_entry_id(set(tree.entries.keys()))
        comp_entry = CompactionEntry(
            id=comp_entry_id,
            parent_id=tree.leaf_id,
            summary=compact_result.summary,
            first_kept_entry_id=compact_result.first_kept_entry_id or tree.leaf_id or "",
            tokens_before=compact_result.tokens_before,
            details=_compaction_entry_details(
                compact_result,
                compaction_entry_id=comp_entry_id,
                session_id=request.session_id,
                query_loop_state=query_loop_state,
            ),
        )
        await _commit_tree_entry(deps=deps, tree=tree, entry=comp_entry)
        # DSH parity: CompactionEntry bypasses bootstrap._append — publish
        # the compacted trajectory cell here (best-effort, never raises).
        try:
            on_compaction_node = getattr(deps, "resolved_on_compaction_node", None) or getattr(deps, "on_compaction_node", None)
            if callable(on_compaction_node):
                on_compaction_node(
                    tree, entry_id=comp_entry_id, summary=compact_result.summary,
                )
        except Exception:
            pass
        await _persist_session_memory_snapshot_for_compaction(
            deps=deps,
            tree=tree,
            session_id=request.session_id,
            compact_result=compact_result,
        )
        # Success resets the consecutive-failure counter so a
        # single successful compaction unblocks the session.  We
        # also flush the reset to the persisted runtime state.
        breaker.reset()
        _persist_breaker_state(compression_runtime, breaker)
        # Persist the summary text so the next compaction can use
        # the session-memory bypass instead of calling the LLM again.
        if compact_result.summary:
            _persist_summary_text(compression_runtime, compact_result.summary)
        _emit_breaker_metric(_METRIC_AUTOCOMPACT_SUCCESS, breaker=breaker)
        _emit_breaker_metric(_METRIC_AUTOCOMPACT_GAUGE, breaker=breaker)
        return CompactionOutcome(ok=True, compacted=True, result=compact_result)

    return CompactionOutcome(ok=True, compacted=False, result=compact_result)


def _compaction_entry_details(
    compact_result: "CompactResult",
    *,
    compaction_entry_id: str,
    session_id: str,
    query_loop_state: QueryLoopState,
) -> dict[str, Any] | None:
    artifact = parse_compaction_artifact(compact_result.details)
    if artifact is None:
        return None
    artifact = _finalize_history_artifact_boundary(
        artifact,
        compaction_entry_id=compaction_entry_id,
        session_id=session_id,
        first_kept_entry_id=compact_result.first_kept_entry_id,
        query_loop_state=query_loop_state,
    )
    merged = artifact.to_metadata_dict()
    merged.setdefault("tokens_before", compact_result.tokens_before)
    merged.setdefault("tokens_after", compact_result.tokens_after)
    merged.setdefault("reason_code", compact_result.reason_code)
    merged.setdefault("summary_text", compact_result.summary)
    return merged


def _finalize_history_artifact_boundary(
    artifact: Any,
    *,
    compaction_entry_id: str,
    session_id: str,
    first_kept_entry_id: str | None,
    query_loop_state: QueryLoopState,
):
    if getattr(artifact, "artifact_type", None) != "history_compaction_summary":
        return artifact
    boundary = artifact.boundary or CompactionBoundary()
    source_messages = query_loop_state.query_view.source_messages.messages
    source_entry_ids = [
        entry_id
        for entry_id in (_message_entry_id(message) for message in source_messages)
        if entry_id is not None
    ]
    preserved_segment = boundary.preserved_segment
    if first_kept_entry_id and source_entry_ids:
        preserved_segment = CompactionPreservedSegment(
            head_entry_id=first_kept_entry_id,
            anchor_entry_id=compaction_entry_id,
            tail_entry_id=source_entry_ids[-1],
        )
    next_boundary = boundary.model_copy(
        update={
            "boundary_entry_id": compaction_entry_id,
            "session_id": boundary.session_id or session_id,
            "source_head_entry_id": boundary.source_head_entry_id
            or (source_entry_ids[0] if source_entry_ids else None),
            "source_tail_entry_id": boundary.source_tail_entry_id
            or (source_entry_ids[-1] if source_entry_ids else None),
            "first_kept_entry_id": boundary.first_kept_entry_id or first_kept_entry_id,
            "preserved_segment": preserved_segment,
        }
    )
    return artifact.model_copy(update={"boundary": next_boundary})


def _message_entry_id(message: Any) -> str | None:
    if not isinstance(message, dict):
        return None
    entry_id = message.get("__entry_id__")
    return entry_id if isinstance(entry_id, str) and entry_id else None


async def _persist_session_memory_snapshot_for_compaction(
    *,
    deps: _CompactionDeps,
    tree: Any,
    session_id: str,
    compact_result: CompactResult,
) -> None:
    artifact = parse_compaction_artifact(compact_result.details)
    if artifact is None or artifact.artifact_type != "history_compaction_summary":
        return
    payload = normalize_state_payload(getattr(artifact, "state_updates", None))
    if not any(payload.values()):
        return
    current = SessionProjectionEngine(tree).get_latest_session_memory_state()
    next_state = (
        current.model_copy(deep=True)
        if current is not None
        else SessionContextState(session_id=session_id)
    )
    next_state.apply_update(SessionStateUpdate.model_validate(payload))
    snapshot = next_state.model_dump(mode="json")
    if current is not None and current.model_dump(mode="json") == snapshot:
        return
    entry = CustomEntry(
        id=generate_entry_id(set(tree.entries.keys())),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        custom_type=SESSION_MEMORY_CUSTOM_TYPE,
        data=snapshot,
    )
    await _commit_tree_entry(deps=deps, tree=tree, entry=entry)


async def _commit_tree_entry(*, deps: _CompactionDeps, tree: Any, entry: Any) -> None:
    """Commit a non-message entry (CompactionEntry / session-memory snapshot)
    via entry_updated AND sync the in-memory tree.

    Without the in-memory sync, tree.leaf_id stays at the pre-commit value:
    the next _append() parents onto the stale leaf, the committed entry is
    orphaned on the parent chain, and a fresh load() drops it from
    get_branch() — the compaction summary would never reach the LLM again
    (long-conversation amnesia). Order is intentionally untouched: the Redis
    ENTRY_UPDATED projection does not append to order either, so the
    in-memory tree stays equivalent to a reloaded one.
    """
    event = _event_logger.entry_updated(tree.header.id, entry, leaf_id=entry.id)
    await deps.session_store.commit_events([event])
    if entry.id not in tree.entries:
        tree.entries[entry.id] = entry
    tree.leaf_id = entry.id


async def maybe_pretrim_history(
    *,
    deps: _CompactionDeps,
    tree: Any,
    request: Any,
    query_loop_state: QueryLoopState,
    compression_runtime: CompressionRuntimeState,
    history_budget: int,
    history_tokens: int,
    abort_event: asyncio.Event,
    compaction_policy: TaskCompactionPolicy | None = None,
    history_assessment: HistoryCompactionAssessment | None = None,
) -> CompactionOutcome | None:
    if history_budget <= 0:
        return None
    if history_assessment is not None:
        if not history_assessment.should_compact:
            return None
        threshold_override = history_assessment.threshold_override
        keep_recent_tokens_override = history_assessment.keep_recent_tokens_override
    else:
        threshold_override = None
        keep_recent_tokens_override = None
        trigger_budget = history_budget
        if compaction_policy is not None:
            threshold_override = compaction_policy.threshold_override
            keep_recent_tokens_override = compaction_policy.keep_recent_tokens
            trigger_budget = compaction_policy.pretrim_trigger_tokens or history_budget
        if history_tokens <= trigger_budget:
            return None
    if history_tokens <= 0:
        return None
    # SDD R-A2 A-3.5 cooldown: a compaction that succeeds but barely reduces
    # (huge summary / keep_recent-heavy tail) would re-trigger on the very
    # next turn — one extra summarizer LLM call per turn. Skip while within
    # min_interval_entries of the last compaction; the over-budget state is
    # NOT hidden (WARNING below), and the tool-loop soft-budget guards still
    # protect the window.
    min_interval_entries = _resolve_min_interval_entries(deps)
    if min_interval_entries > 0 and compression_runtime.last_compaction_entry_seq >= 0:
        entry_seq = len(getattr(tree, "entries", {}) or {})
        elapsed = entry_seq - compression_runtime.last_compaction_entry_seq
        if 0 <= elapsed < min_interval_entries:
            logger.warning(
                "history compaction suppressed by cooldown: %d entries since last "
                "compaction (< %d). history_tokens=%d over budget=%d stays visible "
                "this turn; soft-budget guards remain active.",
                elapsed, min_interval_entries, history_tokens, history_budget,
            )
            return None
    outcome = await try_compaction(
        deps=deps,
        tree=tree,
        request=request,
        query_loop_state=query_loop_state,
        compression_runtime=compression_runtime,
        history_budget=history_budget,
        abort_event=abort_event,
        force=False,
        threshold_override=threshold_override,
        keep_recent_tokens_override=keep_recent_tokens_override,
    )
    if outcome is not None and outcome.ok and getattr(outcome, "compacted", False):
        compression_runtime.last_compaction_entry_seq = len(
            getattr(tree, "entries", {}) or {}
        )
    return outcome


def _resolve_min_interval_entries(deps: Any) -> int:
    """Cooldown window from config; 0 (disabled) on any access failure."""
    try:
        history_cfg = deps.config.context.compaction.history
        return max(0, int(getattr(history_cfg, "min_interval_entries", 0)))
    except Exception:
        return 0


async def recover_from_context_overflow(
    *,
    deps: _CompactionDeps,
    tree: Any,
    request: Any,
    query_loop_state: QueryLoopState,
    compression_runtime: CompressionRuntimeState,
    history_budget: int,
    abort_event: asyncio.Event,
    compaction_policy: TaskCompactionPolicy | None = None,
) -> CompactionOutcome:
    keep_recent_tokens_override = None
    if compaction_policy is not None:
        keep_recent_tokens_override = compaction_policy.keep_recent_tokens
    return await try_compaction(
        deps=deps,
        tree=tree,
        request=request,
        query_loop_state=query_loop_state,
        compression_runtime=compression_runtime,
        history_budget=history_budget,
        abort_event=abort_event,
        force=True,
        keep_recent_tokens_override=keep_recent_tokens_override,
    )


def _find_prior_compaction_summary(tree: Any) -> str | None:
    """Return the summary text of the latest CompactionEntry in the tree.

    Used by the carry-forward anchor to detect sections that the LLM
    dropped in the new summary and mechanically re-append them.
    """
    branch = getattr(tree, "entries", {})
    # Iterate in reverse insertion order; the latest CompactionEntry
    # by position in the branch is the one we want.
    for entry in reversed(list(branch.values())):
        if isinstance(entry, CompactionEntry) and entry.summary:
            return entry.summary
    # Fallback: some trees expose get_branch()
    try:
        for entry in reversed(tree.get_branch()):
            if isinstance(entry, CompactionEntry) and entry.summary:
                return entry.summary
    except Exception:
        pass
    return None


def _patch_summary(result: CompactResult, new_summary: str) -> CompactResult:
    """Return a copy of *result* with the summary replaced.

    CompactResult may be a frozen/immutable dataclass in some
    configurations; model_copy handles both mutable and frozen.
    """
    try:
        return result.model_copy(update={"summary": new_summary})
    except AttributeError:
        result.summary = new_summary
        return result


def _query_messages_for_compaction(
    query_loop_state: QueryLoopState,
    tree: SessionTree | None = None,
) -> list[Any]:
    """Messages fed into compaction.

    Must include the previous compaction summary (meta messages) rendered in
    its LLM form. Without it, a chained compaction only ever sees the tail
    since the previous cut — the earlier summary is silently dropped from
    the new one, i.e. progressive amnesia over long conversations.

    When *tree* is provided, the current Fact Sheet is prepended as an
    immutable context block — this guarantees key facts survive any
    number of chained compactions.
    """
    message_set = query_loop_state.query_view.messages_for_query
    messages = list(message_set.messages)
    meta_messages = list(getattr(message_set, "meta_messages", None) or [])

    # --- Fact Sheet injection ---
    fact_preamble: list[Any] = []
    if tree is not None:
        try:
            from prismclaw.engine.compression.fact_sheet import (
                load_fact_sheet,
                render_fact_sheet_text,
            )
            facts = load_fact_sheet(tree)
            if facts:
                fact_text = render_fact_sheet_text(facts)
                fact_preamble.append({
                    "role": "user",
                    "content": fact_text,
                })
        except Exception:
            pass  # Fact Sheet is best-effort; never block compaction

    if not meta_messages and not fact_preamble:
        return messages
    from prismclaw.engine.context.projection_engine import render_system_reminder_content
    # 从 .session 子模块导入：包 __init__ 的惰性表中没有该符号，走包级导入会
    # 直接 ImportError（曾使 prepare_next_turn 的压缩路径整轮崩溃，会话执行中丢失）。
    from prismclaw.schema.agent.session import SystemReminderProjection

    rendered: list[Any] = list(fact_preamble)
    for reminder in meta_messages:
        # Only persistent memory participates: budget warnings are transient
        # turn-scoped signals and must not leak into the summary.
        if not isinstance(reminder, SystemReminderProjection):
            continue
        if reminder.kind != "history_compaction_summary":
            continue
        rendered.append(
            {
                "role": "user",
                "content": (
                    "[既有压缩摘要——下方每一节都必须出现在"
                    "你的新摘要中。不要省略或替换任何一节，"
                    "请逐字结转每一节。]\n\n"
                    + render_system_reminder_content(reminder)
                ),
            }
        )
    return [*rendered, *messages]


# ──────────────────────────── Predictive Compaction (v4.0) ────────────────────────────


_METRIC_PREDICTIVE_TRIGGER = "compaction.predictive_triggered"


def predict_compaction_trigger(
    runtime: CompressionRuntimeState,
    current_tokens: int,
    trigger_budget: int,
    current_entry_seq: int,
    horizon: int = 3,
    min_samples: int = 4,
    min_interval_entries: int = 0,
    emergency_ratio: float = 0.90,
    history_budget: int = 0,
) -> bool:
    """Predict whether the trigger budget will be reached within *horizon* turns.

    Uses the p75 of observed per-turn growth deltas to project forward.
    Returns True when the projected token count >= trigger_budget.

    Three-tier threshold design:
    - **Predictive** (current < emergency): respects cooldown to avoid
      consecutive compactions that could lose information.
    - **Emergency** (current >= emergency_ratio × history_budget): bypasses
      cooldown because the token count is dangerously close to the hard
      budget — running over budget is worse than a slightly-too-early
      compaction.
    - **Passive** (current > history_budget): handled by the caller.

    Guards:
    - Returns False when fewer than *min_samples* snapshots exist.
    - Returns False when already over budget (let the passive path handle it).
    """
    snapshots = runtime.recent_history_tokens
    if len(snapshots) < min_samples:
        return False

    # ── Emergency valve ──
    # When token usage exceeds emergency_ratio × history_budget, bypass
    # cooldown and trigger immediately.  This prevents the scenario where
    # cooldown blocks a predictive compaction and the next turn injects a
    # large tool result that pushes tokens 15-18% over budget.
    #
    # emergency_threshold is based on history_budget (the hard limit), NOT
    # trigger_budget (the predictive threshold).  This ensures the three
    # tiers are strictly ordered:
    #   Predictive (75% of budget) < Emergency (90% of budget) < Passive (100%)
    if history_budget > 0:
        emergency_threshold = int(history_budget * emergency_ratio)
        if current_tokens >= emergency_threshold:
            try:
                emit(_METRIC_PREDICTIVE_TRIGGER, 1.0, kind="counter")
            except Exception:
                pass
            return True

    # ── Predictive path (with cooldown) ──
    if runtime.last_compaction_entry_seq >= 0:
        interval = max(0, min_interval_entries)
        if interval > 0 and current_entry_seq - runtime.last_compaction_entry_seq < interval:
            return False
    if current_tokens >= trigger_budget:
        return False  # Already over — let the passive path handle it.

    deltas = [b - a for a, b in zip(snapshots, snapshots[1:]) if b > a]
    if len(deltas) < 3:
        return False
    p75 = sorted(deltas)[int(len(deltas) * 0.75)]
    projected = current_tokens + p75 * horizon
    if projected >= trigger_budget:
        try:
            emit(_METRIC_PREDICTIVE_TRIGGER, 1.0, kind="counter")
        except Exception:
            pass
        return True
    return False
