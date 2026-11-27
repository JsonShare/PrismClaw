from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from prismclaw.engine.compression.orchestrator import CompressionOrchestrator
    from prismclaw.engine.compression.ports import HookInvocationResult
    from prismclaw.engine.execution.capabilities.catalog import RuntimeCatalog
    from prismclaw.engine.execution.checkpoint import RunSnapshot
    from prismclaw.engine.execution.output.output_compressor import CompressionStats
    from prismclaw.engine.execution.query._static_context_types import StaticTurnShellCache
    from prismclaw.engine.execution.skills.runtime import SkillRuntime
    from prismclaw.engine.experts.registry.expert_registry import ExpertRegistry
    from prismclaw.engine.experts.scope import Scope
    from prismclaw.engine.hooks import (
        CompactionContext,
        HookRegistry,
        SkillProvider,
    )
    from prismclaw.engine.security.gate.governance import ContextValidatorChain
    from prismclaw.engine.subagent.runtime.collaboration import (
        SubagentCollaborationService,
    )
    from prismclaw.engine.subagent.runtime.parallel import ParallelTaskRuntimeRecord
    from prismclaw.engine.task_profile.interfaces import (
        TaskRequestPolicyProvider,
        TaskRuntimePolicyProvider,
    )
    from prismclaw.engine.toolkit.registry.governance import ToolGovernance
    from prismclaw.engine.toolkit.registry.registry import ToolRegistry
    from prismclaw.infra.config.capability_policy import CapabilityPolicyRegistry
    from prismclaw.infra.environment_catalog import EnvironmentCatalog
    from prismclaw.schema.agent.context import (
        BootstrapContextProvider,
        ContextAssembler,
        ContextCompactor,
        ContextEngine,
        ContextIngestor,
        ContextTurnFinalizer,
        HasCompactionSettings,
        MemoryContextProvider,
    )
    from prismclaw.schema.agent.data_types import ContentBlock
    from prismclaw.schema.agent.session import SessionHeader
    from prismclaw.schema.content.compaction import CompactResult
    from prismclaw.schema.content.content_types import ToolResult
    from prismclaw.schema.content.streaming import SubagentProgressBatch
    from prismclaw.schema.session.types import SessionTree

from prismclaw.engine.hooks import HookDispatcher
from prismclaw.engine.subagent.runtime.parallel import SessionParallelTaskStore
from prismclaw.engine.task_profile.service import TaskProfileService
from prismclaw.schema.agent.bundles import (
    DreamAdminOpsProtocol,
    DreamBoundaryProtocol,
    DreamReviewServiceProtocol,
    ExpertLifecycleProtocol,
    ExtensionManagerProtocol,
    MCPManagerProtocol,
    RunOwnershipStoreProtocol,
)
from prismclaw.schema.agent.callbacks import AgentCallbacks
from prismclaw.schema.agent.types import AgentDeps
from prismclaw.schema.config.config import AgentRunConfig
from prismclaw.schema.llm.types import LLMClient
from prismclaw.schema.services.env_types import ExecutionEnv
from prismclaw.schema.services.lock import LockManagerProtocol
from prismclaw.schema.services.session_queue import SessionQueueProtocol
from prismclaw.schema.services.task_protocol import TaskManagerProtocol
from prismclaw.schema.storage.persistence_types import PersistenceContextProtocol, RunSnapshotStore
from prismclaw.schema.storage.storage_types import SessionStore, WorkspaceStore
from prismclaw.schema.trajectory.trajectory import TrajectoryPersistence


def _lazy_ctx_engine() -> ContextEngine:
    from prismclaw.engine.compression.context_engine import DefaultContextEngine

    return DefaultContextEngine()


def _lazy_hook_registry() -> HookRegistry:
    from prismclaw.engine.hooks import HookRegistry

    return HookRegistry()


def _lazy_static_shell() -> StaticTurnShellCache:
    from prismclaw.engine.execution.query._static_context_types import StaticTurnShellCache

    return StaticTurnShellCache()


def _lazy_compressor() -> CompressionOrchestrator:
    from prismclaw.engine.compression.orchestrator import CompressionOrchestrator
    from prismclaw.engine.execution.query.turn_preparation import (
        prepare_query_turn_from_static_context,
    )

    return CompressionOrchestrator(prepare_turn_callable=prepare_query_turn_from_static_context)


