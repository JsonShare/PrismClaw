from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from prismclaw.engine.hooks import HookRegistry
from prismclaw.schema.services.env_types import ExecutionEnv
from prismclaw.schema.agent.capability import EffectClass
from prismclaw.schema.content.workspace import SandboxTier, WorkspaceMode
from prismclaw.schema.agent.data_types import TextBlock
from prismclaw.schema.agent.task_runtime import ReviewRequirements, TaskRuntimeState
from prismclaw.schema.agent.task_types import TaskSpec
from prismclaw.schema.content.content_types import PersistenceArtifact, ReferenceArtifact, RuntimeControlArtifact, StateDeltaArtifact, SubagentEnvelopeArtifact, ToolResult, UiArtifact
from prismclaw.engine.security.sandbox.policy import SandboxPolicy

from prismclaw.engine.security.gate.types import ParallelTaskExtras, TaskRuntimeContext

ToolClass = Literal["read", "memory-write-safe", "write"]
"""Permission classification consumed by the runner under ``read-only`` tier.

- ``read``: pure read or session-isolated mutation (e.g. ``update_working_memory``)
- ``memory-write-safe``: writes to L2/L3 memory with the subsystem's own guards
  (e.g. ``memorize``); allowed in ``read-only`` per spec ``tool-approval-tiers``
- ``write``: file mutation, shell, destructive memory ops; auto-denied in
  ``read-only`` tier
"""

@dataclass(frozen=True)
class GoalBlockIntent:
    """任务阻塞申报意图（``declare_blocked`` 工具写入，运行循环消费）。

    工具只记意图、不改终态：是否受理由 ``CompletionKernel`` 结合完成
    契约裁决，受理后才经 ``mark_task_blocked`` 写入 blocked 终态（权力分立）。
    """

    code: str
    message: str


