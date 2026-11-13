from __future__ import annotations

import logging
import time
import uuid
from typing import TYPE_CHECKING, Any, Literal

from prismclaw.engine.execution.checkpoint import (
    BatchRuntimeRecord,
    CommitState,
    EffectClass,
    RunSnapshot,
    ResumeBoundary,
    ToolEffectRecord,
    evaluate_resume_decision,
)
from prismclaw.schema.agent.task_runtime import EvidenceRecord, ReviewDecisionRecord, ReviewRequirements, TaskMutation, TaskRuntimeState
from prismclaw.schema.agent.task_types import TaskSpec
from prismclaw.schema.content.compaction import DecisionLedgerEvent
from prismclaw.schema.content.content_types import ToolResult
from prismclaw.schema.session.types import CustomEntry, DECISION_LEDGER_EVENT_CUSTOM_TYPE, EVIDENCE_LEDGER_CUSTOM_TYPE, REVIEW_DECISION_CUSTOM_TYPE, REVIEW_REQUIREMENTS_CUSTOM_TYPE, SessionTree, TASK_MUTATION_CUSTOM_TYPE, TASK_RUNTIME_CUSTOM_TYPE, TASK_SPEC_CUSTOM_TYPE, generate_entry_id, now_iso
from prismclaw.schema.session.event_logger import SessionEventLogger

if TYPE_CHECKING:
    from prismclaw.engine.execution.loop.deps import AgentRunnerDeps
    from prismclaw.engine.execution.dispatch.execution import ResultCompactionTurn

from prismclaw.engine.execution.dispatch.turn_utils import (
    _batch_effect_summary,
    _batch_runtime_kind,
    _collect_batch_parallel_task_ids,
    _failed_batch_signature,
    _resolve_tool_batch_entries,
    _terminal_batch_status,
)

logger = logging.getLogger(__name__)


def _register_entry(tree: SessionTree, entry: CustomEntry) -> None:
    """把已提交的 entry 登记回内存 tree（对齐 store 投影语义：entries + leaf，不动 order）。

    没有这一步，同一次 run 内的连续写入各自拿着同一个过期的 tree.leaf_id，
    提交后内存 tree 也不含这些 entry：get_latest_task_runtime 等 branch 级
    读取失明、去重检查永远失效、下一条写入继续挂在旧 tip 上互为兄弟 fork。
    持久化侧的 parent 正确性由 RedisSessionStore.commit_events 的提交时
    重锚兜底；这里只负责内存侧读取一致性。
    """
    tree.entries[entry.id] = entry
    tree.leaf_id = entry.id

_event_logger = SessionEventLogger()


def _resolve_tool_effect_class(tool: Any) -> EffectClass:
    from prismclaw.engine.toolkit.registry.tool_runtime import resolve_tool_effect_class as _impl

    return _impl(tool)


def _build_task_terminal_mutation(
    task_runtime: TaskRuntimeState,
    *,
    actor_kind: Literal["loop", "review"],
    evidence_ids: list[str] | None = None,
) -> TaskMutation:
    return _new_task_mutation(
        task_runtime,
        actor_kind=actor_kind,
        mutation_type="task.complete",
        patch=_build_runtime_mutation_patch(task_runtime),
        evidence_ids=evidence_ids or [],
    )


async def _persist_task_runtime_snapshot(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    *,
    task_runtime: TaskRuntimeState,
) -> None:
    current = tree.get_latest_task_runtime()
    if current is not None and current.model_dump(mode="json") == task_runtime.model_dump(mode="json"):
        return
    entry = CustomEntry(
        id=generate_entry_id(set(tree.entries.keys())),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        custom_type=TASK_RUNTIME_CUSTOM_TYPE,
        data=task_runtime.model_dump(mode="json"),
    )
    event = _event_logger.entry_updated(tree.header.id, entry, leaf_id=entry.id)
    await deps.session_store.commit_events([event])
    _register_entry(tree, entry)


