from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol
from collections.abc import Awaitable, Callable

if TYPE_CHECKING:
    from prismclaw.engine.trajectory.bus_events import ConversationNodeAdded
    from prismclaw.engine.execution.capabilities.catalog import CapabilityCatalogService
    from prismclaw.engine.execution.query.context_preparation import StaticTurnShellCache
    from prismclaw.engine.compression.orchestrator import CompressionOrchestrator
    from prismclaw.engine.compression.cache_block_packing import (
        PromptCacheRuntimeContract,
    )
    from prismclaw.engine.execution.skills.runtime import SkillRuntime
    from prismclaw.engine.toolkit.registry.registry import ToolRegistry
    from prismclaw.engine.hooks import (
        HookRegistry,
        SkillProvider,
    )
    from prismclaw.schema.agent.context import (
        BootstrapContextProvider,
        ContextAssembler,
        ContextCompactor,
        ContextIngestor,
        ContextTurnFinalizer,
        HasCompactionSettings,
        MemoryContextProvider,
    )
    from prismclaw.engine.task_profile.interfaces import (
        TaskRequestPolicyProvider,
        TaskRuntimePolicyProvider,
    )
    from prismclaw.schema.agent.context import ContextEngine
    from prismclaw.engine.execution.handoff.supervisor import HandoffSupervisor
    from prismclaw.engine.security.gate import SafetyGate
    from prismclaw.engine.security.audit.store import AuditStore
    from prismclaw.engine.security.audit.audit_logger import AuditLogger
    from prismclaw.engine.security.gate.types import SubagentProgressSink
    from prismclaw.schema.agent import SubagentProgressBatch
    from prismclaw.engine.toolkit.registry.governance import ToolGovernance

from prismclaw.schema.session import REPLACEMENT_DECISION_CUSTOM_TYPE
from prismclaw.schema.content import replacement_decision_from_artifact
from prismclaw.schema.content.compaction import parse_compaction_artifact
from prismclaw.schema.llm.types import LLMStreamFn
from prismclaw.engine.hooks import HookDispatcher
from prismclaw.engine.subagent.runtime.parallel import SessionParallelTaskStore
from prismclaw.engine.task_profile.service import TaskProfileService
from prismclaw.schema.services.task_protocol import TaskManagerProtocol
from prismclaw.schema.agent.data_types import CacheStrategySnapshot
from prismclaw.schema.agent.session import MessageEntry, SessionHeader, TurnRuntimeMetricsSnapshot
from prismclaw.schema.agent.task_runtime import ReviewRequirements, TaskRuntimeState
from prismclaw.schema.agent.task_types import TaskSpec
from prismclaw.schema.config.config import AgentRunConfig
from prismclaw.schema.content.context import ContextPackLayout
from prismclaw.schema.session.types import CACHE_STRATEGY_CUSTOM_TYPE, CONTEXT_COMPACTION_REFERENCE_CUSTOM_TYPE, CustomEntry, REVIEW_REQUIREMENTS_CUSTOM_TYPE, SESSION_MEMORY_CUSTOM_TYPE, SessionTree, TASK_RUNTIME_CUSTOM_TYPE, TASK_SPEC_CUSTOM_TYPE, TURN_RUNTIME_METRICS_CUSTOM_TYPE, generate_entry_id, now_iso
from prismclaw.schema.storage.storage_types import SessionStore
from prismclaw.schema.services.env_types import ExecutionEnv
from prismclaw.schema.services.session_queue import SessionQueueProtocol
from prismclaw.schema.services.bus import BusProtocol
from prismclaw.schema.services.skill_management import SkillManagementProtocol
from prismclaw.schema.services.lock import LockManagerProtocol
from prismclaw.schema.services.zip import ZipExtractFn, ZipVerifyFn
from prismclaw.schema.agent.types import AgentDeps
from prismclaw.schema.agent.callbacks import LoopCallbacks
from prismclaw.schema.agent.bundles import (
    DreamBoundaryProtocol,
    DreamAdminOpsProtocol,
    DreamReviewServiceProtocol,
    ExtensionManagerProtocol,
    MCPManagerProtocol,
    PluginLifecycleProtocol,
    RunOwnershipStoreProtocol,
    TrajectoryPersistenceProtocol,
)
from prismclaw.schema.persistence.persistence import PersistenceContextProtocol, RunSnapshotStore
from prismclaw.schema.session.event_logger import SessionEventLogger
from prismclaw.schema.storage.storage_types import WorkspaceStore

