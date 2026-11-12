from __future__ import annotations

import json
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from prismclaw.engine.state.turn_state import TurnState
from prismclaw.schema.agent.task_runtime import ReviewRequirements
from prismclaw.schema.agent.task_types import TaskSpec
from prismclaw.schema.agent.task_runtime import TaskRuntimeState

from prismclaw.engine.execution.checkpoint.models import (
    BatchRuntimeRecord,
    CommitState,
    CompressionRuntimeState,
    EffectClass,
    InvokedSkillState,
    PromptCacheRuntimeState,
    RequestAssemblySnapshot,
    ResumeBoundary,
    ResumeDisposition,
    RunState,
    SystemPromptSectionState,
    ToolEffectRecord,
)

CURRENT_RUN_SNAPSHOT_VERSION = 5


class ActiveRunContextSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str | None = None
    task_id: str | None = None
    task_spec: TaskSpec | None = None
    task_runtime: TaskRuntimeState | None = None
    review_requirements: ReviewRequirements | None = None
    decision_ledger_tail: list[str] = Field(default_factory=list)


class RunSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = CURRENT_RUN_SNAPSHOT_VERSION
    run_state: RunState
    turn_state: TurnState = Field(default_factory=TurnState)
    snapshot_id: str | None = None
    worker_id: str | None = None
    last_safe_boundary: ResumeBoundary = ResumeBoundary.BEFORE_MODEL_CALL
    resume_disposition: ResumeDisposition = ResumeDisposition.AUTO_RESUME
    latest_tool_effect: ToolEffectRecord | None = None
    latest_batch_runtime: BatchRuntimeRecord | None = None
    last_request_snapshot: RequestAssemblySnapshot | None = None
    prompt_cache_runtime: PromptCacheRuntimeState = Field(default_factory=PromptCacheRuntimeState)
    compression_runtime: CompressionRuntimeState = Field(default_factory=CompressionRuntimeState)
    system_prompt_sections: SystemPromptSectionState = Field(default_factory=SystemPromptSectionState)
    invoked_skill_state: InvokedSkillState = Field(default_factory=InvokedSkillState)
    active_context: ActiveRunContextSnapshot = Field(default_factory=ActiveRunContextSnapshot)
    snapshot_cursor: str = ""
    created_at_epoch_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    total_output_tokens: int = 0
    total_cache_creation_tokens: int = 0


class ResumeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disposition: ResumeDisposition
    reason_code: str
    human_readable_reason: str
    resume_from_boundary: ResumeBoundary | None = None


_RECOVERY_PROBE_RESUMABLE_REASON_CODES = frozenset(
    {
        "no_effect_record",
        "safe_boundary",
        "unknown_effect",
        "commit_unknown",
        "external_mutation",
        "local_mutation_in_progress",
    }
)


def is_recovery_probe_resumable(decision: ResumeDecision) -> bool:
    return decision.reason_code in _RECOVERY_PROBE_RESUMABLE_REASON_CODES


def recovery_probe_required(decision: ResumeDecision) -> bool:
    return (
        decision.disposition != ResumeDisposition.AUTO_RESUME
        and is_recovery_probe_resumable(decision)
    )


class OwnershipRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    session_id: str | None = None
    owner_worker_id: str
    snapshot_cursor: str
    last_safe_boundary: ResumeBoundary
    heartbeat_at_epoch_ms: int


RUN_SNAPSHOT_CUSTOM_TYPE = "run_snapshot"


def parse_run_snapshot(data: Any) -> RunSnapshot | None:
    if isinstance(data, RunSnapshot):
        return data
    if not isinstance(data, dict):
        return None
    try:
        return RunSnapshot.model_validate(data)
    except Exception:
        return None


def parse_run_snapshot_json(payload: str) -> RunSnapshot | None:
    try:
        data = json.loads(payload)
    except Exception:
        return None
    return parse_run_snapshot(data)


def parse_ownership_record(data: Any) -> OwnershipRecord | None:
    if isinstance(data, OwnershipRecord):
        return data
    if not isinstance(data, dict):
        return None
    try:
        return OwnershipRecord.model_validate(data)
    except Exception:
        return None


def collect_latest_run_snapshot(branch: list[Any]) -> RunSnapshot | None:
    for entry in reversed(branch):
        if getattr(entry, "type", None) != "custom":
            continue
        if getattr(entry, "custom_type", None) != RUN_SNAPSHOT_CUSTOM_TYPE:
            continue
        snapshot = parse_run_snapshot(getattr(entry, "data", None))
        if snapshot is not None:
            return snapshot
    return None