class ExecutionRuntimePorts:
    """实现 ContextRuntimePorts 协议——execution 层注入到 compression 层。

    compression 层通过 ports 协议访问 execution/hooks/security/task_profile
    能力，避免反向 import。此处在 execution 层实现并注入到
    CompressionOrchestrator，是唯一的反向依赖边界。
    依赖方向：schema ← state ← compression.ports ← compression.* ← execution（此处）。

    hook_dispatcher/task_profile_service 在构造时持有引用；
    其余方法通过 lazy import 调用 execution.* 真实实现。
    """

    def __init__(
        self,
        *,
        hook_dispatcher: HookDispatcher | None = None,
        task_profile_service: TaskProfileService | None = None,
        hook_registry: HookRegistry | None = None,
    ) -> None:
        self._hook_dispatcher = hook_dispatcher
        self._task_profile_service = task_profile_service
        # 进程内 AgentHook registry（engine.hooks.HookRegistry），
        # 与专家包 hook_dispatcher 是两条独立 hook 通道。
        self._hook_registry = hook_registry

    # ── execution.request.model_fallback ──
    def get_effective_context_window(
        self,
        model_id: str | None,
        *,
        max_output_tokens_for_summary: int,
        fallback: int,
    ) -> int:
        from prismclaw.engine.execution.request.model_fallback import (
            get_effective_context_window as _get_effective_context_window,
        )

        return _get_effective_context_window(
            model_id or "",
            max_output_tokens_for_summary=max_output_tokens_for_summary,
            fallback=fallback,
        )

    # ── execution.staging.tool_result_persistence ──
    def is_persisted_content(self, content: object) -> bool:
        from prismclaw.engine.execution.staging.tool_result_persistence import (
            is_persisted_content as _is_persisted_content,
        )

        return _is_persisted_content(content)

    def persist_tool_result_sync(
        self,
        *,
        tool_use_id: str,
        content: list[ContentBlock],
        reason: str,
        working_dir: str | Path | None,
        tool_name: str,
    ) -> str | None:
        from prismclaw.engine.execution.staging.tool_result_persistence import (
            persist_tool_result_sync as _persist_tool_result_sync,
        )

        return _persist_tool_result_sync(
            tool_use_id=tool_use_id,
            content=content,
            reason=reason,
            working_dir=working_dir,
            tool_name=tool_name,
        )

    # ── execution.staging.context_block_persistence ──
    def persist_context_block(
        self,
        *,
        block_id: str,
        source: str,
        content: str,
        reason: str,
        working_dir: str | Path | None = None,
    ) -> str | None:
        from prismclaw.engine.execution.staging.context_block_persistence import (
            persist_context_block as _persist_context_block,
        )

        return _persist_context_block(
            block_id=block_id,
            source=source,
            content=content,
            reason=reason,
            working_dir=working_dir,
        )

    # ── execution.output.output_compressor ──
    def compress_tool_result(
        self,
        content: ToolResult,
        tool_name: str | None = None,
        tool_args: dict[str, Any] | str | None = None,
    ) -> tuple[ToolResult, CompressionStats]:
        from prismclaw.engine.execution.output.output_compressor import (
            compress_tool_result as _compress_tool_result,
        )

        return _compress_tool_result(content, tool_name or "", tool_args=tool_args)

    # ── hooks (HookEvent + CompactionContext + HookOutcome) ──
    def build_compaction_context(
        self,
        *,
        session_id: str,
        workspace_id: str,
        agent_id: str,
        message_count: int,
        tokens_before: int,
    ) -> CompactionContext:
        from prismclaw.engine.hooks import CompactionContext

        return CompactionContext(
            session_id=session_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            message_count=message_count,
            tokens_before=tokens_before,
        )

    async def notify_before_compaction(self, ctx: CompactionContext) -> None:
        if self._hook_registry is None:
            return
        await self._hook_registry.notify_before_compaction(ctx)

    async def notify_after_compaction(self, ctx: CompactionContext, result: CompactResult) -> None:
        if self._hook_registry is None:
            return
        await self._hook_registry.notify_after_compaction(ctx, result)

    async def invoke_compaction_hooks(
        self,
        *,
        event: str,
        ctx: CompactionContext,
        payload: dict[str, Any] | None = None,
    ) -> HookInvocationResult | None:
        if self._hook_dispatcher is None:
            return None
        from prismclaw.engine.compression.ports import HookInvocationResult
        from prismclaw.engine.hooks import HookEvent
        from prismclaw.engine.hooks.schema import HookOutcome

        event_map = {
            "PRE_COMPACT": HookEvent.PRE_COMPACT,
            "POST_COMPACT": HookEvent.POST_COMPACT,
        }
        hook_event = event_map.get(event)
        if hook_event is None:
            return None
        arg = payload if payload is not None else ctx
        result = await self._hook_dispatcher.dispatch(hook_event, arg)
        outcome = "BLOCKING" if result.outcome == HookOutcome.BLOCKING else "SUCCESS"
        return HookInvocationResult(
            outcome=outcome,
            system_message=result.system_message,
        )

    # ── security.gate.governance ──
    def raise_circuit_breaker(self, *, message: str) -> None:
        from prismclaw.engine.security.gate.governance import (
            CircuitBreakerError,
        )

        raise CircuitBreakerError(message)

    def get_context_validator_chain(self) -> ContextValidatorChain:
        from prismclaw.engine.security.gate.governance import ContextValidatorChain

        return ContextValidatorChain()

    # ── execution.checkpoint ──
    async def persist_run_snapshot(
        self,
        session_store: SessionStore,
        tree: SessionTree,
        snapshot: RunSnapshot,
        *,
        snapshot_store: RunSnapshotStore | None = None,
    ) -> None:
        from prismclaw.engine.execution.checkpoint import (
            persist_run_snapshot as _persist_run_snapshot,
        )

        await _persist_run_snapshot(
            session_store,
            tree,
            snapshot,
            snapshot_store=snapshot_store,
        )


