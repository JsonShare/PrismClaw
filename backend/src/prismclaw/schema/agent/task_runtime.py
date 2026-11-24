from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field

# 跨模块共享的语义类型（保留命名契约）。仅单字段使用的 Literal 直接内联在
# 字段上，不再提升为全局别名——避免本模块膨胀成无消费者的"类型池"。
ConfirmationDecision = Literal["approve", "deny"]
TaskComplexity = Literal["simple", "moderate", "complex"]


class TurnAssessment(BaseModel):
    """单轮任务评估结果（纯规则，不引入 LLM 调用）。

    合并了此前 TurnSemantics / TaskContextContract / ComplexityAssessment 三个
    对象中唯一被消费的信号，用于 ``TurnPlanner`` 决定是否跳过重管道。
    """

    level: TaskComplexity
    score: int
    signals: list[str] = Field(default_factory=list)


class ProbeSignal(BaseModel):
    code: str
    severity: Literal["info", "warning", "high"]
    summary: str


class PreflightProbeResult(BaseModel):
    probe_id: str
    workspace_root: str | None = None
    mentioned_paths: list[str] = Field(default_factory=list)
    existing_paths: list[str] = Field(default_factory=list)
    critical_workspace_paths: list[str] = Field(default_factory=list)
    baseline_risk: Literal["normal", "high"] = "normal"
    signals: list[ProbeSignal] = Field(default_factory=list)
    summary: str


class RecoveryProbeResult(BaseModel):
    probe_id: str
    workspace_root: str | None = None
    baseline_source: Literal["preflight_probe", "task_spec_text", "none"] = "none"
    status: Literal["ready", "degraded", "blocked"] = "ready"
    checked_paths: list[str] = Field(default_factory=list)
    missing_paths: list[str] = Field(default_factory=list)
    signals: list[ProbeSignal] = Field(default_factory=list)
    summary: str


class EvidenceRecord(BaseModel):
    """单流证据台账：一次工具调用的机械事实，或一条系统探针事实。"""

    evidence_id: str
    task_id: str
    tool_name: str
    status: Literal["ok", "error"]
    wrote_paths: list[str] = Field(default_factory=list)
    origin: Literal["main", "subagent", "system"] = "main"
    created_at_epoch_ms: int = Field(default_factory=lambda: int(time.time() * 1000))


class AcceptanceEvaluation(BaseModel):
    criterion: str
    status: Literal["met", "unmet", "unknown"]
    evidence_ids: list[str] = Field(default_factory=list)
    note: str | None = None


class ReviewDecisionRecord(BaseModel):
    decision_id: str
    task_id: str
    verdict: Literal["continue", "repair", "complete", "blocked"]
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
    state: Literal[
        "awaiting_confirmation",
        "awaiting_risk_confirmation",
        "executing",
        "completed",
        "blocked",
    ]
    entry_resolution_status: Literal["ready", "failed"] = "ready"
    state_version: int = 1
    latest_review_decision_id: str | None = None
    pending_confirmation_reason: str | None = None
    checkpoint_cursor: str | None = None
    last_mutation_id: str | None = None
    created_at_epoch_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    updated_at_epoch_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