def build_empty_run_snapshot(
    session_id: str,
    *,
    run_id: str | None = None,
    workspace_id: str | None = None,
) -> RunSnapshot:
    return RunSnapshot(
        run_state=RunState(
            session_id=session_id,
            run_id=run_id,
            workspace_id=workspace_id,
        )
    )


def load_run_snapshot_payload(session_id: str, payload: Any) -> RunSnapshot:
    snapshot = parse_run_snapshot(payload)
    if snapshot is None:
        raise ValueError("invalid run snapshot payload")
    if snapshot.run_state.session_id != session_id:
        raise ValueError(
            f"run snapshot session mismatch: expected {session_id!r}, got {snapshot.run_state.session_id!r}"
        )
    return snapshot


def load_run_snapshot_from_branch(
    session_id: str,
    branch: list[Any],
) -> RunSnapshot:
    persisted = collect_latest_run_snapshot(branch)
    if persisted is not None:
        return persisted
    return build_empty_run_snapshot(session_id)


def evaluate_resume_decision(snapshot: RunSnapshot) -> ResumeDecision:
    batch = snapshot.latest_batch_runtime
    if snapshot.run_state.approval_pending:
        return ResumeDecision(
            disposition=ResumeDisposition.HUMAN_REVIEW,
            reason_code="approval_pending",
            human_readable_reason="A tool approval decision is still pending and requires human action before resuming.",
        )
    effect = snapshot.latest_tool_effect
    if effect is None:
        if batch is not None and (
            batch.status in {"running", "partial", "failed", "cancelled"}
            or batch.error_count > 0
            or batch.batch_kind == "subagent_parallel"
            or batch.effect_summary == "mixed"
        ):
            return ResumeDecision(
                disposition=ResumeDisposition.HUMAN_REVIEW,
                reason_code="batch_runtime_requires_review",
                human_readable_reason="Latest snapshot carries a batch runtime state that requires operator review before resuming.",
            )
        return ResumeDecision(
            disposition=ResumeDisposition.AUTO_RESUME,
            reason_code="no_effect_record",
            human_readable_reason="No tool-side effect record is attached to the latest snapshot.",
            resume_from_boundary=snapshot.last_safe_boundary,
        )
    if effect.effect_class == EffectClass.UNKNOWN_EFFECT:
        if batch is not None and batch.batch_kind == "subagent_parallel":
            return ResumeDecision(
                disposition=ResumeDisposition.HUMAN_REVIEW,
                reason_code="subagent_parallel_unknown_effect",
                human_readable_reason="Latest tool batch is a parallel sub-agent fan-out with unresolved effect semantics and requires operator review.",
            )
        if batch is not None and batch.effect_summary == "mixed":
            return ResumeDecision(
                disposition=ResumeDisposition.HUMAN_REVIEW,
                reason_code="mixed_batch_unknown_effect",
                human_readable_reason="Latest tool batch mixes multiple effect classes and cannot be resumed automatically.",
            )
        return ResumeDecision(
            disposition=ResumeDisposition.HUMAN_REVIEW,
            reason_code="unknown_effect",
            human_readable_reason="Latest tool effect is unknown and must not be replayed automatically.",
        )
    if effect.commit_state == CommitState.COMMIT_UNKNOWN:
        if batch is not None and batch.batch_kind == "subagent_parallel":
            return ResumeDecision(
                disposition=ResumeDisposition.HUMAN_REVIEW,
                reason_code="subagent_parallel_commit_unknown",
                human_readable_reason="Latest parallel sub-agent batch has an unknown commit state and requires operator review.",
            )
        return ResumeDecision(
            disposition=ResumeDisposition.HUMAN_REVIEW,
            reason_code="commit_unknown",
            human_readable_reason="Latest tool commit state is unknown and requires operator review.",
        )
    if effect.effect_class == EffectClass.EXTERNAL_MUTATION:
        return ResumeDecision(
            disposition=ResumeDisposition.HUMAN_REVIEW,
            reason_code="external_mutation",
            human_readable_reason="External mutations require explicit review before resuming.",
        )
    if effect.effect_class == EffectClass.LOCAL_MUTATION and effect.commit_state == CommitState.IN_PROGRESS:
        return ResumeDecision(
            disposition=ResumeDisposition.RESUME_BLOCKED,
            reason_code="local_mutation_in_progress",
            human_readable_reason="Local mutation was in progress and cannot be replayed safely.",
        )
    return ResumeDecision(
        disposition=ResumeDisposition.AUTO_RESUME,
        reason_code="safe_boundary",
        human_readable_reason="Snapshot is on a safe resume boundary.",
        resume_from_boundary=snapshot.last_safe_boundary,
    )