@dataclass
class DreamBundle:
    """Dream 子系统组件——从 AgentRunnerDeps 中提取的子容器。

    将 3 个 Any 字段收敛为 1 个类型化 bundle，减少 deps 顶层字段数。
    仅在 wiring 层组装时赋值，core 运行时不直接访问。
    """

    review_service: DreamReviewServiceProtocol | None = None
    boundary: DreamBoundaryProtocol | None = None
    ops: DreamAdminOpsProtocol | None = None


@dataclass
class ExpertBundle:
    """专家包/MCP 运行时组件——从 AgentRunnerDeps 中提取的子容器。

    extension_manager / runtime_mcp_manager / expert_lifecycle_manager
    三者生命周期绑定（同时初始化、同时热重载），适合归入同一子容器。
    """

    extension_manager: ExtensionManagerProtocol | None = None
    runtime_mcp_manager: MCPManagerProtocol | None = None
    lifecycle_manager: ExpertLifecycleProtocol | None = None
    environment_catalog: EnvironmentCatalog | None = None
    # 目录与策略注册表：HTTP 路由查询、发布候选构建等显式读写场景使用。
    expert_registry: ExpertRegistry | None = None
    capability_policy_registry: CapabilityPolicyRegistry | None = None

    def build_session_scope(self, header: SessionHeader) -> Scope:
        from prismclaw.engine.experts.scope import build_session_scope

        if self.environment_catalog is None:
            raise RuntimeError("environment catalog is not configured")
        return build_session_scope(
            header,
            self.environment_catalog.snapshot(),
        )


@dataclass
class PersistenceBundle:
    """持久化/基础设施组件——从 AgentRunnerDeps 中提取的子容器。

    run_snapshot_store / run_ownership_store 两者均为
    底层基础设施，仅在 core agent 的少数路径（所有权管理）中使用。
    trajectory_persistence 提供封装好的 TrajectoryPersistence 实现，
    供 TrajectoryAuditService 使用，engine 层不再直接持有 session_factory。
    """

    run_snapshot_store: RunSnapshotStore | None = None
    run_ownership_store: RunOwnershipStoreProtocol | None = None
    trajectory_persistence: TrajectoryPersistence | None = None