@dataclass
class ToolContext:
    workspace_id: str
    workspace_path: Path
    workspace_root: str = ""        # per-user 工作区根路径（BwrapPolicy 用于沙箱隔离）
    session_id: str = ""
    sandbox_policy: SandboxPolicy | None = None
    abort: asyncio.Event = field(default_factory=asyncio.Event)
    tenant_id: str | None = None
    user_id: str | None = None
    user_profile: Any = None
    subagent_depth: int = 0
    task_runtime: ParallelTaskExtras | None = None

    request_id: str = ""
    task_id: str = ""
    task_spec: TaskSpec | None = None
    task_runtime_state: TaskRuntimeState | None = None
    review_requirements: ReviewRequirements | None = None
    system_prompt_sections: Any = None
    invoked_skill_state: Any = None
    handoff_request_seen: bool = False
    pending_handoff: Any = None
    should_break_after_results: bool = False
    # 任务阻塞申报意图（declare_blocked 写入、下一次 no-tool 回合消费）。
    # 运行级信号：子 agent 有独立循环与独立裁决，不继承父级意图。
    goal_block_intent: GoalBlockIntent | None = None
    error_fatigue_tracker: dict[str, int] = field(default_factory=dict)
    pending_handoff_dispatch_extras: dict[str, Any] = field(default_factory=dict)
    progress_batch_id: str | None = None
    subagent_progress_emitter: Any = None
    hook_registry: HookRegistry | None = None
    hook_dispatcher: Any = None  # HookDispatcher（插件 hooks.json 声明式钩子；运行时引用，deepcopy 排除后由 sub_agent 按引用回填）
    capability_catalog: Any = None
    total_output_tokens: int = 0
    total_cache_creation_tokens: int = 0

    mode: WorkspaceMode = "work"
    current_sandbox_tier: SandboxTier = SandboxTier.STANDARD
    runtime: ExecutionEnv | None = None  # WorkspaceRuntimeClient | InProcessRuntimeClient（统一 ExecutionEnv 契约）
    workspace_tag: Any = None  # P1-SEC-04: will become WorkspaceTag
    task_manager: Any = None
    mcp_death_handler: Any = None
    mcp_runtime_manager: Any = None
    session_tree: Any = None
    parallel_task_store: Any = None
    approval_execution_audit_contexts: Any = None
    decision_audit_logger: Any = None
    scheduler_store: Any = None
    scheduler_engine: Any = None
    scheduler_dispatch: Any = None
    scheduler_redis_client: Any = None
    scheduler_max_tasks_per_user: int = 20
    scheduler_broadcast: Any = None  # async (user_id, event_type, data) -> None
    turn_state: Any = None
    active_skill_name: str | None = None
    invoked_skill_names: list[str] = field(default_factory=list)
    decision_ledger_tail: list[Any] = field(default_factory=list)
    headless: bool = False
    parent_session_id: str | None = None
    read_file_state: Any = None
    session_state: Any = None
    context_engine: Any = None
    subagent_catalog: Any = None
    subagent_manifest: Any = None
    subagent_name: str | None = None
    subagent_timed_out: bool = False
    session_store: Any = None
    subagent_progress_sink: Any = None
    channel: str = "web"

    # ── deepcopy 门控 ──
    # 被 sub_agent 工具 deepcopy 时，必须排除所有运行时引用：
    #   (1) 显式标为 runtime 的字段（下面 _DEEPCOPY_EXCLUDE）
    #   (2) 类型注解为 Any 且默认值为 None 的字段——这些是运行时注入的
    #       TaskManager/MCP stdio 客户端/分布式锁等对象，含 Future/Lock/Task，
    #       绝不能传给子 agent（子 agent 有独立运行时）
    _DEEPCOPY_EXCLUDE = frozenset({
        # 显式 asyncio / 线程对象
        "abort",
        # 显式 runtime
        "runtime",
        "hook_registry",
        "hook_dispatcher",
        "task_manager",
        "mcp_death_handler",
        "mcp_runtime_manager",
        "scheduler_engine",
        "scheduler_dispatch",
        "scheduler_store",
        "scheduler_redis_client",
        "scheduler_broadcast",
        "session_store",
        "context_engine",
        "session_state",
        "read_file_state",
        "subagent_catalog",
        "subagent_manifest",
        "goal_block_intent",
    })

    def __deepcopy__(self, memo):
        import copy as _copy
        from typing import get_type_hints

        # 先把 dataclass 自身排除（避免递归）
        if id(self) in memo:
            return memo[id(self)]

        # 收集所有要排除的字段名
        exclude = set(self._DEEPCOPY_EXCLUDE)
        try:
            hints = get_type_hints(self)
        except Exception:
            hints = {}
        import dataclasses as _dc
        for f in _dc.fields(self):
            if f.name in exclude:
                continue
            hint = hints.get(f.name)
            if hint is Any:
                # 类型注解为 Any 的字段——跳过（不 copy，保留默认 None）
                exclude.add(f.name)

        # 构造新实例：只 copy 非排除字段
        kwargs = {}
        for f in _dc.fields(self):
            if f.name in exclude:
                continue
            val = getattr(self, f.name)
            try:
                kwargs[f.name] = _copy.deepcopy(val, memo)
            except Exception:
                # 如果某个本应可 copy 的字段仍失败（e.g. 自定义对象），
                # 降级为直接引用（不 copy，但至少让 deepcopy 不崩）
                kwargs[f.name] = val

        # 确保必填字段都有值
        if "workspace_id" not in kwargs:
            kwargs["workspace_id"] = self.workspace_id
        if "workspace_path" not in kwargs:
            kwargs["workspace_path"] = self.workspace_path

        new_ctx = self.__class__(**kwargs)
        memo[id(self)] = new_ctx
        return new_ctx

    def __post_init__(self):
        if self.task_runtime is None:
            self.task_runtime = ParallelTaskExtras()

    @property
    def task_runtime_ctx(self) -> TaskRuntimeContext:
        # getattr 兜底：task_runtime 可能被错换为 TaskRuntimeState（schema
        # 持久化快照，无 .task_runtime 嵌套字段）。此时返回默认 TaskRuntimeContext。
        inner = getattr(self.task_runtime, "task_runtime", None)
        if isinstance(inner, TaskRuntimeContext):
            return inner
        return TaskRuntimeContext()


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]
    side_effect: bool
    tool_class: str
    effect_class: str

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult: ...


