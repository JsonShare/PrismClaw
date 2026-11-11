from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from prismclaw.schema.agent.task_runtime import (
    ConfirmationDecision,
    ReviewRequirements,
    TaskContextContract,
    TurnSemantics,
)
WorkspaceMode = Literal["chat", "work"]
"""用户工作模式。
- ``chat`` — 只读模式，写类工具不可见
- ``work`` — 读写模式，全部工具可见
"""

from prismclaw.schema.services.env_types import ExecutionEnv
from prismclaw.schema.llm.types import LLMStreamFn
from prismclaw.schema.storage.storage_types import SessionStore

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from prismclaw.schema.agent.task_types import TaskSpec


# ── RunRequest (moved from core/agent/ownership.py) ────────────────

@dataclass
class RunRequest:
    """Agent 运行请求——从 ownership.py 提取到 contracts。

    delivery 层构造此对象，engine 层消费它。contracts 允许依赖 kernel。
    """

    session_id: str
    workspace_id: str
    agent_id: str
    user_message: str
    confirmation_decision: ConfirmationDecision | None = None
    sandbox_policy: Any = field(kw_only=True)
    model: str | None = None
    tool_context_extras: dict[str, Any] = field(default_factory=dict)
    attachments: list[Any] = field(default_factory=list)
    extra_system: str = ""
    mode_override: WorkspaceMode | None = None
    user_id: str | None = None
    workspace_tag: Any = None
    user_profile: Any = None
    request_id: str | None = None
    task_id: str | None = None
    task_spec: TaskSpec | None = None
    turn_semantics: TurnSemantics | None = None
    task_context_contract: TaskContextContract | None = None
    review_requirements: ReviewRequirements | None = None
    recovery_run_id: str | None = None
    recovery_owner_worker_id: str | None = None
    recovery_reason: str | None = None
    designated_skills: list[str] = field(default_factory=list)  # 用户指定技能（可多个，首轮前逐个激活）
    designated_plugins: list[str] = field(default_factory=list)  # 用户指定插件（可多个，逐个注入 profile + 偏向提示）
    # §4.10 动态层：任务级技能路由结果（top-3 候选，仅提示不激活）。
    # 仅 TaskSpec 新建回合（非确认回合）由 _apply_fresh_governance_fields 写入；
    # RunRequest 是 run 级生命周期，确认回合/延续轮不触发路由，天然无残留。
    skill_route_hints: tuple[str, ...] = ()
    # 本 run 所属的 delivery 层会话标识（web channel = WS conversation_id）。
    # 引擎用它从 session_queue 桥 drain steering（排队消息提升注入）；
    # 非 web channel（CLI / 测试）不填 → steering 消费为 no-op；定时任务走 web channel。
    conversation_id: str | None = None
    referenced_files: list[str] = field(default_factory=list)  # 用户显式引用的工作区文件（工作区根相对路径）
    # 落树/回放用的正文（原样，可含内联卡片标记）；user_message 是给模型的
    # 可读文本。None 表示两者相同（非 web 客户端不区分）。
    display_user_message: str | None = None
    trajectory_audit_header: str | None = None
    approval_reason: str | None = None
    mfa_credential_id: str | None = None
    second_approver_id: str | None = None


# ── AgentDeps Protocol ─────────────────────────────────────────────

class AgentDeps(Protocol):
    """Agent 运行时需要的依赖契约。

    AgentRunnerDeps 是此 Protocol 的具体实现。
    delivery 层通过 AgentDeps 引用，不依赖 AgentRunnerDeps 的具体字段。

    解耦原理：delivery 层不再直接引用 AgentRunnerDeps 的字段名
    （audit_logger / subagent_progress_sink / channel），
    而是通过 with_request_context() 的参数名注入请求级上下文。
    """

    @property
    def llm(self) -> LLMStreamFn: ...

    @property
    def runtime_client(self) -> ExecutionEnv | None: ...

    @property
    def session_store(self) -> SessionStore | None: ...

    @property
    def bus(self) -> Any: ...  # SessionBus，避免 contracts 依赖 bus 模块

    def with_request_context(
        self,
        *,
        audit_logger: Any = None,
        progress_sink: Any = None,
        channel: str | None = None,
    ) -> AgentDeps:
        """创建注入了请求级上下文的 deps 变体。

        delivery 层只调此方法，不知道 AgentRunnerDeps 的内部字段结构。
        具体的 dataclasses.replace 实现在 AgentRunnerDeps 中，delivery 看不到。
        """
        ...


__all__ = ["AgentDeps", "RunRequest"]