@dataclass
class AgentRunnerDeps:
    llm: LLMClient
    tools: ToolRegistry
    context_engine: ContextEngine = field(default_factory=_lazy_ctx_engine)
    hooks: HookRegistry = field(default_factory=_lazy_hook_registry)
    session_store: SessionStore | None = field(default=None)
    config: AgentRunConfig = field(default_factory=AgentRunConfig)
    workspace_store: WorkspaceStore | None = field(default=None)
    skill_provider: SkillProvider | None = field(default=None)
    skill_runtime: SkillRuntime | None = field(default=None)
    capability_catalog: RuntimeCatalog | None = field(default=None)
    task_manager: TaskManagerProtocol | None = field(default=None)
    lock_manager: LockManagerProtocol | None = field(default=None)
    dream: DreamBundle = field(default_factory=DreamBundle)
    task_profile_service: TaskProfileService = field(default_factory=TaskProfileService)
    channel: str = "web"
    hook_dispatcher: HookDispatcher | None = field(default=None)
    expert: ExpertBundle = field(default_factory=ExpertBundle)
    static_shell_cache: StaticTurnShellCache = field(default_factory=_lazy_static_shell)
    parallel_task_store: SessionParallelTaskStore | None = field(default=None)
    subagent_collaboration: SubagentCollaborationService | None = field(default=None)
    persistence: PersistenceContextProtocol = field(default_factory=PersistenceBundle)
    worker_id: str | None = field(default=None)
    # Web 渠道的会话队列，用于 AskUserQuestion 等人机交互的「执行中途暂停-续跑」。
    # 非交互渠道（CLI / 测试 / 子代理）为 None，即「无头执行」：
    # 无法暂停等人，因此 AskUserQuestion 这类 requires_interactive_user 的工具
    # 会被 HeadlessModeReducer 直接从模型的工具清单中摘除（不可见、不可调用）。
    # 定时任务已复用 Web 主链路、持有队列，不属此类。
    session_queue: SessionQueueProtocol | None = (
        None  # channels/web 专属，避免 core→channels 反向依赖
    )
    # ── Runtime 客户端 ──
    # runtime_client: WorkspaceRuntimeClient | InProcessRuntimeClient 实例
    # 统一遵循 ExecutionEnv Protocol 契约
    runtime_client: ExecutionEnv | None = None
    # The orchestrator is a strategy object, not a state owner.
    # It may be reused across queries, but cross-turn bookkeeping now
    # lives on ``RunSnapshot.compression_runtime`` instead of instance memory.
    session_compressor: CompressionOrchestrator = field(default_factory=_lazy_compressor)
    # Per-result LLM summarization callback.
    # When set, result_compaction uses this instead of character truncation.
    summarize_fn: Callable[[str], Awaitable[str]] | None = None
    # 回调收敛容器（对齐 Pi）：on_compaction_node / on_mcp_death /
    # on_subagent_progress 统一从这里读取。
    callbacks: AgentCallbacks | None = None
    governance: ToolGovernance | None = field(default=None)
    # compression 层依赖反转——ports 注入。
    # compression 层通过 deps.ports 访问 execution/hooks/security 能力，
    # 避免 compression → execution 反向 import。
    ports: Any = field(default=None)

    def __post_init__(self) -> None:
        if self.parallel_task_store is None and self.session_store is not None:
            self.parallel_task_store = SessionParallelTaskStore(self.session_store)
        self.context_engine.set_result_compaction_config(self.config.context.compaction)
        self.context_engine.set_session_store(self.session_store)
        # 构造 compression 层依赖反转 ports。
        # hook_dispatcher/task_profile_service 在构造时已就位；其余
        # execution 能力通过 lazy import 在方法内调用。
        if self.ports is None:
            self.ports = ExecutionRuntimePorts(
                hook_dispatcher=self.hook_dispatcher,
                task_profile_service=self.task_profile_service,
                hook_registry=self.hooks,
            )
        # 将 ports 注入 compression 层，反转 execution→compression 依赖。
        self.context_engine.set_ports(self.ports)

    @property
    def context_bootstrap_provider(self) -> BootstrapContextProvider:
        return self.context_engine.bootstrap_provider

    @property
    def context_memory_provider(self) -> MemoryContextProvider:
        return self.context_engine.memory_provider

    @property
    def context_assembler(self) -> ContextAssembler:
        return self.context_engine

    @property
    def context_compactor(self) -> ContextCompactor:
        return self.context_engine

    @property
    def context_ingestor(self) -> ContextIngestor:
        return self.context_engine

    @property
    def context_turn_finalizer(self) -> ContextTurnFinalizer:
        return self.context_engine

    @property
    def context_compaction_settings(self) -> HasCompactionSettings:
        return self.context_engine

    @property
    def task_request_policy(self) -> TaskRequestPolicyProvider:
        return self.task_profile_service

    @property
    def task_runtime_policy(self) -> TaskRuntimePolicyProvider:
        return self.task_profile_service

    def with_request_context(
        self,
        *,
        progress_sink: Callable[[SubagentProgressBatch], Awaitable[None]] | None = None,
        terminal_sink: Callable[[ParallelTaskRuntimeRecord], Awaitable[None]] | None = None,
        channel: str | None = None,
    ) -> AgentDeps:
        """创建注入了请求级上下文的 deps 变体。

        实现 AgentDeps Protocol 的 with_request_context() 方法。
        delivery 层通过 Protocol 调用此方法，不需要知道
        AgentRunnerDeps 的内部字段名。
        """
        import dataclasses

        overrides: dict[str, Any] = {}
        if progress_sink is not None or terminal_sink is not None:
            base = self.callbacks if self.callbacks is not None else AgentCallbacks()
            overrides["callbacks"] = dataclasses.replace(
                base,
                on_subagent_progress=progress_sink or base.on_subagent_progress,
                on_subagent_terminal=terminal_sink or base.on_subagent_terminal,
            )
        if channel is not None:
            overrides["channel"] = channel
        return dataclasses.replace(self, **overrides)
