from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from prismclaw.schema.agent.data_types import ContentBlock as ContentBlock  # noqa: F401
from prismclaw.schema.agent.data_types import FileBlock as FileBlock  # noqa: F401
from prismclaw.schema.agent.data_types import ImageBlock as ImageBlock  # noqa: F401
from prismclaw.schema.agent.data_types import TextBlock as TextBlock  # noqa: F401
from prismclaw.schema.content.subagent import SubagentResultEnvelope as SubagentResultEnvelope  # noqa: F401


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class FileChangeEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    path: str
    name: str
    kind: str
    insertions: int = 0
    deletions: int = 0
    hunks: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False
    before_ref: str | None = None
    before_sha: str | None = None
    note: str | None = None


class UiArtifact(BaseModel):
    artifact_type: str
    file_changes: list[FileChangeEntry] = Field(default_factory=list)
    result_compaction_preview: dict[str, Any] | None = None


class HandoffRequestPayload(BaseModel):
    target: str
    task: str
    context_hint: str | None = None
    source_subagent: str
    source_sub_session_id: str
    source_chain_id: str
    source_hop_index: int
    source_effective_hop_index: int
    tool_call_id: str | None = None
    source_skill_origin: str | None = None
    source_preload_skills: list[str] = Field(default_factory=list)
    source_is_fork: bool = False


class PersistenceArtifact(BaseModel):
    artifact_type: str
    entry_type: str
    handoff_request: HandoffRequestPayload | None = None


class StateDeltaArtifact(BaseModel):
    delta_type: str
    tool_discoveries: list[dict[str, Any]] = Field(default_factory=list)


class ToolSkillInvocation(BaseModel):
    skill_name: str
    task_context: str
    args: dict[str, Any] = Field(default_factory=dict)
    source: Literal["model", "user", "system"] = "model"


class ToolSkillExecutionStrategy(BaseModel):
    skill_name: str
    execution_mode: Literal["inline", "fork", "subagent"] = "inline"
    allowed_tools: list[str] | None = None
    preload_skills: list[str] = Field(default_factory=list)
    subagent_manifest: dict[str, Any] | None = None
    append_messages: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""


class ToolContextModifier(BaseModel):
    active_skill_name: str
    allowed_tools_override: list[str] | None = None
    append_messages: list[dict[str, Any]] = Field(default_factory=list)
    clear_after_terminal_result: bool = True
    # 区分调用来源: "skill"(标准技能激活) 或 "command"(命令 dispatch)
    invocation_context: Literal["skill", "command"] = "skill"


class RuntimeControlArtifact(BaseModel):
    skill_name: str | None = None
    skill_summary: str | None = None
    skill_invocation: ToolSkillInvocation | None = None
    skill_execution_strategy: ToolSkillExecutionStrategy | None = None
    context_modifier: ToolContextModifier | None = None


class SubagentEnvelopeArtifact(BaseModel):
    execution_mode: str | None = None
    context_mode: str | None = None
    fork_status: str | None = None
    agent_name: str | None = None
    agent_source: str | None = None
    outcome: str | None = None
    turns: int | None = None
    tokens_input: int | None = None
    tokens_output: int | None = None
    duration_seconds: float | None = None
    model_used: str | None = None
    skill_origin: str | None = None
    preload_skills: list[str] = Field(default_factory=list)
    task_id: str | None = None
    final_text: str | None = None
    preservation_summary: dict[str, Any] | None = None
    result_envelope: SubagentResultEnvelope | None = None
    handoff_request: HandoffRequestPayload | None = None


class ReferenceArtifact(BaseModel):
    artifact_type: str
    search_evidence: dict[str, Any] | None = None
    extract_result_count: int | None = None
    extract_source: str | None = None
    extract_cached: bool | None = None


class ToolResult(BaseModel):
    tool_call_id: str
    content: list[ContentBlock]
    is_error: bool = False
    # 失败归因（终止架构重构，2026-10-24）：区分"外部数据源/网络
    # 失败"（external）与"模型自身错误"（internal，如参数错、工具
    # 用错、本地执行异常）。None = 未打标，record 层按 internal 保守
    # 处理（保持旧行为，无回归）。消费方：call_handling 的签名窗口
    # 与连错计数按归因分流。
    error_origin: str | None = None
    ui_artifact: UiArtifact | None = None
    persistence_artifact: PersistenceArtifact | None = None
    state_delta: StateDeltaArtifact | None = None
    runtime_control: RuntimeControlArtifact | None = None
    subagent_envelope: SubagentEnvelopeArtifact | None = None
    reference_artifact: ReferenceArtifact | None = None
    metadata: dict[str, Any] | None = None