from prismclaw.engine.execution.query.context_helpers import (
    _existing_context_compaction_artifact_keys,
)
from prismclaw.engine.toolkit.builtin.tool_helpers import _entry_to_llm_dict
# 再导出门面：governance / preparer / agent_loop 从本模块导入下列符号，
# 本模块自身不直接调用。静态检查会把它们判为 F401，勿删。
from prismclaw.engine.execution.loop._bootstrap_helpers import (
    _build_manual_confirmation_evidence,
    _build_preflight_probe_evidence,
    _build_recovery_probe_evidence,
    _build_task_runtime_from_spec,
    _build_workspace_state_evidence,
    _node_seq,
    _tool_args_for_call,
    _user_message_count,
)

logger = logging.getLogger(__name__)

_event_logger = SessionEventLogger()

def _lazy_import(module_path: str):
    """Lazily import a module to avoid circular imports."""
    import importlib
    return importlib.import_module(module_path)


def _lazy_ctx_engine():
    from prismclaw.engine.compression.context_engine import DefaultContextEngine
    return DefaultContextEngine()

def _lazy_hook_registry():
    from prismclaw.engine.hooks import HookRegistry
    return HookRegistry()

def _lazy_static_shell():
    from prismclaw.engine.execution.query.context_preparation import StaticTurnShellCache
    return StaticTurnShellCache()

def _lazy_compressor():
    from prismclaw.engine.compression.orchestrator import CompressionOrchestrator
    from prismclaw.engine.execution.query.turn_preparation import prepare_query_turn_from_static_context
    return CompressionOrchestrator(prepare_turn_callable=prepare_query_turn_from_static_context)

def _lazy_prompt_cache():
    from prismclaw.engine.compression.cache_block_packing import DefaultPromptCacheRuntimeContract
    return DefaultPromptCacheRuntimeContract()


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
        hook_dispatcher: Any = None,
        task_profile_service: Any = None,
        hook_registry: Any = None,
    ) -> None:
        self._hook_dispatcher = hook_dispatcher
        self._task_profile_service = task_profile_service
        # 进程内 AgentHook registry（engine.hooks.HookRegistry），
        # 与插件 hook_dispatcher 是两条独立 hook 通道。
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
            model_id,
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
        content: Any,
        reason: str,
        working_dir: Any | None,
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
        working_dir: Any | None = None,
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
        content: Any,
        tool_name: str | None = None,
        tool_args: Any = None,
    ) -> tuple[Any, Any]:
        from prismclaw.engine.execution.output.output_compressor import (
            compress_tool_result as _compress_tool_result,
        )
        return _compress_tool_result(content, tool_name=tool_name, tool_args=tool_args)

    # ── hooks (HookEvent + CompactionContext + HookOutcome) ──
    def build_compaction_context(
        self,
        *,
        session_id: str,
        workspace_id: str,
        agent_id: str,
        message_count: int,
        tokens_before: int,
    ) -> Any:
        from prismclaw.engine.hooks import CompactionContext
        return CompactionContext(
            session_id=session_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            message_count=message_count,
            tokens_before=tokens_before,
        )

    def notify_before_compaction(self, ctx: Any) -> Any:
        if self._hook_registry is None:
            return None
        return self._hook_registry.notify_before_compaction(ctx)

    def notify_after_compaction(self, ctx: Any, result: Any) -> Any:
        if self._hook_registry is None:
            return None
        return self._hook_registry.notify_after_compaction(ctx, result)

    async def invoke_compaction_hooks(
        self,
        *,
        event: str,
        ctx: Any,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        if self._hook_dispatcher is None:
            return None
        from prismclaw.engine.hooks import HookEvent
        from prismclaw.engine.plugins.hooks.schema import HookOutcome
        from prismclaw.engine.compression.ports import HookInvocationResult
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
            CircuitBreakerException,
        )
        raise CircuitBreakerException(message)

    def get_context_validator_chain(self) -> Any:
        from prismclaw.engine.security.gate.governance import ContextValidatorChain
        return ContextValidatorChain()

    # ── execution.checkpoint ──
    async def persist_run_snapshot(
        self,
        session_store: Any,
        tree: Any,
        snapshot: Any,
        *,
        snapshot_store: Any | None = None,
    ) -> None:
        from prismclaw.engine.execution.checkpoint import persist_run_snapshot as _persist_run_snapshot
        await _persist_run_snapshot(
            session_store,
            tree,
            snapshot,
            snapshot_store=snapshot_store,
        )


