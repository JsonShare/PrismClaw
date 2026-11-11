"""Pure helper functions and dataclasses extracted from turn_preparation.py.

This module contains stateless utilities, data definitions, and logging
functions that do not depend on ``QueryTurnPreparator`` or any class instance.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prismclaw.engine.compression.budget_orchestrator import (
    BudgetOrchestrator,
)
from prismclaw.engine.execution.capabilities.catalog import CapabilityCatalogService
from prismclaw.engine.compression.compaction import estimate_messages_tokens
from prismclaw.schema.content.content_types import RuntimeControlArtifact, ToolSkillInvocation
from prismclaw.schema.content.context import ContextBlock, ContextPackLayout, TaskProfile
from prismclaw.engine.execution.checkpoint import SystemPromptSectionState
from prismclaw.schema.llm.request import PackedLLMRequest
from prismclaw.schema.agent.session import TurnRuntimeMetricsSnapshot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# T14: Skill auto-activation pattern
# ---------------------------------------------------------------------------
_SKILL_GESTURE_RE = re.compile(r"^/([a-zA-Z0-9_:-]+)(?:\s+(.*))?$")

# ---------------------------------------------------------------------------
# T15: Runtime status injection
# ---------------------------------------------------------------------------

# Module-level streak for the usage-missing degradation path (process-wide by
# design: the sentinel watches the deployment's backend behavior, not one
# session).
_USAGE_MISSING_STREAK = 0


# ---------------------------------------------------------------------------
# T14: Skill auto-activation
# ---------------------------------------------------------------------------

def _try_auto_activate_skill(
    *,
    user_message: str,
    capability_catalog: CapabilityCatalogService | None,
    workspace_path: str,
) -> RuntimeControlArtifact | None:
    """Attempt automatic skill activation from the user message.

    Two activation paths:
    1. **Gesture match**: user types ``/pdf``, ``/docx``, etc. — resolve via
       ``resolve_skill_name`` from the skill resolver module.
    2. **Single search match**: when ``SkillDiscoveryRuntime.search()`` returns
       exactly one result, auto-activate it.

    Returns a ``RuntimeControlArtifact`` with ``skill_invocation`` populated
    on success, or ``None`` if no activation is possible (the LLM can still
    fall back to the ``skill_search`` tool).
    """
    if capability_catalog is None:
        return None
    if not isinstance(user_message, str) or not user_message.strip():
        return None

    # --- Path 1: gesture match (e.g. "/pdf my document") ---
    gesture_match = _SKILL_GESTURE_RE.match(user_message.strip())
    if gesture_match is not None:
        skill_ref = gesture_match.group(1)
        task_context = (gesture_match.group(2) or "").strip()
        try:
            from prismclaw.schema.services.skills import resolve_skill_name

            entries = capability_catalog.list_skill_entries()
            resolvable = [
                type("R", (), {"name": e.name})() for e in entries
            ]
            resolved = resolve_skill_name(skill_ref, resolvable)
            if resolved is not None:
                logger.info(
                    "skill_auto_activate: gesture /%s resolved to '%s'",
                    skill_ref, resolved,
                )
                return RuntimeControlArtifact(
                    skill_name=resolved,
                    skill_summary=f"Auto-activated skill '{resolved}' from gesture /{skill_ref}.",
                    skill_invocation=ToolSkillInvocation(
                        skill_name=resolved,
                        task_context=task_context or f"Activate skill {resolved}",
                        source="user",
                    ),
                )
        except Exception:
            logger.debug("skill_auto_activate: gesture resolution failed", exc_info=True)

    # --- Path 2: single search match ---
    try:
        from prismclaw.engine.execution.capabilities.discovery_runtime import SkillDiscoveryRuntime

        discovery = SkillDiscoveryRuntime(capability_catalog=capability_catalog)
        matches = discovery.search(
            user_message.strip(),
            workspace_path=Path(workspace_path),
            limit=2,  # fetch 2 to detect ambiguity
        )
        if len(matches) == 1:
            skill_name = matches[0].name
            logger.info(
                "skill_auto_activate: single search match '%s'",
                skill_name,
            )
            return RuntimeControlArtifact(
                skill_name=skill_name,
                skill_summary=f"Auto-activated skill '{skill_name}' (single search match).",
                skill_invocation=ToolSkillInvocation(
                    skill_name=skill_name,
                    task_context=user_message.strip(),
                    source="system",
                ),
            )
    except Exception:
        logger.debug("skill_auto_activate: search-based resolution failed", exc_info=True)

    return None


# ---------------------------------------------------------------------------
# T15: Runtime status injection
# ---------------------------------------------------------------------------

def _build_runtime_status_blocks(context: Any) -> list[ContextBlock]:
    """Build runtime status blocks for auto-injection at turn start.

    Replaces the deleted jobs/task_status/follow_up_status tools by injecting
    task state directly into the conversation context.
    Token budget: max 500 tokens total.
    """
    blocks: list[ContextBlock] = []
    total_tokens = 0
    try:
        from prismclaw.infra.settings import load_settings
        budget_cap = load_settings().agent.context.budget.subcaps.runtime_status
    except Exception:
        budget_cap = 500

    # 1. Follow-up attention items (from session_tree)
    try:
        from prismclaw.engine.subagent.runtime.follow_up import (
            session_follow_up_snapshot,
            format_parallel_task_attention,
        )
        session_tree = getattr(context, "session_tree", None)
        if session_tree is None:
            session_tree = getattr(
                getattr(context, "state", None), "tree", None,
            )
        if session_tree is not None:
            snapshot = session_follow_up_snapshot(session_tree)
            attention = getattr(snapshot, "parallel_task_attention", None)
            if attention is not None:
                attention_text = format_parallel_task_attention(attention)
                if attention_text and attention_text != "并行任务待关注信息可用。":
                    est = len(attention_text) // 4
                    if total_tokens + est <= budget_cap:
                        blocks.append(
                            context_block(
                                name="runtime_follow_up_attention",
                                content=attention_text,
                                truncatable=True,
                                priority="medium",
                                stability="volatile",
                                metadata={"source_kind": "runtime_status"},
                            )
                        )
                        total_tokens += est
    except ImportError:
        pass
    except Exception:
        logger.debug("_build_runtime_status_blocks: follow_up injection failed", exc_info=True)

    # 2. Goal status (from session_state.goals if present)
    session_state = getattr(context, "session_state", None)
    if session_state is None:
        session_state = getattr(
            getattr(context, "state", None), "session_state", None,
        )
    if session_state is not None:
        goals = getattr(session_state, "goals", [])
        if goals:
            active_goals = [g for g in goals if getattr(g, "status", None) == "active"]
            if active_goals:
                goal_lines = [
                    f"  - {g.goal_id}: {g.objective} "
                    f"(round {g.current_round}/{g.max_goal_rounds})"
                    for g in active_goals
                ]
                goal_text = "Active goals:\n" + "\n".join(goal_lines)
                est = len(goal_text) // 4
                if total_tokens + est <= budget_cap:
                    blocks.append(
                        context_block(
                            name="runtime_goal_status",
                            content=goal_text,
                            truncatable=True,
                            priority="medium",
                            stability="volatile",
                            metadata={"source_kind": "runtime_status"},
                        )
                    )
                    total_tokens += est

    return blocks


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DynamicContextResult:
    assembled_context_layout: ContextPackLayout
    task_profile: TaskProfile | None
    projection: Any  # PromptProjection — avoid circular import
    budget_audit: Any  # BudgetAuditTrail
    system_prompt_sections: SystemPromptSectionState


@dataclass
class TurnTokenTelemetry:
    frozen_tokens: int
    bootstrap_tokens: int
    l1_tokens: int
    memory_context_tokens: int
    per_turn_tokens: int
    history_tokens: int
    history_messages_tokens: int = 0
    context_payload_tokens: int = 0
    tools_tokens: int = 0


@dataclass
class TurnPreflightResult:
    assembled_context_layout: ContextPackLayout
    task_profile: TaskProfile | None
    projection: Any  # PromptProjection
    packed_request: PackedLLMRequest
    request_snapshot: Any | None  # RequestAssemblySnapshot | None
    history_tokens: int
    request_mode: str | None
    budget_audit: Any  # BudgetAuditTrail
    system_prompt_sections: SystemPromptSectionState

    def to_context_layout(self) -> ContextPackLayout:
        return self.projection.to_context_layout()

    def compute_turn_telemetry(self) -> TurnTokenTelemetry:
        telemetry = compute_turn_token_telemetry(
            turn_context_layout=self.to_context_layout(),
            assembled_plan=self.assembled_context_layout,
            assembled_messages=self.projection.messages,
        )
        # self.history_tokens 已在 turn_preparation 中累加 context_payload_tokens
        # （legacy bridge 把动态块渲染成 <system-context> user 消息）。
        # compute_turn_token_telemetry 只按 messages 重算，会漏掉这部分，
        # 导致 token_usage drift 长期 +46%。此处用已含 payload 的值覆盖。
        ctx_payload = int(getattr(self.packed_request, "context_payload_tokens", 0) or 0)
        history_msgs = self.history_tokens - ctx_payload
        return TurnTokenTelemetry(
            frozen_tokens=telemetry.frozen_tokens,
            bootstrap_tokens=telemetry.bootstrap_tokens,
            l1_tokens=telemetry.l1_tokens,
            memory_context_tokens=telemetry.memory_context_tokens,
            per_turn_tokens=telemetry.per_turn_tokens,
            history_tokens=self.history_tokens,
            history_messages_tokens=history_msgs,
            context_payload_tokens=ctx_payload,
        )


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------

def _require_capability_catalog(deps: Any) -> CapabilityCatalogService:
    catalog = getattr(deps, "capability_catalog", None)
    if isinstance(catalog, CapabilityCatalogService):
        return catalog
    raise RuntimeError(
        "AgentRunnerDeps.capability_catalog is required for prompt planning"
    )


def ensure_user_message(
    messages: list[dict[str, Any]],
    *,
    fallback_user_message: str,
) -> list[dict[str, Any]]:
    if any((m or {}).get("role") == "user" for m in messages if isinstance(m, dict)):
        return messages
    fallback = (fallback_user_message or "").strip() or "请继续处理用户的请求。"
    return [*messages, {"role": "user", "content": fallback}]


def context_block(
    *,
    name: str,
    content: str,
    zone: str = "dynamic",
    truncatable: bool = True,
    cacheable: bool = False,
    priority: str = "medium",
    stability: str = "volatile",
    replaceable: bool = False,
    metadata: dict[str, Any] | None = None,
) -> ContextBlock:
    return ContextBlock(
        id=name,
        zone=zone,  # type: ignore[arg-type]
        source=name,
        content=content,
        stability=stability,  # type: ignore[arg-type]
        priority=priority,  # type: ignore[arg-type]
        cacheable=cacheable,
        truncatable=truncatable,
        replaceable=replaceable,
        metadata=dict(metadata or {}),
    )


def sum_block_tokens(blocks: list[ContextBlock], *, source: str) -> int:
    return sum(block.estimated_tokens for block in blocks if block.source == source)


def compute_turn_token_telemetry(
    *,
    turn_context_layout: ContextPackLayout,
    assembled_plan: ContextPackLayout,
    assembled_messages: list[dict[str, Any]],
) -> TurnTokenTelemetry:
    frozen_tokens = sum(
        block.estimated_tokens for block in turn_context_layout.zone_blocks("static")
    )
    bootstrap_tokens = sum_block_tokens(
        turn_context_layout.zone_blocks("static"),
        source="bootstrap",
    )
    l1_tokens = sum_block_tokens(
        turn_context_layout.zone_blocks("static"),
        source="l1_snapshot",
    )
    memory_context_tokens = sum(
        block.estimated_tokens for block in assembled_plan.non_static_blocks()
    )
    per_turn_tokens = max(
        0,
        sum(block.estimated_tokens for block in turn_context_layout.non_static_blocks())
        - memory_context_tokens,
    )
    history_tokens = estimate_messages_tokens(assembled_messages)
    return TurnTokenTelemetry(
        frozen_tokens=frozen_tokens,
        bootstrap_tokens=bootstrap_tokens,
        l1_tokens=l1_tokens,
        memory_context_tokens=memory_context_tokens,
        per_turn_tokens=per_turn_tokens,
        history_tokens=history_tokens,
    )


def log_turn_token_usage(
    *,
    logger_: logging.Logger,
    iteration: int,
    telemetry: TurnTokenTelemetry,
    usage: Any,
    history_budget: int,
    history_compaction_count: int = 0,
    history_compaction_reduction: int = 0,
    tools_tokens: int = 0,
) -> None:
    """Per-LLM-call token telemetry + budget drift sentinel (SDD R-A2 A-3).

    Accounting note (E1 勘误): the OLD log line printed ``dynamic_payload``
    (= memory_ctx + per_turn) as an ADDITIONAL component, which double-counted
    when summed with memory_ctx/per_turn. The line now prints the mutually
    exclusive components only: accounted = frozen(static zone, incl. the
    bootstrap/L1 sub-blocks) + per_turn + memory_ctx + history + tools.

    Drift sentinel (W13 防复发): drift = real input − accounted. Any new
    un-accounted wire component (request-field additions, injection sites,
    estimator regressions) shows up here — exactly how the A-0 spike found
    the payload double-injection. drift/input > 15% → ERROR. usage-missing
    (some OpenAI-compatible servers omit streaming usage) degrades to N/A
    with a streak counter; the sentinel failing silently is itself an ERROR
    at 50 consecutive misses (W15 principle applied to the sentinel itself).
    """
    global _USAGE_MISSING_STREAK
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    cache_creation = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    cache_read_ratio_pct = (cache_read / input_tokens) * 100 if input_tokens > 0 else 0.0

    # tools_tokens is the calibrated full-registry schema cost (UTF-8 bytes ÷ 5,
    # see ``ToolRegistry.wire_tokens_estimate``), so it is accounted for here
    # just like every other wire component. Prior behavior omitted it because
    # the old CJK heuristic overestimated schemas by ~1.8× (11,840 vs ~6,450 for
    # 31 tools), which made drift permanently positive (~40% of input) and
    # masked real gaps. After calibration the residual on the same runs is
    # +16 / −61 tokens (0.1% / 0.5%), i.e. the block is now accounted to the
    # measurement noise level. Do NOT widen the 15% threshold to suppress
    # noise — the threshold is the invariant; fix the estimate instead.
    #
    # ``frozen_tokens`` is the WHOLE static zone, so ``bootstrap_tokens`` and
    # ``l1_tokens`` are sub-blocks of it (same static zone, filtered by
    # source). They are printed separately for decomposition only and must
    # NOT be added again — doing so over-accounts whenever bootstrap/L1
    # snapshots are injected, which erodes the sentinel in the dangerous
    # direction (hiding a genuinely un-accounted component).
    accounted = (
        telemetry.frozen_tokens
        + telemetry.per_turn_tokens
        + telemetry.memory_context_tokens
        + telemetry.history_tokens
        + tools_tokens
    )
    if input_tokens > 0:
        drift = input_tokens - accounted
        _USAGE_MISSING_STREAK = 0
        drift_label = str(drift)
        if abs(drift) / input_tokens > 0.15:
            # 组件分解：哪个 accounted 分量偏得最多，直接在错误行里给出。
            _components = [
                ("frozen", telemetry.frozen_tokens),
                ("per_turn", telemetry.per_turn_tokens),
                ("memory_ctx", telemetry.memory_context_tokens),
                ("history_msgs", telemetry.history_messages_tokens),
                ("ctx_payload", telemetry.context_payload_tokens),
                ("tools", tools_tokens),
            ]
            _breakdown = " ".join(f"{n}={v}" for n, v in _components)
            logger_.error(
                "token_usage drift: real input %d vs accounted %d (drift %d, %.0f%%) — "
                "components [%s] — compare with a mock capture "
                "(scripts/eval + /tmp/mock_openai_capture.py recipe) before it overflows "
                "the window.",
                input_tokens, accounted, drift, abs(drift) / input_tokens * 100,
                _breakdown,
            )
    else:
        # Degraded mode: no usage from the backend — sentinel cannot run.
        _USAGE_MISSING_STREAK += 1
        drift_label = "N/A"
        if _USAGE_MISSING_STREAK >= 50:
            logger_.error(
                "token_usage drift sentinel blind for %d consecutive calls — "
                "backend returns no streaming usage. Budget accounting is "
                "unverifiable on this deployment.",
                _USAGE_MISSING_STREAK,
            )
            _USAGE_MISSING_STREAK = 0

    logger_.info(
        "token_usage turn=%d frozen=%d bootstrap=%d l1=%d memory_ctx=%d per_turn=%d "
        "history(total=%d msgs=%d ctx_payload=%d) tools=%d input=%d output=%d "
        "budget_remaining=%d drift=%s cache_creation=%d cache_read=%d "
        "cache_read_ratio=%.2f history_compactions=%d history_compaction_reduction=%d",
        iteration,
        telemetry.frozen_tokens,
        telemetry.bootstrap_tokens,
        telemetry.l1_tokens,
        telemetry.memory_context_tokens,
        telemetry.per_turn_tokens,
        telemetry.history_tokens,
        telemetry.history_messages_tokens,
        telemetry.context_payload_tokens,
        tools_tokens,
        input_tokens,
        output_tokens,
        max(0, history_budget - telemetry.history_tokens),
        drift_label,
        cache_creation,
        cache_read,
        cache_read_ratio_pct,
        history_compaction_count,
        history_compaction_reduction,
    )


def dynamic_payload_tokens(telemetry: TurnTokenTelemetry) -> int:
    return telemetry.memory_context_tokens + telemetry.per_turn_tokens


def log_session_runtime_metrics(
    *,
    logger_: logging.Logger,
    turn_metrics_snapshots: list[TurnRuntimeMetricsSnapshot],
    history_compaction_count: int,
    stable_prefix_follow_up_turns: int,
    stable_prefix_reuse_turns: int,
    stable_prefix_digest_changes: int,
) -> None:
    turn_count = len(turn_metrics_snapshots)
    total_dynamic_payload = sum(
        int(snapshot["dynamic_payload"]) for snapshot in turn_metrics_snapshots
    )
    result_payload_reductions = [
        int(snapshot["result_payload_reduction"])
        for snapshot in turn_metrics_snapshots
        if int(snapshot["result_payload_reduction"]) > 0
    ]
    total_result_payload_reduction = sum(result_payload_reductions)
    result_payload_reduction_count = len(result_payload_reductions)
    total_input_tokens = sum(int(snapshot["input_tokens"]) for snapshot in turn_metrics_snapshots)
    total_cache_read_tokens = sum(
        int(snapshot["cache_read_tokens"]) for snapshot in turn_metrics_snapshots
    )
    average_dynamic_payload = round(total_dynamic_payload / turn_count) if turn_count > 0 else 0
    average_result_payload_reduction = (
        round(total_result_payload_reduction / result_payload_reduction_count)
        if result_payload_reduction_count > 0
        else 0
    )
    cache_read_ratio_pct = (
        (total_cache_read_tokens / total_input_tokens) * 100 if total_input_tokens > 0 else 0.0
    )
    stable_prefix_reuse_ratio = (
        (stable_prefix_reuse_turns / stable_prefix_follow_up_turns) * 100
        if stable_prefix_follow_up_turns > 0
        else 0.0
    )
    logger_.info(
        "session_runtime_metrics turns=%d average_dynamic_payload=%d "
        "average_result_payload_reduction=%d cache_read_ratio=%.2f "
        "history_compactions=%d "
        "stable_prefix_follow_up_turns=%d stable_prefix_reuse_turns=%d "
        "stable_prefix_reuse_ratio=%.2f stable_prefix_digest_changes=%d",
        turn_count,
        average_dynamic_payload,
        average_result_payload_reduction,
        cache_read_ratio_pct,
        history_compaction_count,
        stable_prefix_follow_up_turns,
        stable_prefix_reuse_turns,
        stable_prefix_reuse_ratio,
        stable_prefix_digest_changes,
    )


def log_session_success_verdict(
    *,
    logger_: logging.Logger,
    turn_metrics_snapshots: list[TurnRuntimeMetricsSnapshot],
    stable_prefix_follow_up_turns: int,
    stable_prefix_reuse_turns: int,
    stable_prefix_digest_changes: int,
) -> None:
    turn_count = len(turn_metrics_snapshots)
    first_turn = turn_metrics_snapshots[0] if turn_metrics_snapshots else None
    post_baseline_turns = turn_metrics_snapshots[1:] if len(turn_metrics_snapshots) > 1 else []
    follow_up_turns = [
        snapshot for snapshot in post_baseline_turns if snapshot["request_mode"] == "follow_up"
    ]
    follow_up_count = len(follow_up_turns)
    baseline_dynamic_payload = (
        int(first_turn["dynamic_payload"]) if first_turn is not None else 0
    )
    follow_up_average_dynamic_payload = (
        round(
            sum(int(snapshot["dynamic_payload"]) for snapshot in follow_up_turns) / follow_up_count
        )
        if follow_up_count > 0
        else 0
    )
    dynamic_payload_status = "not_applicable"
    if follow_up_count > 0:
        dynamic_payload_status = (
            "decreased"
            if follow_up_average_dynamic_payload < baseline_dynamic_payload
            else "flat_or_higher"
        )

    baseline_cache_read_ratio = (
        _turn_cache_read_ratio(first_turn) if first_turn is not None else 0.0
    )
    follow_up_cache_read_ratio = (
        _aggregate_cache_read_ratio(follow_up_turns) if follow_up_turns else 0.0
    )
    cache_read_status = "not_applicable"
    if follow_up_count > 0:
        cache_read_status = (
            "improved"
            if follow_up_cache_read_ratio > baseline_cache_read_ratio
            else "flat_or_lower"
        )

    stable_prefix_status = "not_applicable"
    if stable_prefix_follow_up_turns > 0:
        stable_prefix_status = "preserved" if stable_prefix_digest_changes == 0 else "drifted"

    logger_.info(
        "session_success_verdict turns=%d follow_up_turns=%d "
        "dynamic_payload_status=%s baseline_dynamic_payload=%d follow_up_average_dynamic_payload=%d "
        "cache_read_status=%s baseline_cache_read_ratio=%.2f follow_up_cache_read_ratio=%.2f "
        "stable_prefix_status=%s stable_prefix_reuse_turns=%d stable_prefix_follow_up_turns=%d",
        turn_count,
        follow_up_count,
        dynamic_payload_status,
        baseline_dynamic_payload,
        follow_up_average_dynamic_payload,
        cache_read_status,
        baseline_cache_read_ratio,
        follow_up_cache_read_ratio,
        stable_prefix_status,
        stable_prefix_reuse_turns,
        stable_prefix_follow_up_turns,
    )


def _aggregate_cache_read_ratio(turn_metrics_snapshots: list[TurnRuntimeMetricsSnapshot]) -> float:
    total_input_tokens = sum(int(snapshot["input_tokens"]) for snapshot in turn_metrics_snapshots)
    total_cache_read_tokens = sum(
        int(snapshot["cache_read_tokens"]) for snapshot in turn_metrics_snapshots
    )
    return (total_cache_read_tokens / total_input_tokens) * 100 if total_input_tokens > 0 else 0.0


def _turn_cache_read_ratio(turn_snapshot: TurnRuntimeMetricsSnapshot) -> float:
    input_tokens = int(turn_snapshot["input_tokens"])
    cache_read_tokens = int(turn_snapshot["cache_read_tokens"])
    return (cache_read_tokens / input_tokens) * 100 if input_tokens > 0 else 0.0


def apply_context_layout_budgets(
    plan: ContextPackLayout,
    *,
    system_budget: int,
    dynamic_budget: int,
    result_budget: int | None = None,
    ports: Any = None,
) -> ContextPackLayout:
    return orchestrate_context_layout_budgets(
        plan,
        system_budget=system_budget,
        dynamic_budget=dynamic_budget,
        result_budget=result_budget,
        ports=ports,
    ).context_layout


def orchestrate_context_layout_budgets(
    plan: ContextPackLayout,
    *,
    system_budget: int,
    dynamic_budget: int,
    result_budget: int | None = None,
    working_dir: str | Path | None = None,
    ports: Any = None,
):
    orchestrator = BudgetOrchestrator(working_dir=working_dir, ports=ports)
    return orchestrator.apply_context_layout_budgets(
        plan,
        system_budget=system_budget,
        dynamic_budget=dynamic_budget,
        result_budget=result_budget,
    )


def apply_request_mode_skeleton(
    plan: ContextPackLayout,
    *,
    request_mode: str | None,
) -> ContextPackLayout:
    if request_mode != "cold_start":
        return plan
    plan.dynamic_blocks = [
        block
        for block in plan.dynamic_blocks
        if not (
            block.source == "memory_archives"
            and block.priority in {"low", "medium"}
        )
    ]
    return plan


def build_turn_context_layout(
    *,
    frozen_blocks: list[ContextBlock],
    per_turn_blocks: list[ContextBlock],
    assembled_plan: ContextPackLayout,
    attachment_guidance: str | None,
    extra_system: str | None,
    task_profile: TaskProfile | None,
    fact_sheet_text: str | None = None,
) -> ContextPackLayout:
    plan = assembled_plan.model_copy(deep=True)

    plan.static_blocks = [*frozen_blocks, *plan.static_blocks]
    plan.dynamic_blocks = [*per_turn_blocks, *plan.dynamic_blocks]

    if attachment_guidance:
        plan.dynamic_blocks.append(
            context_block(
                name="attachment_guidance",
                content=attachment_guidance,
                truncatable=True,
                priority="high",
                metadata={"source_kind": "attachment"},
            )
        )

    if extra_system:
        plan.dynamic_blocks.append(
            context_block(
                name="extra_system",
                content=extra_system,
                truncatable=False,
                priority="critical",
                metadata={"source_kind": "user_extra_system"},
            )
        )

    # Fact Sheet: structured key-value facts that survive any number of
    # chained compactions.  Injected at "critical" priority so they are
    # never truncated.
    if fact_sheet_text:
        plan.dynamic_blocks.append(
            context_block(
                name="fact_sheet",
                content=fact_sheet_text,
                truncatable=False,
                priority="critical",
                metadata={"source_kind": "fact_sheet"},
            )
        )

    plan.apply_task_profile(task_profile)
    return plan