async def _persist_execution_evidence_batch(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    *,
    evidences: list[EvidenceRecord],
) -> None:
    """批量持久化多条执行证据，合并为单次 commit_events 调用。"""
    if not evidences:
        return
    events = []
    entries = []
    for evidence in evidences:
        entry = CustomEntry(
            id=generate_entry_id(set(tree.entries.keys())),
            parent_id=tree.leaf_id,
            timestamp=now_iso(),
            custom_type=EVIDENCE_LEDGER_CUSTOM_TYPE,
            data=evidence.model_dump(mode="json"),
        )
        events.append(_event_logger.entry_updated(tree.header.id, entry, leaf_id=entry.id))
        entries.append(entry)
    await deps.session_store.commit_events(events)
    for entry in entries:
        _register_entry(tree, entry)


async def _persist_task_spec_snapshot(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    *,
    task_spec: TaskSpec,
) -> None:
    current = tree.get_latest_task_spec()
    if current is not None and current.model_dump(mode="json") == task_spec.model_dump(mode="json"):
        return
    entry = CustomEntry(
        id=generate_entry_id(set(tree.entries.keys())),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        custom_type=TASK_SPEC_CUSTOM_TYPE,
        data=task_spec.model_dump(mode="json"),
    )
    event = _event_logger.entry_updated(tree.header.id, entry, leaf_id=entry.id)
    await deps.session_store.commit_events([event])
    _register_entry(tree, entry)


async def _persist_review_requirements_snapshot(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    *,
    review_requirements: ReviewRequirements,
) -> None:
    current = tree.get_latest_review_requirements()
    if current is not None and current.model_dump(mode="json") == review_requirements.model_dump(mode="json"):
        return
    entry = CustomEntry(
        id=generate_entry_id(set(tree.entries.keys())),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        custom_type=REVIEW_REQUIREMENTS_CUSTOM_TYPE,
        data=review_requirements.model_dump(mode="json"),
    )
    event = _event_logger.entry_updated(tree.header.id, entry, leaf_id=entry.id)
    await deps.session_store.commit_events([event])
    _register_entry(tree, entry)


async def _persist_execution_evidence(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    *,
    evidence: EvidenceRecord,
) -> None:
    entry = CustomEntry(
        id=generate_entry_id(set(tree.entries.keys())),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        custom_type=EVIDENCE_LEDGER_CUSTOM_TYPE,
        data=evidence.model_dump(mode="json"),
    )
    event = _event_logger.entry_updated(tree.header.id, entry, leaf_id=entry.id)
    await deps.session_store.commit_events([event])
    _register_entry(tree, entry)


async def _persist_review_decision_record(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    *,
    record: ReviewDecisionRecord,
) -> None:
    entry = CustomEntry(
        id=generate_entry_id(set(tree.entries.keys())),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        custom_type=REVIEW_DECISION_CUSTOM_TYPE,
        data=record.model_dump(mode="json"),
    )
    event = _event_logger.entry_updated(tree.header.id, entry, leaf_id=entry.id)
    await deps.session_store.commit_events([event])
    _register_entry(tree, entry)


async def _persist_task_mutation(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    *,
    mutation: TaskMutation,
) -> None:
    entry = CustomEntry(
        id=generate_entry_id(set(tree.entries.keys())),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        custom_type=TASK_MUTATION_CUSTOM_TYPE,
        data=mutation.model_dump(mode="json"),
    )
    event = _event_logger.entry_updated(tree.header.id, entry, leaf_id=entry.id)
    await deps.session_store.commit_events([event])
    _register_entry(tree, entry)


async def _persist_decision_ledger_event(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    *,
    event: DecisionLedgerEvent,
) -> None:
    entry = CustomEntry(
        id=generate_entry_id(set(tree.entries.keys())),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        custom_type=DECISION_LEDGER_EVENT_CUSTOM_TYPE,
        data=event.model_dump(mode="json"),
    )
    session_event = _event_logger.entry_updated(tree.header.id, entry, leaf_id=entry.id)
    await deps.session_store.commit_events([session_event])
    _register_entry(tree, entry)