class _AppendDeps(Protocol):

    context_ingestor: ContextIngestor

    session_store: SessionStore


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
class PluginBundle:
    """插件/MCP 运行时组件——从 AgentRunnerDeps 中提取的子容器。

    extension_manager / runtime_mcp_manager / plugin_lifecycle_manager
    三者生命周期绑定（同时初始化、同时热重载），适合归入同一子容器。
    """
    extension_manager: ExtensionManagerProtocol | None = None
    runtime_mcp_manager: MCPManagerProtocol | None = None
    lifecycle_manager: PluginLifecycleProtocol | None = None


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
    trajectory_persistence: TrajectoryPersistenceProtocol | None = None


@dataclass
class AgentRunnerDeps:
    llm: LLMStreamFn
    tools: ToolRegistry
    context_engine: ContextEngine = field(default_factory=_lazy_ctx_engine)
    hooks: HookRegistry = field(default_factory=_lazy_hook_registry)
    session_store: SessionStore | None = field(default=None)
    config: AgentRunConfig = field(default_factory=AgentRunConfig)
    workspace_store: WorkspaceStore | None = field(default=None)
    skill_provider: SkillProvider | None = field(default=None)
    skill_runtime: SkillRuntime | None = field(default=None)
    capability_catalog: CapabilityCatalogService | None = field(default=None)
    task_manager: TaskManagerProtocol | None = field(default=None)
    lock_manager: LockManagerProtocol | None = field(default=None)
    audit_logger: AuditLogger | None = field(default=None)
    mcp_death_handler: Callable[[str], Awaitable[None]] | None = field(default=None)
    subagent_progress_sink: Callable[[SubagentProgressBatch], Awaitable[None]] | None = field(default=None)
    handoff_supervisor: HandoffSupervisor | None = field(default=None)
    dream: DreamBundle = field(default_factory=DreamBundle)
    task_profile_service: TaskProfileService = field(default_factory=TaskProfileService)
    channel: str = "web"
    hook_dispatcher: HookDispatcher | None = field(default=None)
    plugin: PluginBundle = field(default_factory=PluginBundle)
    static_shell_cache: StaticTurnShellCache = field(default_factory=_lazy_static_shell)
    parallel_task_store: SessionParallelTaskStore | None = field(default=None)
    persistence: PersistenceContextProtocol = field(default_factory=PersistenceBundle)
    worker_id: str | None = field(default=None)
    # Web 渠道的会话队列，用于 AskUserQuestion 等人机交互的「执行中途暂停-续跑」。
    # 非交互渠道（CLI / 测试 / 子代理）为 None，即「无头执行」：
    # 无法暂停等人，因此 AskUserQuestion 这类 requires_interactive_user 的工具
    # 会被 HeadlessModeReducer 直接从模型的工具清单中摘除（不可见、不可调用）。
    # 定时任务已复用 Web 主链路、持有队列，不属此类。
    session_queue: SessionQueueProtocol | None = None  # channels/web 专属，避免 core→channels 反向依赖
    # ── Runtime 客户端 + SafetyGate ──
    # runtime_client: WorkspaceRuntimeClient | InProcessRuntimeClient 实例
    # 统一遵循 ExecutionEnv Protocol 契约
    runtime_client: ExecutionEnv | None = None
    # safety_gate: SafetyGate 实例（RuleGate → RiskRouter → WorkspaceMode → ConfirmationGateway）
    safety_gate: SafetyGate | None = field(default=None)
    # audit_store: AuditStore 实例（异步队列 + 后台 flush worker 持久化工具调用/审批审计到 PG）
    audit_store: AuditStore | None = field(default=None)
    # The orchestrator is a strategy object, not a state owner.
    # It may be reused across queries, but cross-turn bookkeeping now
    # lives on ``RunSnapshot.compression_runtime`` instead of instance memory.
    session_compressor: CompressionOrchestrator = field(
        default_factory=_lazy_compressor
    )
    prompt_cache_runtime_contract: PromptCacheRuntimeContract = field(
        default_factory=_lazy_prompt_cache
    )
    # Skill management facade (DefaultSkillManagement).
    skill_management: SkillManagementProtocol | None = None
    # Per-result LLM summarization callback.
    # When set, result_compaction uses this instead of character truncation.
    summarize_fn: Callable[[str], Awaitable[str]] | None = None
    # Zip extraction callbacks — injected by wiring layer to avoid engine→skills import
    zip_extract_fn: ZipExtractFn | None = None
    zip_verify_fn: ZipVerifyFn | None = None
    # Callback to publish a compaction trajectory node (replaces
    # lazy import of this module from history_compaction.py).
    on_compaction_node: Callable[..., None] | None = None
    # 极简注入：回调收敛容器（对齐 Pi）。当 callbacks 非 None 时，
    # 优先从 callbacks 读取 on_compaction_node/on_mcp_death/on_subagent_progress/on_stream_event。
    # 旧字段保留兼容，逐步迁移。
    callbacks: LoopCallbacks | None = None
    # AgentDeps Protocol requires a bus property; not consumed via deps
    # (engine code uses current_bus() directly), but needed for type compat.
    bus: BusProtocol | None = None
    governance: ToolGovernance | None = field(default=None)
    # compression 层依赖反转——ports 注入。
    # compression 层通过 deps.ports 访问 execution/hooks/security 能力，
    # 避免 compression → execution 反向 import。
    ports: Any = field(default=None)

    # ── Callback resolution helpers (unified access) ──

    @property
    def resolved_on_compaction_node(self) -> Callable[..., None] | None:
        """Resolve on_compaction_node: callbacks first, then legacy field."""
        if self.callbacks is not None:
            cb = self.callbacks.on_compaction_node
            if cb is not None:
                return cb
        return self.on_compaction_node

    @property
    def resolved_mcp_death_handler(self) -> Callable[[str], Awaitable[None]] | None:
        """Resolve mcp_death_handler: callbacks first, then legacy field."""
        if self.callbacks is not None:
            cb = self.callbacks.on_mcp_death
            if cb is not None:
                return cb
        return self.mcp_death_handler

    @property
    def resolved_subagent_progress_sink(self) -> Callable[[Any], Awaitable[None]] | None:
        """Resolve subagent_progress_sink: callbacks first, then legacy field."""
        if self.callbacks is not None:
            cb = self.callbacks.on_subagent_progress
            if cb is not None:
                return cb
        return self.subagent_progress_sink

    def __post_init__(self) -> None:
        # Note: skill_runtime construction moved to wiring/runner_assembly.py.
        # The wiring layer explicitly passes skill_runtime=
        # to AgentRunnerDeps, so this fallback is no longer needed.
        if self.parallel_task_store is None and self.session_store is not None:
            self.parallel_task_store = SessionParallelTaskStore(self.session_store, self.lock_manager)
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
        audit_logger: AuditLogger | None = None,
        progress_sink: SubagentProgressSink | None = None,
        channel: str | None = None,
    ) -> AgentDeps:
        """创建注入了请求级上下文的 deps 变体。

        实现 AgentDeps Protocol 的 with_request_context() 方法。
        delivery 层通过 Protocol 调用此方法，不需要知道
        AgentRunnerDeps 的内部字段名。
        """
        import dataclasses
        overrides: dict[str, Any] = {}
        if audit_logger is not None:
            overrides["audit_logger"] = audit_logger
        if progress_sink is not None:
            overrides["subagent_progress_sink"] = progress_sink
        if channel is not None:
            overrides["channel"] = channel
        return dataclasses.replace(self, **overrides)


