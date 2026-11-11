from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class ToolCallStatus(str, Enum):
    PENDING = "pending"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    DENIED = "denied"


class ApprovalDecision(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"
    EXPIRED = "expired"


class ToolCallRecord(BaseModel):
    id: str
    session_id: str
    turn_seq: int
    iteration_seq: int
    tool_name: str
    args_hash: str
    args_sanitized: str
    args_preview: str
    sandbox_tier: str
    confirmation_kind: str
    risk_assessment_json: str
    workspace_tag_flags: str
    consent_purposes: list[str] = Field(default_factory=list)
    skill_name: str | None = None
    plugin_name: str | None = None
    subagent_run_id: str | None = None
    status: ToolCallStatus = ToolCallStatus.PENDING
    exit_code: int | None = None
    elapsed_ms: int | None = None
    error_summary: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    tenant_id: str | None = None


class ApprovalRecord(BaseModel):
    id: str
    tool_call_id: str
    decision: ApprovalDecision
    confirm_level: str
    requester_id: str
    responder_id: str | None = None
    second_approver_id: str | None = None
    signed_payload: str
    verified: bool = False
    batch_size: int = 1
    batch_tool_call_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None
    tenant_id: str | None = None