def _append_decision_tail(snapshot: RunSnapshot, event_id: str) -> None:
    decision_tail = [
        item for item in snapshot.active_context.decision_ledger_tail if isinstance(item, str)
    ]
    decision_tail.append(event_id)
    snapshot.active_context.decision_ledger_tail = decision_tail[-8:]


def _new_task_mutation(
    task_runtime: TaskRuntimeState,
    *,
    actor_kind: Literal["loop", "recovery", "review", "system"],
    mutation_type: str,
    patch: dict[str, object] | None = None,
    evidence_ids: list[str] | None = None,
    caused_by_event_id: str | None = None,
) -> TaskMutation:
    return TaskMutation(
        mutation_id=f"{task_runtime.task_id}:mut:{uuid.uuid4().hex[:12]}",
        task_id=task_runtime.task_id,
        expected_state_version=task_runtime.state_version,
        actor_kind=actor_kind,
        mutation_type=mutation_type,
        patch=dict(patch or {}),
        evidence_ids=list(evidence_ids or []),
        caused_by_event_id=caused_by_event_id,
    )


def _build_runtime_mutation_patch(
    task_runtime: TaskRuntimeState,
    *,
    extras: dict[str, object] | None = None,
) -> dict[str, object]:
    patch: dict[str, object] = {}
    if extras:
        for key, value in extras.items():
            if value is not None:
                patch[key] = value
    return patch


def _apply_task_mutation_to_runtime(
    *,
    task_runtime: TaskRuntimeState,
    mutation: TaskMutation,
    review_record: ReviewDecisionRecord | None = None,
) -> TaskRuntimeState:
    updated_runtime = task_runtime.model_copy(deep=True)

    if mutation.mutation_type == "task.start_execution":
        updated_runtime.state = "executing"
    elif mutation.mutation_type == "task.complete":
        updated_runtime.state = "completed"
    elif mutation.mutation_type == "task.block":
        updated_runtime.state = "blocked"
        updated_runtime.confirmation_required = False
        reason = mutation.patch.get("reason")
        updated_runtime.pending_confirmation_reason = str(reason) if reason is not None else None
    elif mutation.mutation_type == "task.await_risk_confirmation":
        updated_runtime.state = "awaiting_risk_confirmation"
        updated_runtime.confirmation_required = True
        reason = mutation.patch.get("reason")
        updated_runtime.pending_confirmation_reason = str(reason) if reason is not None else None
    elif mutation.mutation_type == "task.risk_confirmed":
        updated_runtime.state = "executing"
        updated_runtime.confirmation_required = False
        updated_runtime.pending_confirmation_reason = None
    elif mutation.mutation_type == "task.resume_after_confirmation":
        updated_runtime.state = "executing"
        updated_runtime.confirmation_required = False
        updated_runtime.pending_confirmation_reason = None
    elif mutation.mutation_type == "task.review.record" and review_record is not None:
        updated_runtime.state = "reviewing"
        updated_runtime.confirmation_required = False
        updated_runtime.latest_review_decision_id = review_record.decision_id
        updated_runtime.checkpoint_cursor = review_record.decision_id
    elif mutation.mutation_type == "task.resume_probe_completed":
        resume_state = mutation.patch.get("resume_state")
        if resume_state == "awaiting_confirmation":
            updated_runtime.state = "awaiting_confirmation"
            updated_runtime.confirmation_required = True
            reason = mutation.patch.get("reason")
            updated_runtime.pending_confirmation_reason = str(reason) if reason is not None else None
        else:
            updated_runtime.state = "executing"
            updated_runtime.confirmation_required = False
            updated_runtime.pending_confirmation_reason = None

    updated_runtime.state_version += 1
    updated_runtime.last_mutation_id = mutation.mutation_id
    updated_runtime.updated_at_epoch_ms = mutation.created_at_epoch_ms
    return updated_runtime