class EntryRuntimeBootstrapError(RuntimeError):
    def __init__(self, *, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"entry runtime bootstrap failed: {reason_code}")

    @property
    def error_code(self) -> str:
        return "entry_runtime_bootstrap_failed"

    @property
    def user_message(self) -> str:
        return "entry runtime bootstrap failed"


def _derive_session_key(session_id: str) -> str:
    """Derive the ``session_key`` grouping prefix from a *session_id*.

    Delegates to the single shared implementation in
    :mod:`prismclaw.kernel.session.ids` (SDD context-governance
    W10: the derivation logic previously existed in three diverging copies —
    the memory subsystem's copy only understood the ``:s:`` convention and
    silently isolated every web-chat conversation from the user's durable
    memories).
    """
    from prismclaw.schema.session.ids import derive_memory_session_key

    return derive_memory_session_key(session_id)


async def ensure_session(
    deps: AgentRunnerDeps,
    *,
    session_id: str,
    workspace_id: str,
    agent_id: str,
) -> SessionTree:
    existing = await deps.session_store.load(session_id)
    if existing is not None:
        return existing
    header = SessionHeader(
        id=session_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
        session_key=_derive_session_key(session_id),
    )
    tree = SessionTree(header=header)
    await deps.session_store.save_header(tree)
    return tree




