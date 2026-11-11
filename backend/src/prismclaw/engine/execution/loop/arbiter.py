"""运行上下文 / 任务变更仲裁 / 终态副作用。

2026-10-30 收权：恢复熔断（压缩重启 / 工具全拒）已收归
``control.kernel.RecoveryPolicy``（唯一链、无终态权）；本模块不再有
``Arbiter`` / ``IterationSignals`` / ``ContinueDecision`` / ``consult_arbiter``。

保留的职责：

* ``RunContext``                 — 拆分后各模块共享的可变运行时上下文
* ``evaluate_task_mutation``     — 并发版本 / entry 就绪 / review 记录 / 恢复探针
* ``persist_snapshot_fn`` / ``mark_task_blocked`` — 终态副作用
* ``record_review_outcome``      — 审查遥测
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from prismclaw.schema.agent.task_runtime import (
    EvidenceRecord,
    ReviewDecisionRecord,
    TaskMutation,
    TaskRuntimeState,
)
from prismclaw.engine.execution.control.kernel import TerminalReason
from prismclaw.engine.foundation.evidence_classify import is_recovery_probe_evidence
from prismclaw.engine.execution.checkpoint import persist_run_snapshot
from prismclaw.engine.execution.state.task_persistence import (
    _evaluate_and_apply_task_mutation,
    _new_task_mutation,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MutationDecision:
    accepted: bool
    reason_code: str
    error_message: str | None = None


def evaluate_task_mutation(
    mutation: TaskMutation,
    *,
    task_runtime: TaskRuntimeState,
    evidence_records: list[EvidenceRecord] | None = None,
    review_decision: ReviewDecisionRecord | None = None,
) -> MutationDecision:
    """结构性仲裁：并发版本 / entry 就绪 / review 记录 / 恢复探针。

    完成证据门禁的唯一权威是完成申报受理（``control.completion``，
    准则逐条评估）；这里不重复实现证据检查。
    """
    if mutation.task_id != task_runtime.task_id:
        return MutationDecision(
            accepted=False,
            reason_code="mutation_task_mismatch",
            error_message=(
                f"变更的 task_id={mutation.task_id!r} 与"
                f"运行时的 task_id={task_runtime.task_id!r} 不一致"
            ),
        )
    if mutation.expected_state_version != task_runtime.state_version:
        return MutationDecision(
            accepted=False,
            reason_code="mutation_version_conflict",
            error_message=(
                f"变更期望的 state_version={mutation.expected_state_version}，"
                f"但运行时为 {task_runtime.state_version}"
            ),
        )

    if (
        task_runtime.entry_resolution_status != "ready"
        and mutation.mutation_type != "task.block"
    ):
        return MutationDecision(
            accepted=False,
            reason_code=f"entry_resolution_{task_runtime.entry_resolution_status}",
            error_message=(
                "任务条目尚未就绪；在条目解析成功前，运行时变更被阻断"
            ),
        )

    if mutation.mutation_type == "task.review.record":
        if review_decision is None:
            return MutationDecision(
                accepted=False,
                reason_code="review_decision_missing",
                error_message="review 变更需要结构化的审查决策",
            )
        return MutationDecision(accepted=True, reason_code="ok")

    if mutation.mutation_type == "task.resume_probe_completed":
        probe_records = [
            record
            for record in (evidence_records or [])
            if record.task_id == task_runtime.task_id
            and is_recovery_probe_evidence(record)
        ]
        if not probe_records:
            return MutationDecision(
                accepted=False,
                reason_code="recovery_probe_required",
                error_message="恢复任务需要恢复探针证据",
            )
        return MutationDecision(accepted=True, reason_code="ok")

    return MutationDecision(accepted=True, reason_code="ok")


# ---------------------------------------------------------------------------
# RunContext — 拆分后各模块共享的可变运行时上下文
# ---------------------------------------------------------------------------

@dataclass
class RunContext:
    """拆分后各模块共享的可变运行时上下文。

    替代 nonlocal 变量声明——通过可变 dataclass 字段实现跨函数状态共享。
    """
    deps: Any = None
    tree: Any = None
    request: Any = None
    snapshot: Any = None
    audit_store: Any = None
    abort_event: Any = None
    hotpath: Any = None
    terminated_by: TerminalReason | None = None
    task_runtime: Any = None
    iteration: int = 0
    evidence_ledger: Any = None
    recovery: Any = None
    run_deadline_s: float = 0.0
    run_started: float = 0.0
    _tool_ctx: Any = None
    _tool_workspace_path: Any = None
    _prepared_turn: Any = None


async def persist_snapshot_fn(ctx: RunContext) -> None:
    """持久化当前运行快照。"""
    await persist_run_snapshot(
        ctx.deps.session_store,
        ctx.tree,
        ctx.snapshot,
        snapshot_store=ctx.deps.persistence.run_snapshot_store,
    )


async def mark_task_blocked(ctx: RunContext, reason: str) -> None:
    """标记任务为 blocked 状态。"""
    if ctx.task_runtime is None:
        return
    if ctx.task_runtime.state == "blocked":
        return
    mutation = _new_task_mutation(
        ctx.task_runtime,
        actor_kind="system",
        mutation_type="task.block",
        patch={"reason": reason},
    )
    ctx.task_runtime, _accepted, _reason = await _evaluate_and_apply_task_mutation(
        deps=ctx.deps,
        tree=ctx.tree,
        snapshot=ctx.snapshot,
        task_runtime=ctx.task_runtime,
        mutation=mutation,
        evidence_records=ctx.evidence_ledger,
    )


def record_review_outcome(ctx: RunContext, record: ReviewDecisionRecord) -> None:
    """审查遥测（只度量，不计数）。"""
    ctx.hotpath.observe(
        "turn.review.repair",
        1.0 if record.verdict == "repair" else 0.0,
        labels={"action": record.verdict},
    )