async def _evaluate_and_apply_task_mutation(
    *,
    deps: AgentRunnerDeps,
    tree: SessionTree,
    snapshot: RunSnapshot,
    task_runtime: TaskRuntimeState,
    mutation: TaskMutation,
    evidence_records: list[EvidenceRecord],
    review_record: ReviewDecisionRecord | None = None,
) -> tuple[TaskRuntimeState, bool, str]:
    from prismclaw.engine.execution.loop.arbiter import evaluate_task_mutation

    decision = evaluate_task_mutation(
        mutation,
        task_runtime=task_runtime,
        evidence_records=evidence_records,
        review_decision=review_record,
    )
    if not decision.accepted:
        return task_runtime, False, decision.reason_code

    next_runtime = _apply_task_mutation_to_runtime(
        task_runtime=task_runtime,
        mutation=mutation,
        review_record=review_record,
    )
    await _persist_task_mutation(deps, tree, mutation=mutation)
    if review_record is not None:
        await _persist_review_decision_record(deps, tree, record=review_record)
    await _persist_task_runtime_snapshot(deps, tree, task_runtime=next_runtime)
    snapshot.active_context = snapshot.active_context.model_copy(
        update={
            "task_runtime": next_runtime,
        }
    )
    return next_runtime, True, decision.reason_code


def _set_resume_boundary(snapshot: RunSnapshot, *, boundary: ResumeBoundary) -> None:
    snapshot.last_safe_boundary = boundary
    snapshot.resume_disposition = evaluate_resume_decision(snapshot).disposition


def _set_approval_pending(snapshot: RunSnapshot, *, pending: bool) -> None:
    snapshot.run_state.approval_pending = pending
    snapshot.resume_disposition = evaluate_resume_decision(snapshot).disposition


def _record_cache_strategy_projection(snapshot: RunSnapshot, cache_strategy: Any) -> None:
    if not isinstance(cache_strategy, dict):
        return
    compression_state = snapshot.run_state.compression_state
    replacement_digest = cache_strategy.get("replacement_state_digest")
    if isinstance(replacement_digest, str) and replacement_digest:
        compression_state.replacement_ledger_id = replacement_digest
    projection_hash = cache_strategy.get("static_digest")
    if isinstance(projection_hash, str) and projection_hash:
        compression_state.last_projection_hash = projection_hash


def _record_result_compaction_state(
    snapshot: RunSnapshot,
    *,
    compaction_turn: ResultCompactionTurn | None,
) -> None:
    if compaction_turn is None:
        return
    compression_state = snapshot.run_state.compression_state
    replacement_state = compaction_turn.replacement_state
    replacement_digest = replacement_state.state_digest()
    if isinstance(replacement_digest, str) and replacement_digest:
        compression_state.replacement_ledger_id = replacement_digest
        compression_state.last_projection_hash = replacement_digest
    compression_state.micro_prune_count += replacement_state.decision_count()


def _new_batch_runtime_id() -> str:
    return f"batch:{uuid.uuid4().hex[:12]}"


def _record_batch_runtime(
    snapshot: RunSnapshot,
    *,
    deps: AgentRunnerDeps,
    session_id: str,
    tool_calls: list[dict[str, Any]],
    results: list[ToolResult],
    status: Literal["running", "completed", "partial", "failed", "cancelled"],
) -> None:
    resolved = _resolve_tool_batch_entries(deps.tools, tool_calls)
    tool_call_ids = [call_id for call_id, _tool_name, _effect_class in resolved]
    tool_names = [tool_name for _call_id, tool_name, _effect_class in resolved]
    effect_classes = [effect_class for _call_id, _tool_name, effect_class in resolved]
    if not tool_call_ids:
        snapshot.latest_batch_runtime = None
        return
    now_ms = int(time.time() * 1000)
    current = snapshot.latest_batch_runtime
    reuse_current = (
        current is not None
        and current.member_call_ids == tool_call_ids
        and current.status in {"prepared", "running"}
    )
    error_count = sum(1 for result in results if result.is_error)
    if status == "failed" and not results:
        error_count = max(1, len(tool_call_ids))
    parallel_task_ids = _collect_batch_parallel_task_ids(results)
    if reuse_current and not parallel_task_ids:
        parallel_task_ids = list(current.parallel_task_ids)
    batch_runtime = BatchRuntimeRecord(
        batch_id=current.batch_id if reuse_current else _new_batch_runtime_id(),
        owner_session_id=session_id,
        batch_kind=_batch_runtime_kind(tool_names),
        status=status,
        member_call_ids=tool_call_ids,
        member_names=tool_names,
        started_at_epoch_ms=(
            current.started_at_epoch_ms if reuse_current else now_ms
        ),
        completed_at_epoch_ms=None if status == "running" else now_ms,
        error_count=error_count,
        effect_summary=_batch_effect_summary(effect_classes),
        parallel_task_ids=parallel_task_ids,
    )
    snapshot.latest_batch_runtime = batch_runtime
    snapshot.resume_disposition = evaluate_resume_decision(snapshot).disposition


