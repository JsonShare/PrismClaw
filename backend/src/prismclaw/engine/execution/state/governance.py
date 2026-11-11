"""无工具回合治理：把「模型停止调用工具」翻译成完成内核的一次裁决。

本模块只做三件事：

* 空答复 / 纯推理的重试（响应质量，不属完成判定）；
* 调用 ``CompletionKernel.decide`` 得到唯一裁决；
* 把裁决落成副作用（审查留痕、完成 mutation、驱动提醒、终态事件）。

终止权威不在这里：``CompletionKernel`` 是唯一决策者，本模块不重新判断
「是否完成」，也不再有第二套计数器。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from prismclaw.engine.execution.control.completion import (
    CompletionContract,
    build_review_record,
)
from prismclaw.engine.execution.control.kernel import Decision, CompletionKernel, TerminalReason
from prismclaw.engine.execution.loop.arbiter import RunContext, record_review_outcome
from prismclaw.engine.execution.state.task_persistence import (
    _new_task_mutation,
    _build_runtime_mutation_patch,
    _build_task_terminal_mutation,
    _evaluate_and_apply_task_mutation,
    _set_resume_boundary,
)
from prismclaw.engine.execution.loop.bootstrap import (
    _persist_session_memory_snapshot,
)
from prismclaw.engine.execution.skills.skill_dispatch import _append_control_message_override
from prismclaw.schema.agent.session import ProjectionInputMessage, SystemReminderProjection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# No-tool turn governance result
# ---------------------------------------------------------------------------

@dataclass
class NoToolGovernanceResult:
    """内核裁决 + 调用方需要落地的副作用。

    ``decision`` 的 verdict 决定骨架动作；``appended_messages_override``
    仅在继续执行时由调用方写到 ``state.pending_messages``；``done_event``
    仅在受理完成时非空；``terminal_code`` 非空时由调用方走统一终态副作用
    （``_terminate``），本模块不自行落终态。
    """

    decision: Decision
    appended_messages_override: list[ProjectionInputMessage] | None = None
    done_event: Any = None
    terminal_code: str | None = None
    terminal_message: str | None = None
    terminated_by: TerminalReason | None = None


# ---------------------------------------------------------------------------
# Goal driving prompt
# ---------------------------------------------------------------------------

_EMPTY_RESPONSE_HEADLINES = {
    "empty": "本次回合没有产生可见答复，任务尚未完成。",
    "reasoning": "本次回合只产生了内部推理，没有可见答复，任务尚未完成。",
}

_GAP_HEADLINE = "完成申报未获受理：机械可核对项仍有缺口。"


def _build_goal_drive_prompt(
    *,
    contract: CompletionContract | None,
    headline: str,
    gaps: tuple[str, ...] = (),
) -> str:
    """驱动文案：携带完成要求与缺口，取代静态 repair/retry 文案。

    完成要求来自冻结的完成契约准则，保证模型自评看到的要求与事后
    逐条核对的要求是同一组。
    """
    lines = ["[Goal Review]", headline]
    if contract is not None and contract.criteria:
        anchor_lines = ["完成要求（逐条满足才算完成）："]
        anchor_lines.extend(f"- {criterion.describe()}" for criterion in contract.criteria)
        anchors = "\n".join(anchor_lines)
    else:
        anchors = "本次请求无需机械产物，给出实质答复即可。"
    lines.append(anchors)
    if gaps:
        lines.append("未满足项：")
        lines.extend(f"- {gap}" for gap in gaps)
    lines.append("请继续执行并补齐上述项，真正达成后再给出最终答复。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# No-tool turn governance
# ---------------------------------------------------------------------------

async def evaluate_no_tool_governance(
    ctx: RunContext,
    *,
    response: Any,
    event: Any,
    kernel: CompletionKernel,
    retry_counts: Any,
    response_policy: Any,
    state_machine: Any,
    prepared_turn: Any,
    total_input_tokens: int,
    total_output_tokens: int,
    total_cache_creation_tokens: int,
    total_cache_read_tokens: int,
    run_state: Any,
    usd_cost: float = 0.0,
) -> NoToolGovernanceResult:
    """对无工具回合执行一次裁决。

    顺序即优先级：

    1. 空答复 / 纯推理且未超上限 → 重试（响应质量问题，非完成判定）；
    2. 空答复 / 纯推理已超上限 → ``no_response_exhausted`` 硬边界终态；
    3. 其余交内核：无契约 → ``complete_open``；有契约 → 准则逐条评估。
    """
    from prismclaw.engine.execution.dispatch.response_handling import (
        decide_no_tool_turn,
        complete_no_tool_turn,
        append_assistant_response,
    )
    from prismclaw.engine.execution.dispatch.turn_utils import strip_thinking
    from prismclaw.engine.execution.loop.bootstrap import _append
    from prismclaw.engine.execution.checkpoint import ResumeBoundary
    from prismclaw.engine.execution.loop.arbiter import persist_snapshot_fn

    deps = ctx.deps
    request = ctx.request
    tree = ctx.tree
    snapshot = ctx.snapshot
    task_runtime = ctx.task_runtime
    evidence_ledger = ctx.evidence_ledger  # 引用语义：append 直接修改 ctx 上的同一个 list

    decision_no_tool = decide_no_tool_turn(
        response=response,
        config=deps.config,
        retry_counts=retry_counts,
        response_policy=response_policy,
    )

    # ── 1. 空答复 / 纯推理的响应质量重试（不属于完成判定） ──
    if decision_no_tool.action == "retry":
        state_machine.record_retry(decision_no_tool.classification)
        await append_assistant_response(
            deps=deps,
            tree=tree,
            append_entry=_append,
            text=strip_thinking(response.text or ""),
        )
        drive_prompt = _build_goal_drive_prompt(
            contract=kernel.state.contract,
            headline=_EMPTY_RESPONSE_HEADLINES.get(
                decision_no_tool.classification,
                "任务尚未完成。",
            ),
        )
        return NoToolGovernanceResult(
            decision=Decision("PROCEED", "retry"),
            appended_messages_override=_append_control_message_override(
                None,
                SystemReminderProjection.text_reminder(drive_prompt),
            ),
        )

    # ── 2. 重试已用尽仍无可见答复：硬边界终态 ──
    if decision_no_tool.classification in {"empty", "reasoning"}:
        logger.info(
            "no_tool_exhausted: classification=%s task_id=%s",
            decision_no_tool.classification,
            task_runtime.task_id,
        )
        return NoToolGovernanceResult(
            decision=Decision("TERMINATE", TerminalReason.NO_RESPONSE_EXHAUSTED),
            terminal_code="no_response_exhausted",
            terminal_message="模型连续多次未产生可见答复，任务未完成即终止。",
        )

    # ── 3. 完成内核的唯一裁决 ──
    # blocked 申报意图在工具回合写入、在此一次性消费；内核负责判定它是
    # 否成立（未尝试即申报会被拒绝并计入 repairs）。
    tool_ctx = ctx._tool_ctx
    block_intent = getattr(tool_ctx, "goal_block_intent", None)
    if block_intent is not None:
        tool_ctx.goal_block_intent = None
    decision = kernel.decide(event, usd_cost=usd_cost)
    review = kernel.last_review

    # ── 4. 审查留痕（受理与不受理都写，保证自评/留痕同源） ──
    review_evidence_ids: list[str] = []
    _gov_p_t0 = time.monotonic()
    if review is not None:
        task_runtime, review_evidence_ids = await _persist_review_record(
            ctx,
            task_runtime=task_runtime,
            review=review,
            evidence_ledger=evidence_ledger,
        )
    _review_persist_ms = (time.monotonic() - _gov_p_t0) * 1000
    if _review_persist_ms > 500.0:
        logger.info("gov_persist_timing phase=review total=%.0fms", _review_persist_ms)

    if decision.verdict == "TERMINATE":
        if decision.code == TerminalReason.BLOCKED_DECLARED:
            code = block_intent.code if block_intent is not None else "blocked"
            message = (
                block_intent.message
                if block_intent is not None
                else "模型申报阻塞且已有执行尝试。"
            )
            logger.info(
                "goal_blocked: code=%s repairs=%s task_id=%s",
                code,
                kernel.state.repairs,
                task_runtime.task_id,
            )
            return NoToolGovernanceResult(
                decision=decision,
                terminal_code=code,
                terminal_message=message,
                terminated_by=TerminalReason.BLOCKED,
            )
        # 预算类 / 修复耗尽：同走统一终态副作用。
        return NoToolGovernanceResult(decision=decision, terminal_code=decision.code)

    # ── 5. 不受理：驱动继续（携带缺口） ──
    if decision.verdict == "REPAIR":
        gaps = tuple(gap.detail or gap.code for gap in decision.gaps)
        if decision.code == "blocked_rejected":
            await append_assistant_response(
                deps=deps,
                tree=tree,
                append_entry=_append,
                text=strip_thinking(response.text or ""),
            )
            headline = "阻塞申报暂不受理：尚未执行任何尝试。"
            if block_intent is not None:
                gaps = (*gaps, f"申报理由：{block_intent.code} — {block_intent.message}")
        elif decision.code == "advisory_nudge":
            headline = "本次回合可能未产出交付物（提醒一次，不阻塞完成）。"
        else:
            headline = _GAP_HEADLINE
        return NoToolGovernanceResult(
            decision=decision,
            appended_messages_override=_append_control_message_override(
                None,
                SystemReminderProjection.text_reminder(
                    _build_goal_drive_prompt(
                        contract=kernel.state.contract,
                        headline=headline,
                        gaps=gaps,
                    )
                ),
            ),
        )

    # ── 6. 受理完成：终态 mutation + done 事件 ──
    # 终态标签（"done" / "max_tokens"）由调用方统一赋值，此处不写。
    _gov_p_t0 = time.monotonic()
    terminal_mutation = _build_task_terminal_mutation(
        task_runtime,
        actor_kind="loop",
        evidence_ids=list(review_evidence_ids),
    )
    task_runtime, completion_applied, completion_reason = (
        await _evaluate_and_apply_task_mutation(
            deps=deps,
            tree=tree,
            snapshot=snapshot,
            task_runtime=task_runtime,
            mutation=terminal_mutation,
            evidence_records=evidence_ledger,
        )
    )
    _mutation_ms = (time.monotonic() - _gov_p_t0) * 1000
    if not completion_applied:
        raise RuntimeError(
            f"completion mutation rejected after review: {completion_reason}"
        )
    ctx.task_runtime = task_runtime
    run_state.current_phase = "finalize_turn"
    _set_resume_boundary(snapshot, boundary=ResumeBoundary.AFTER_TURN_FINALIZE)
    _gov_p_t0 = time.monotonic()
    await persist_snapshot_fn(ctx)
    _snapshot_ms = (time.monotonic() - _gov_p_t0) * 1000
    _gov_p_t0 = time.monotonic()
    done_event = await complete_no_tool_turn(
        deps=deps,
        request=request,
        tree=tree,
        session_messages=list(
            prepared_turn.query_loop_state.query_view.source_messages.messages
        ),
        append_entry=_append,
        response_text=response.text,
        usage_totals={
            "input": total_input_tokens,
            "output": total_output_tokens,
            "cache_creation": total_cache_creation_tokens,
            "cache_read": total_cache_read_tokens,
        },
    )
    _complete_ms = (time.monotonic() - _gov_p_t0) * 1000
    _gov_p_t0 = time.monotonic()
    await _persist_session_memory_snapshot(
        deps,
        tree,
        session_id=request.session_id,
    )
    _mem_snap_ms = (time.monotonic() - _gov_p_t0) * 1000
    _gov_total_ms = _review_persist_ms + _mutation_ms + _snapshot_ms + _complete_ms + _mem_snap_ms
    logger.info(
        "gov_persist_timing phase=acceptance review=%.0fms mutation=%.0fms "
        "snapshot=%.0fms complete=%.0fms mem_snapshot=%.0fms total=%.0fms",
        _review_persist_ms,
        _mutation_ms,
        _snapshot_ms,
        _complete_ms,
        _mem_snap_ms,
        _gov_total_ms,
    )
    return NoToolGovernanceResult(
        decision=decision,
        done_event=done_event,
    )


# ---------------------------------------------------------------------------
# Helpers (factored out from inline closures)
# ---------------------------------------------------------------------------

async def _persist_review_record(
    ctx: RunContext,
    *,
    task_runtime: Any,
    review: Any,
    evidence_ledger: list[Any],
) -> tuple[Any, list[str]]:
    """把准则评估结果写成 review mutation（审计留痕），返回 (任务运行时, 证据 id)。"""
    review_record = build_review_record(task_runtime=task_runtime, result=review)
    record_review_outcome(ctx, review_record)
    review_mutation = _new_task_mutation(
        task_runtime,
        actor_kind="review",
        mutation_type="task.review.record",
        patch=_build_runtime_mutation_patch(task_runtime),
        evidence_ids=list(review_record.evidence_ids),
    )
    task_runtime, applied, reason = await _evaluate_and_apply_task_mutation(
        deps=ctx.deps,
        tree=ctx.tree,
        snapshot=ctx.snapshot,
        task_runtime=task_runtime,
        mutation=review_mutation,
        evidence_records=evidence_ledger,
        review_record=review_record,
    )
    if not applied:
        raise RuntimeError(f"completion review mutation rejected: {reason}")
    # NOTE: ctx._tool_ctx.task_runtime 是 ParallelTaskExtras（引擎级运行时容器，
    # 持有 progress_emitter / parallel_task_store），不得覆写为 TaskRuntimeState。
    ctx.task_runtime = task_runtime
    return task_runtime, list(review_record.evidence_ids)

