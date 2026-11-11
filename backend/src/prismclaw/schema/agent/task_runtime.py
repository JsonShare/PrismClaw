from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field

TaskRuntimeStateName = Literal[
    "intaking",
    "clarifying",
    "awaiting_confirmation",
    "awaiting_risk_confirmation",
    "executing",
    "reviewing",
    "resuming",
    "completed",
    "blocked",
    "cancelled",
]

EvidenceStatus = Literal["ok", "error"]
EvidenceOrigin = Literal["main", "subagent", "system"]
ReviewVerdict = Literal["continue", "repair", "complete", "blocked"]
AcceptanceStatus = Literal["met", "unmet", "unknown"]
ProbeSeverity = Literal["info", "warning", "high"]
ProbeRuntimeStatus = Literal["ready", "degraded", "blocked"]
ConfirmationDecision = Literal["approve", "deny"]
EntryResolutionStatus = Literal["pending", "ready", "failed"]
TurnRelation = Literal[
    "new",
    "continue",
]
AmbiguityLevel = Literal["low", "medium", "high"]
SemanticFieldOrigin = Literal["rules", "llm_proposed", "human_confirmed"]


class TurnSemantics(BaseModel):
    turn_index: int
    turn_relation: TurnRelation = "new"
    latest_user_delta_summary: str
    carry_forward_commitments: list[str] = Field(default_factory=list)
    superseded_constraints: list[str] = Field(default_factory=list)
    ambiguity_level: AmbiguityLevel = "low"
    ambiguity_reason_codes: list[str] = Field(default_factory=list)


class ProbeSignal(BaseModel):
    code: str
    severity: ProbeSeverity
    summary: str


class PreflightProbeResult(BaseModel):
    probe_id: str
    workspace_root: str | None = None
    mentioned_paths: list[str] = Field(default_factory=list)
    existing_paths: list[str] = Field(default_factory=list)
    recovery_targets: list[str] = Field(default_factory=list)
    touched_roots: list[str] = Field(default_factory=list)
    workspace_file_count: int = 0
    workspace_dir_count: int = 0
    code_file_count: int = 0
    git_dirty_paths: list[str] = Field(default_factory=list)
    critical_workspace_paths: list[str] = Field(default_factory=list)
    baseline_risk: Literal["normal", "high"] = "normal"
    signals: list[ProbeSignal] = Field(default_factory=list)
    summary: str


class RecoveryProbeResult(BaseModel):
    probe_id: str
    workspace_root: str | None = None
    baseline_source: Literal["preflight_probe", "task_spec_text", "none"] = "none"
    status: ProbeRuntimeStatus = "ready"
    checked_paths: list[str] = Field(default_factory=list)
    missing_paths: list[str] = Field(default_factory=list)
    signals: list[ProbeSignal] = Field(default_factory=list)
    summary: str


class TaskContextContract(BaseModel):
    task_id: str
    goal: str
    normalized_user_intent: str
    normalized_user_intent_source_refs: list[str] = Field(default_factory=list)
    normalized_user_intent_origin: SemanticFieldOrigin = "rules"
    constraints: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    missing_information_reason_codes: list[str] = Field(default_factory=list)
    attachments_present: bool = False
    preflight_probe: PreflightProbeResult | None = None


class ReviewRequirements(BaseModel):
    require_permission_review: bool = False
    require_human_review: bool = False
    require_fact_evidence: bool = False
    require_tool_verification: bool = False


class EvidenceRecord(BaseModel):
    """单流证据台账：一次工具调用的机械事实，或一条系统探针事实。"""

    evidence_id: str
    task_id: str
    tool_name: str
    status: EvidenceStatus
    wrote_paths: list[str] = Field(default_factory=list)
    origin: EvidenceOrigin = "main"
    created_at_epoch_ms: int = Field(default_factory=lambda: int(time.time() * 1000))


class AcceptanceEvaluation(BaseModel):
    criterion: str
    status: AcceptanceStatus
    evidence_ids: list[str] = Field(default_factory=list)
    note: str | None = None


class ReviewDecisionRecord(BaseModel):
    decision_id: str
    task_id: str
    verdict: ReviewVerdict
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    acceptance_evaluation: list[AcceptanceEvaluation] = Field(default_factory=list)
    created_at_epoch_ms: int = Field(default_factory=lambda: int(time.time() * 1000))


class TaskMutation(BaseModel):
    mutation_id: str
    task_id: str
    expected_state_version: int
    actor_kind: Literal["loop", "recovery", "review", "system"]
    mutation_type: str
    patch: dict[str, object] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    caused_by_event_id: str | None = None
    created_at_epoch_ms: int = Field(default_factory=lambda: int(time.time() * 1000))


class TaskRuntimeState(BaseModel):
    task_id: str
    session_id: str
    workspace_id: str | None = None
    state: TaskRuntimeStateName
    entry_resolution_status: EntryResolutionStatus = "ready"
    entry_failure_code: str | None = None
    state_version: int = 1
    latest_review_decision_id: str | None = None
    risk_level: str | None = None
    confirmation_required: bool = False
    pending_confirmation_reason: str | None = None
    checkpoint_cursor: str | None = None
    last_mutation_id: str | None = None
    created_at_epoch_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    updated_at_epoch_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
