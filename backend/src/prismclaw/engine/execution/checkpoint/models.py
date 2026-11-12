from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from prismclaw.schema.services.skills import SkillInvocationState
# 由 checkpoint/__init__ 再导出；ruff 视为未使用，不能删。
from prismclaw.schema.session.cache_types import (
    CompressionRuntimeState,
    PromptCacheRuntimeState,
)


class ResumeBoundary(str, Enum):
    BEFORE_MODEL_CALL = "before_model_call"
    BEFORE_TOOL_DISPATCH = "before_tool_dispatch"
    AFTER_TOOL_RESULT_PERSIST = "after_tool_result_persist"
    AFTER_TURN_FINALIZE = "after_turn_finalize"


# EffectClass 已迁移到 schema 层，此处 re-export 保持兼容
from prismclaw.schema.agent.capability import EffectClass  # noqa: F811,E402


class CommitState(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMMITTED = "committed"
    COMMIT_UNKNOWN = "commit_unknown"
    ROLLED_BACK = "rolled_back"


class ResumeDisposition(str, Enum):
    AUTO_RESUME = "auto_resume"
    RESUME_BLOCKED = "resume_blocked"
    HUMAN_REVIEW = "human_review"


class RetryState(BaseModel):
    reasoning: int = 0
    empty: int = 0


class RecoveryState(BaseModel):
    chain: list[str] = Field(default_factory=list)


class LoopState(BaseModel):
    steps_spent: int = 0
    tool_calls_spent: int = 0
    soft_at: float = 0.0
    hard_at: float = 0.0


class CompressionState(BaseModel):
    replacement_ledger_id: str | None = None
    micro_prune_count: int = 0
    last_projection_hash: str | None = None


class RequestAssemblySnapshot(BaseModel):
    request_id: str = ""
    query_loop_revision_id: str = ""
    model: str = ""
    request_mode: str | None = None
    messages_for_api: list[dict[str, Any]] = Field(default_factory=list)
    system: str | list[dict[str, Any]] | None = None
    context_payload: dict[str, Any] | None = None
    tool_schemas_fingerprint: str | None = None
    cache_strategy: dict[str, Any] = Field(default_factory=dict)


class SystemPromptSectionRecord(BaseModel):
    name: str
    block_id: str | None = None
    zone: Literal["static", "dynamic"] = "dynamic"
    stability: str | None = None
    priority: str = "medium"
    cacheable: bool = False
    truncatable: bool = True
    replaceable: bool = False
    estimated_tokens: int = 0
    content_digest: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class SystemPromptSectionState(BaseModel):
    lifecycle_phase: Literal["empty", "active", "durable"] = "empty"
    static_sections: list[SystemPromptSectionRecord] = Field(default_factory=list)
    dynamic_sections: list[SystemPromptSectionRecord] = Field(default_factory=list)
    cache_break_sections: list[str] = Field(default_factory=list)


class InvokedSkillRecord(BaseModel):
    skill_name: str
    skill_path: str = ""
    content_digest: str = ""
    rendered_prompt_fragment: str = ""
    agent_id: str | None = None
    invoked_at_epoch_ms: int | None = None
    execution_mode: str = "inline"
    allowed_tools: list[str] | None = None
    preload_skills: list[str] = Field(default_factory=list)
    clear_after_terminal_result: bool = True


class InvokedSkillState(BaseModel):
    active_invocation: SkillInvocationState | None = None
    skills: dict[str, InvokedSkillRecord] = Field(default_factory=dict)

    def retain_durable_records(self) -> "InvokedSkillState":
        retained_active_invocation = self.active_invocation
        if (
            retained_active_invocation is not None
            and retained_active_invocation.clear_after_terminal_result
        ):
            retained_active_invocation = None
        retained_skills = {
            skill_name: record.model_copy(deep=True)
            for skill_name, record in self.skills.items()
            if not record.clear_after_terminal_result
        }
        return InvokedSkillState(
            active_invocation=(
                retained_active_invocation.model_copy(deep=True)
                if retained_active_invocation is not None
                else None
            ),
            skills=retained_skills,
        )


class ToolEffectRecord(BaseModel):
    tool_call_id: str
    tool_name: str
    effect_class: EffectClass = EffectClass.UNKNOWN_EFFECT
    idempotency_key: str | None = None
    approval_id: str | None = None
    commit_state: CommitState = CommitState.NOT_STARTED
    result_persisted: bool = False
    started_at_epoch_ms: int | None = None
    finished_at_epoch_ms: int | None = None


class BatchRuntimeRecord(BaseModel):
    batch_id: str
    owner_session_id: str
    batch_kind: Literal["tool_batch", "subagent_parallel", "background_projection"]
    status: Literal["prepared", "running", "completed", "partial", "failed", "cancelled"]
    member_call_ids: list[str] = Field(default_factory=list)
    member_names: list[str] = Field(default_factory=list)
    started_at_epoch_ms: int | None = None
    completed_at_epoch_ms: int | None = None
    error_count: int = 0
    effect_summary: str = EffectClass.UNKNOWN_EFFECT.value
    parallel_task_ids: list[str] = Field(default_factory=list)


class RunState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    run_id: str | None = None
    workspace_id: str | None = None
    retry_state: RetryState = Field(default_factory=RetryState)
    tool_loop_state: dict[str, object] = Field(default_factory=dict)
    recovery_state: RecoveryState = Field(default_factory=RecoveryState)
    current_phase: str | None = None
    last_model_response_meta: dict[str, object] = Field(default_factory=dict)
    loop_state: LoopState = Field(default_factory=LoopState)
    compression_state: CompressionState = Field(default_factory=CompressionState)
    active_tool_call_id: str | None = None
    approval_pending: bool = False