def publish_context_node(
    tree: SessionTree,
    *,
    entry_id: str,
    label: str,
    content: str,
) -> None:
    """Publish a kind='context' ConversationNodeAdded (DSH context kind).

    DSH 的 context kind 标记"注入模型上下文的非用户消息"（steering/context
    节点）。PrismClaw 对应事件：技能激活注入 —— 技能 prompt 本体进系统提示
    （由 kind='system' cell 逐轮快照覆盖），这里记录的是激活时点 + 注入的
    控制消息，即"模型上下文何时被谁改变"。Best-effort，永不抛出。
    """
    try:
        from prismclaw.engine.trajectory.bus import current_bus
        from prismclaw.engine.trajectory.bus_events import ConversationNodeAdded

        bus = current_bus()
        if bus is None or bus._is_noop:
            return
        now_ms = int(time.time() * 1000)
        bus.publish(ConversationNodeAdded(
            session_id=tree.header.id,
            entry_id=entry_id,
            # 无 tree 节点（transient 注入）→ 排在当前末尾，与 compacted 同语义
            seq=len(tree.order),
            turn=max(1, _user_message_count(tree)),
            role="user",
            kind="context",
            content_preview=f"{label}: {content[:500]}" if len(content) > 500 else (f"{label}: {content}" if content else label),
            input_detail=content or None,
            started_at_ms=now_ms,
            completed_at_ms=now_ms,
        ))
    except Exception:
        # Best-effort — never break the skill dispatch path
        pass


def publish_compaction_node(
    tree: SessionTree,
    *,
    entry_id: str,
    summary: str,
) -> None:
    """Publish a kind='compacted' ConversationNodeAdded for a CompactionEntry.

    CompactionEntry is committed directly by the compaction pipeline
    (history_compaction.py autocompact / builtin.py manual /compact) — it
    never flows through _append, so this is the compacted-cell publish
    point. Best-effort: never raises into the compaction path.

    NOTE: the compaction path commits the entry to the session store but
    does NOT register it in the in-memory tree.order, so seq falls back to
    len(tree.order) — "appended at the end", which matches the compaction
    position semantics (the cell's unique key is entry_id, not seq).
    """
    try:
        from prismclaw.engine.trajectory.bus import current_bus
        from prismclaw.engine.trajectory.bus_events import ConversationNodeAdded

        bus = current_bus()
        if bus is None or bus._is_noop:
            return
        now_ms = int(time.time() * 1000)
        bus.publish(ConversationNodeAdded(
            session_id=tree.header.id,
            entry_id=entry_id,
            seq=_node_seq(tree, entry_id),
            turn=max(1, _user_message_count(tree)),
            role="system",
            kind="compacted",
            content_preview=(summary[:500] + "…") if len(summary) > 500 else (summary or None),
            output_detail=summary or None,
            started_at_ms=now_ms,
            completed_at_ms=now_ms,
        ))
    except Exception:
        # Best-effort — never break the compaction commit path
        pass


# ---------------------------------------------------------------------------
# _append() 拆分为三个职责单一的子函数
# ---------------------------------------------------------------------------

async def append_to_tree(
    deps: _AppendDeps,
    tree: SessionTree,
    entry: MessageEntry | CustomEntry,
) -> None:
    """纯树操作：append entry + commit_events + context ingest。"""
    if isinstance(entry, MessageEntry):
        events = _event_logger.message_entry(tree.header.id, entry, entry.id)
        tree.header.last_interaction_at = now_iso()
        events.append(_event_logger.request_header(tree.header.id, {"last_interaction_at": tree.header.last_interaction_at}))
        await deps.context_ingestor.ingest(tree.header.id, _entry_to_llm_dict(entry))
    else:
        events = [_event_logger.entry_updated(tree.header.id, entry, leaf_id=entry.id)]
    # Keep the in-memory tree in sync with what we just committed.
    if entry.id not in tree.entries:
        tree.append(entry)
    await deps.session_store.commit_events(events)