def _record_failed_tool_effect(
    snapshot: RunSnapshot,
    *,
    deps: AgentRunnerDeps,
    session_id: str,
    tool_calls: list[dict[str, Any]],
    results: list[ToolResult] | None = None,
) -> None:
    effective_results = results or []
    _record_batch_runtime(
        snapshot,
        deps=deps,
        session_id=session_id,
        tool_calls=tool_calls,
        results=effective_results,
        status=(
            _terminal_batch_status(
                tool_call_count=len(tool_calls),
                results=effective_results,
            )
            if effective_results
            else "failed"
        ),
    )
    active_call_id = snapshot.run_state.active_tool_call_id
    signature = _failed_batch_signature(
        tools=deps.tools,
        tool_calls=tool_calls,
        active_call_id=active_call_id,
    )
    if signature is None:
        snapshot.run_state.active_tool_call_id = None
        snapshot.resume_disposition = evaluate_resume_decision(snapshot).disposition
        return
    tool_call_id, tool_name, effect_class = signature
    snapshot.latest_tool_effect = ToolEffectRecord(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        effect_class=effect_class,
        idempotency_key=tool_call_id,
        commit_state=CommitState.COMMIT_UNKNOWN,
        result_persisted=False,
    )
    snapshot.run_state.active_tool_call_id = None
    snapshot.resume_disposition = evaluate_resume_decision(snapshot).disposition


def _record_latest_tool_effect(
    snapshot: RunSnapshot,
    *,
    deps: AgentRunnerDeps,
    tool_calls: list[dict[str, Any]],
    results: list[ToolResult],
) -> None:
    if not tool_calls or not results:
        return
    _record_batch_runtime(
        snapshot,
        deps=deps,
        session_id=snapshot.run_state.session_id,
        tool_calls=tool_calls,
        results=results,
        status=_terminal_batch_status(
            tool_call_count=len(tool_calls),
            results=results,
        ),
    )
    latest_call = tool_calls[-1]
    latest_result = results[-1]
    function_payload = (latest_call or {}).get("function") or {}
    tool_name = function_payload.get("name")
    if not isinstance(tool_name, str) or not tool_name:
        return
    tool = deps.tools.get(tool_name)
    effect_class: EffectClass = (
        _resolve_tool_effect_class(tool) if tool is not None else EffectClass.UNKNOWN_EFFECT
    )
    commit_state = (
        CommitState.COMMITTED
        if effect_class in {EffectClass.PURE_READ, EffectClass.LOCAL_MUTATION}
        else CommitState.COMMIT_UNKNOWN
    )
    snapshot.latest_tool_effect = ToolEffectRecord(
        tool_call_id=latest_result.tool_call_id or latest_call.get("id", ""),
        tool_name=tool_name,
        effect_class=effect_class,
        idempotency_key=latest_result.tool_call_id or latest_call.get("id", ""),
        commit_state=commit_state,
        result_persisted=True,
    )
    snapshot.run_state.active_tool_call_id = None