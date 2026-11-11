from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ExecutorKind = Literal["local", "mcp_remote", "agent_spawn"]
VisibilityScope = Literal["all", "main_only", "subagent_only"]
ToolClass = Literal["read", "memory-write-safe", "write"]


class ToolExecutionTraits(BaseModel):
    concurrency_safe: bool = False
    supports_background: bool = False
    streaming_preferred: bool = False
    timeout_seconds: float | None = None
    requires_discovery: bool = False


class ToolPermissionTraits(BaseModel):
    tool_class: ToolClass = "write"
    visibility_scope: VisibilityScope = "all"
    requires_approval: bool = False
    approval_eligible: bool = True
    read_only_allowed: bool = False
    parent_only: bool = False
    forced_chat_only: bool = False
    # 工具只有在「存在能实时应答的人类用户」时才有意义（如 AskUserQuestion 要暂停等回答）。
    # 无头执行（CLI / 测试）与子代理都无法暂停问人，此类工具会被 HeadlessModeReducer 摘除。
    # 定时任务复用 Web 主链路、持有队列，不属无头执行。
    requires_interactive_user: bool = False
    # 工具仅在对应 Skill 激活时可见（由 SkillGatedReducer 按激活 Skill 的 allowed_tools 放行）。
    skill_gated: bool = False


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    execution_traits: ToolExecutionTraits = Field(default_factory=ToolExecutionTraits)
    permission_traits: ToolPermissionTraits = Field(default_factory=ToolPermissionTraits)
    executor_kind: ExecutorKind = "local"
    tags: tuple[str, ...] = ()