def _build_conversation_node(
    tree: SessionTree,
    entry: MessageEntry,
) -> ConversationNodeAdded | None:
    """纯函数：从 tree + entry 构建 ConversationNodeAdded 事件。

    返回 None 表示不应发布（非 MessageEntry 由调用方判断）。
    """
    from prismclaw.engine.trajectory.bus_events import ConversationNodeAdded
    _seq = _node_seq(tree, entry.id)
    _user_count = _user_message_count(tree)
    _turn = _user_count if entry.role == "user" else max(1, _user_count)
    _role = entry.role or ""
    _kind = {
        "user": "user",
        "assistant": "message",
        "tool": "tool",
        "system": "system",
    }.get(_role, "system")

    # Iteration, LLM metrics from trajectory runtime ctx
    _iteration: int | None = None
    _metrics_model: str | None = None
    _metrics_finish: str | None = None
    _metrics_usage: dict[str, Any] = {}
    _metrics_ttft: float | None = None
    _metrics_decoding: float | None = None
    _started_ms: int
    _completed_ms: int
    try:
        from prismclaw.engine.trajectory.bus_context import current_runtime
        _rt = current_runtime()
    except Exception:
        _rt = None
    _now_ms = int(time.time() * 1000)
    if _rt is not None and _rt.session_id == tree.header.id:
        if _role in ("assistant", "tool"):
            _iteration = _rt.iteration
        if _role == "assistant" and _rt.llm_metrics is not None:
            _m = _rt.llm_metrics
            _metrics_model = _m.model
            _metrics_finish = _m.finish_reason
            _metrics_usage = {
                "usage_input_tokens": _m.usage_input_tokens,
                "usage_output_tokens": _m.usage_output_tokens,
                "usage_cache_read_tokens": _m.usage_cache_read_tokens,
                "usage_cache_write_tokens": _m.usage_cache_write_tokens,
                "usage_reasoning_tokens": _m.usage_reasoning_tokens,
            }
            _metrics_ttft = _m.ttft_ms
            _metrics_decoding = _m.decoding_ms
            _started_ms = _m.started_at_ms or _now_ms
            _completed_ms = _m.completed_at_ms or _now_ms
        elif _role == "tool" and entry.tool_call_id:
            _started_ms = _rt.tool_call_starts.pop(entry.tool_call_id, _now_ms)
            _completed_ms = _now_ms
        else:
            _started_ms = _now_ms
            _completed_ms = _now_ms
    else:
        _started_ms = _now_ms
        _completed_ms = _now_ms

    # content_preview (up to 500 chars) + full *_detail fields
    _content_preview: str | None = None
    _full_text: str | None = None
    _full_thinking: str | None = None
    _content = entry.content
    if isinstance(_content, str):
        _full_text = _content if _content else None
        _content_preview = _content[:500]
        if len(_content) > 500:
            _content_preview = _content_preview + "…"
    elif isinstance(_content, list):
        _parts: list[str] = []
        _thinking_parts: list[str] = []
        for _b in _content:
            try:
                if isinstance(_b, str):
                    _parts.append(_b)
                    continue
                _btype = getattr(_b, "type", "") or ""
                _t = getattr(_b, "text", None) or ""
                if not isinstance(_t, str):
                    continue
                if _btype in ("thinking", "reasoning"):
                    _thinking_parts.append(_t)
                else:
                    _parts.append(_t)
            except Exception:
                continue
        _joined = "".join(_parts)
        if _joined:
            _full_text = _joined
            _content_preview = _joined[:500]
            if len(_joined) > 500:
                _content_preview = _content_preview + "…"
        if _thinking_parts:
            _full_thinking = "".join(_thinking_parts) or None

    # role-driven *_detail assignment
    _input_detail: str | None = None
    _output_detail: str | None = None
    _thinking_detail: str | None = None
    _is_error: bool = entry.is_error
    if _role == "user":
        _input_detail = _full_text
    elif _role == "assistant":
        _output_detail = _full_text
        _thinking_detail = _full_thinking
        if _thinking_detail is None and _rt is not None:
            _thinking_detail = _rt.assistant_reasoning
    elif _role == "tool":
        _input_detail = (
            _tool_args_for_call(tree, entry.tool_call_id)
            or entry.tool_call_id
        )
        _output_detail = _full_text
    elif _role == "system":
        _input_detail = _full_text

    # tool_calls_summary (assistant only)
    _tc_summary: tuple[str, ...] = ()
    _tcs = entry.tool_calls
    if _tcs:
        import json as _tc_json
        _summaries: list[str] = []
        for _tc in _tcs:
            try:
                _tc_dict = _tc if isinstance(_tc, dict) else _tc.model_dump()
                _tc_name = str(_tc_dict.get("name") or _tc_dict.get("function", {}).get("name") or "?")
                _tc_args = _tc_dict.get("arguments") or _tc_dict.get("function", {}).get("arguments") or {}
                if isinstance(_tc_args, str):
                    _tc_args_str = _tc_args
                else:
                    _tc_args_str = _tc_json.dumps(_tc_args, ensure_ascii=False, default=str)
                _summaries.append(f"{_tc_name}({_tc_args_str[:200]})")
            except Exception:
                _summaries.append("?")
        _tc_summary = tuple(_summaries)

    return ConversationNodeAdded(
        session_id=tree.header.id,
        entry_id=entry.id,
        seq=_seq,
        turn=_turn,
        role=_role,
        kind=_kind,
        content_preview=_content_preview,
        tool_calls_summary=_tc_summary,
        tool_call_id=entry.tool_call_id,
        input_detail=_input_detail,
        output_detail=_output_detail,
        thinking_detail=_thinking_detail,
        is_error=_is_error,
        schema_detail=None,
        started_at_ms=_started_ms,
        completed_at_ms=_completed_ms,
        iteration=_iteration,
        error=None,
        model=_metrics_model,
        finish_reason=_metrics_finish,
        usage_input_tokens=_metrics_usage.get("usage_input_tokens"),
        usage_output_tokens=_metrics_usage.get("usage_output_tokens"),
        usage_cache_read_tokens=_metrics_usage.get("usage_cache_read_tokens"),
        usage_cache_write_tokens=_metrics_usage.get("usage_cache_write_tokens"),
        usage_reasoning_tokens=_metrics_usage.get("usage_reasoning_tokens"),
        ttft_ms=_metrics_ttft,
        decoding_ms=_metrics_decoding,
        tool_name=entry.tool_name,
    )