async def check_abort_or_run(
    tool: Tool,
    args: dict[str, Any],
    context: ToolContext,
) -> ToolResult:
    call_id = args.get("_call_id", "")
    if context.abort.is_set():
        return error_result(call_id, f"{tool.name}：执行前已中止")
    return await tool.execute(args, context)


def wrap_tool_with_abort(tool: Tool) -> Tool:
    wrapped_class: ToolClass = getattr(tool, "tool_class", "write")
    wrapped_effect_class = getattr(tool, "effect_class", resolve_tool_effect_class(tool).value)

    class _AbortWrapped:
        name = tool.name
        description = tool.description
        parameters = tool.parameters
        side_effect = tool.side_effect
        tool_class: str = wrapped_class
        effect_class: str = wrapped_effect_class
        timeout_seconds = getattr(tool, "timeout_seconds", None)
        max_output_chars = getattr(tool, "max_output_chars", None)

        async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
            return await check_abort_or_run(tool, args, context)

    return _AbortWrapped()  # type: ignore[return-value]


def resolve_tool_effect_class(tool: Tool) -> EffectClass:
    explicit = getattr(tool, "effect_class", None)
    if explicit is not None:
        try:
            return EffectClass(str(explicit))
        except ValueError:
            pass
    tool_class = getattr(tool, "tool_class", None)
    side_effect = bool(getattr(tool, "side_effect", False))
    if not side_effect and tool_class == "read":
        return EffectClass.PURE_READ
    if tool_class == "memory-write-safe":
        return EffectClass.LOCAL_MUTATION
    if side_effect and tool_class == "write":
        return EffectClass.LOCAL_MUTATION
    return EffectClass.UNKNOWN_EFFECT


def text_result(
    tool_call_id: str,
    text: str,
    *,
    metadata: dict[str, Any] | None = None,
    ui_artifact: UiArtifact | None = None,
    persistence_artifact: PersistenceArtifact | None = None,
    state_delta: StateDeltaArtifact | None = None,
    runtime_control: RuntimeControlArtifact | None = None,
    subagent_envelope: SubagentEnvelopeArtifact | None = None,
    reference_artifact: ReferenceArtifact | None = None,
) -> ToolResult:
    return ToolResult(
        tool_call_id=tool_call_id,
        content=[TextBlock(text=text)],
        is_error=False,
        ui_artifact=ui_artifact,
        persistence_artifact=persistence_artifact,
        state_delta=state_delta,
        runtime_control=runtime_control,
        subagent_envelope=subagent_envelope,
        reference_artifact=reference_artifact,
        metadata=metadata,
    )


def error_result(
    tool_call_id: str,
    text: str,
    *,
    error_origin: str = "internal",
    metadata: dict[str, Any] | None = None,
    ui_artifact: UiArtifact | None = None,
    persistence_artifact: PersistenceArtifact | None = None,
    state_delta: StateDeltaArtifact | None = None,
    runtime_control: RuntimeControlArtifact | None = None,
    subagent_envelope: SubagentEnvelopeArtifact | None = None,
    reference_artifact: ReferenceArtifact | None = None,
) -> ToolResult:
    # 默认 internal：内置工具错误（参数错/文件不存在/执行异常）是模型
    # 自身问题，终止判定按空转嫌疑处理。外部数据源失败的打标在
    # MCP adapter 与 subagent tool（它们不走本构造函数）。
    return ToolResult(
        tool_call_id=tool_call_id,
        content=[TextBlock(text=text)],
        is_error=True,
        error_origin=error_origin,
        ui_artifact=ui_artifact,
        persistence_artifact=persistence_artifact,
        state_delta=state_delta,
        runtime_control=runtime_control,
        subagent_envelope=subagent_envelope,
        reference_artifact=reference_artifact,
        metadata=metadata,
    )