async def publish_trajectory_node(
    tree: SessionTree,
    entry: MessageEntry,
) -> None:
    """ConversationNodeAdded 发布——独立于树操作，best-effort。"""
    try:
        from prismclaw.engine.trajectory.bus import current_bus
        import logging as _pub_log
        _pub_logger = _pub_log.getLogger(__name__)

        _bus = current_bus()
        _pub_logger.info(
            "publish_trajectory_node: entry_id=%s role=%s bus_is_noop=%s",
            entry.id, getattr(entry, "role", "?"),
            getattr(_bus, "_is_noop", "?"),
        )
        if _bus is not None and not _bus._is_noop:
            node = _build_conversation_node(tree, entry)
            if node is not None:
                _bus.publish(node)
    except Exception as _pub_exc:
        _pub_logger.warning(
            "publish_trajectory_node FAILED: entry_id=%s role=%s exc=%s",
            getattr(entry, "id", "?"), getattr(entry, "role", "?"), _pub_exc,
        )


async def _append(
    deps: _AppendDeps,
    tree: SessionTree,
    entry: MessageEntry | CustomEntry,
) -> None:
    """组合入口：append_to_tree + publish_trajectory_node。"""
    await append_to_tree(deps, tree, entry)

    if isinstance(entry, MessageEntry):
        await publish_trajectory_node(tree, entry)


async def _persist_context_compaction_references(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    turn_context_layout: ContextPackLayout,
) -> None:
    blocks = turn_context_layout.non_static_blocks()
    existing_keys = _existing_context_compaction_artifact_keys(tree)
    existing_replacements = tree.get_replacement_decision_state().latest_by_source_id()
    for block in blocks:
        meta = (block.metadata or {}).get("result_compaction")
        artifact = parse_compaction_artifact(meta)
        if artifact is None or artifact.scope != "context_block":
            continue
        artifact = artifact.model_copy(update={"frozen": True})
        artifact_key = (artifact.artifact_id, artifact.source_digest or "")
        if artifact_key in existing_keys:
            continue
        entry = CustomEntry(
            id=generate_entry_id(set(tree.entries.keys())),
            parent_id=tree.leaf_id,
            timestamp=now_iso(),
            custom_type=CONTEXT_COMPACTION_REFERENCE_CUSTOM_TYPE,
            data={
                "session_id": tree.header.id,
                **artifact.to_metadata_dict(),
            },
        )
        event = _event_logger.entry_updated(tree.header.id, entry, leaf_id=entry.id)
        await deps.session_store.commit_events([event])
        replacement_decision = replacement_decision_from_artifact(artifact)
        if replacement_decision is not None:
            existing = existing_replacements.get(replacement_decision.source_id)
            if existing is None or existing.stable_payload() != replacement_decision.stable_payload():
                replacement_entry = CustomEntry(
                    id=generate_entry_id(set(tree.entries.keys())),
                    parent_id=tree.leaf_id,
                    timestamp=now_iso(),
                    custom_type=REPLACEMENT_DECISION_CUSTOM_TYPE,
                    data=replacement_decision.model_dump(mode="json"),
                )
                event = _event_logger.entry_updated(tree.header.id, replacement_entry, leaf_id=replacement_entry.id)
                await deps.session_store.commit_events([event])
                existing_replacements[replacement_decision.source_id] = replacement_decision
        existing_keys.add(artifact_key)


async def _persist_cache_strategy_snapshot(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    cache_strategy: CacheStrategySnapshot,
) -> None:
    if not cache_strategy:
        return
    snapshot = {
        key: value
        for key, value in cache_strategy.items()
        if isinstance(value, (str, int, float, bool)) or value is None
    }
    if not snapshot:
        return
    if tree.get_last_cache_strategy() == snapshot:
        return
    entry = CustomEntry(
        id=generate_entry_id(set(tree.entries.keys())),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        custom_type=CACHE_STRATEGY_CUSTOM_TYPE,
        data=snapshot,
    )
    event = _event_logger.entry_updated(tree.header.id, entry, leaf_id=entry.id)
    await deps.session_store.commit_events([event])


async def _persist_turn_runtime_metrics(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    *,
    request_mode: str | None,
    dynamic_payload: int,
    input_tokens: int,
    cache_read_tokens: int,
    result_payload_reduction: int,
) -> None:
    snapshot = TurnRuntimeMetricsSnapshot(
        request_mode=request_mode or "",
        dynamic_payload=max(0, dynamic_payload),
        input_tokens=max(0, input_tokens),
        cache_read_tokens=max(0, cache_read_tokens),
        result_payload_reduction=max(0, result_payload_reduction),
    )
    entry = CustomEntry(
        id=generate_entry_id(set(tree.entries.keys())),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        custom_type=TURN_RUNTIME_METRICS_CUSTOM_TYPE,
        data=dict(snapshot),
    )
    event = _event_logger.entry_updated(tree.header.id, entry, leaf_id=entry.id)
    await deps.session_store.commit_events([event])


async def _persist_session_memory_snapshot(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    *,
    session_id: str,
) -> None:
    getter = getattr(deps.context_engine, "get_session_state", None)
    if getter is None:
        return
    state = getter(session_id)
    snapshot = state.model_dump(mode="json")
    current = tree.get_latest_session_memory_state()
    if current is not None and current.model_dump(mode="json") == snapshot:
        return
    entry = CustomEntry(
        id=generate_entry_id(set(tree.entries.keys())),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        custom_type=SESSION_MEMORY_CUSTOM_TYPE,
        data=snapshot,
    )
    event = _event_logger.entry_updated(tree.header.id, entry, leaf_id=entry.id)
    await deps.session_store.commit_events([event])





async def _bootstrap_entry_runtime(
    *,
    deps: AgentRunnerDeps,
    tree: SessionTree,
    task_spec: TaskSpec,
    review_requirements: ReviewRequirements,
    workspace_id: str | None,
    persist_runtime: bool = True,
) -> TaskRuntimeState:
    """Build the minimal in-memory entry runtime and persist the structural
    snapshots required before mode resolution.

    Per the unified refactor the entry runtime is no longer double-written as a
    ``pending`` intermediate. For a new task the runtime is persisted exactly
    once later, after entry resolution succeeds (``ready``). For recovery
    runs the runtime is persisted here so failure observability is preserved.
    """
    task_runtime = _build_task_runtime_from_spec(
        task_spec=task_spec,
        workspace_id=workspace_id,
    )
    try:
        await _persist_task_spec_snapshot(deps, tree, task_spec=task_spec)
        if persist_runtime:
            await _persist_task_runtime_snapshot(deps, tree, task_runtime=task_runtime)
        await _persist_review_requirements_snapshot(
            deps,
            tree,
            review_requirements=review_requirements,
        )
    except Exception as exc:
        raise EntryRuntimeBootstrapError(reason_code="snapshot_persist_failed") from exc
    return task_runtime



async def _persist_task_runtime_snapshot(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    *,
    task_runtime: TaskRuntimeState,
) -> None:
    current = tree.get_latest_task_runtime()
    if current is not None and current.model_dump(mode="json") == task_runtime.model_dump(mode="json"):
        return
    entry = CustomEntry(
        id=generate_entry_id(set(tree.entries.keys())),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        custom_type=TASK_RUNTIME_CUSTOM_TYPE,
        data=task_runtime.model_dump(mode="json"),
    )
    event = _event_logger.entry_updated(tree.header.id, entry, leaf_id=entry.id)
    await deps.session_store.commit_events([event])


async def _persist_task_spec_snapshot(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    *,
    task_spec: TaskSpec,
) -> None:
    current = tree.get_latest_task_spec()
    if current is not None and current.model_dump(mode="json") == task_spec.model_dump(mode="json"):
        return
    entry = CustomEntry(
        id=generate_entry_id(set(tree.entries.keys())),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        custom_type=TASK_SPEC_CUSTOM_TYPE,
        data=task_spec.model_dump(mode="json"),
    )
    event = _event_logger.entry_updated(tree.header.id, entry, leaf_id=entry.id)
    await deps.session_store.commit_events([event])


async def _persist_review_requirements_snapshot(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    *,
    review_requirements: ReviewRequirements,
) -> None:
    current = tree.get_latest_review_requirements()
    if current is not None and current.model_dump(mode="json") == review_requirements.model_dump(mode="json"):
        return
    entry = CustomEntry(
        id=generate_entry_id(set(tree.entries.keys())),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        custom_type=REVIEW_REQUIREMENTS_CUSTOM_TYPE,
        data=review_requirements.model_dump(mode="json"),
    )
    event = _event_logger.entry_updated(tree.header.id, entry, leaf_id=entry.id)
    await deps.session_store.commit_events([event])
