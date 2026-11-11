"""ContextRuntimePorts — 依赖反转协议。

compression 层通过此协议访问 execution/infrastructure 能力，
execution 层在 bootstrap 时注入实现，禁止 compression 反向 import。

设计原则：
- 类型注解所需 execution 类型（QueryLoopState / CompressionRuntimeState /
  TaskCompactionPolicy / PromptCacheRuntimeState / InvokedSkillState 等）
  通过 TYPE_CHECKING 引入，runtime 不依赖；
- runtime 行为所需 execution 能力（get_effective_context_window、
  CircuitBreakerException、HookEvent/HookOutcome、persist_*、
  compress_tool_result、ContextValidatorChain、persist_run_snapshot 等）
  通过 ports 方法调用，避免 compression 层 import execution.*；
- execution 层在 bootstrap 中实现 ExecutionRuntimePorts 并注入到
  CompressionOrchestrator，是唯一的反向依赖边界。

依赖方向（单向，禁止反向）：
    schema ← foundation/state ← compression.ports ← compression.* ← execution ← assembly/app
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class HookInvocationResult:
    """invoke_compaction_hooks 的统一返回类型。

    把 plugins.hooks.schema.HookOutcome 枚举收敛为字符串，避免
    compression 层 import plugins 模块。
    """
    outcome: str  # "SUCCESS" | "BLOCKING"
    system_message: str | None = None


@runtime_checkable
class ContextRuntimePorts(Protocol):
    """compression 层访问 execution 能力的依赖反转协议。

    所有方法对应原 execution.* 子系统的反向 import；将其收敛到
    此协议后，compression 层不再 runtime import execution，依赖图
    单向：schema ← state ← compression.ports ← compression.* ← execution。
    """

    # ── execution.request.model_fallback ──
    def get_effective_context_window(
        self,
        model_id: str | None,
        *,
        max_output_tokens_for_summary: int,
        fallback: int,
    ) -> int:
        """返回当前模型的有效上下文窗口大小。"""
        ...

    # ── execution.staging.tool_result_persistence ──
    def is_persisted_content(self, content: object) -> bool:
        """判断 content 是否为已持久化的工具结果引用（<persisted-output>）。"""
        ...

    def persist_tool_result_sync(
        self,
        *,
        tool_use_id: str,
        content: Any,
        reason: str,
        working_dir: Any | None,
        tool_name: str,
    ) -> str | None:
        """sync 版本的工具结果落盘；返回 <persisted-output> 引用文本。"""
        ...

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
        """将 context block 落盘并返回引用文本。"""
        ...

    # ── execution.output.output_compressor ──
    def compress_tool_result(
        self,
        content: Any,
        tool_name: str | None = None,
        tool_args: Any = None,
    ) -> tuple[Any, Any]:
        """压缩工具结果；返回 (compacted_result, stats)。

        stats 含 .lossy / .saved_chars 字段。"""
        ...

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
        """构造 hooks.CompactionContext dataclass 实例。

        compression 不再 import hooks.CompactionContext，统一通过此方法
        在 execution 端构造。"""
        ...

    def notify_before_compaction(self, ctx: Any) -> Any:
        """进程内 AgentHook 迭代：广播 before_compaction。

        委托给 hook_registry.notify_before_compaction(ctx)；registry
        不可用时 no-op。与 invoke_compaction_hooks（插件 dispatcher）
        是两条独立 hook 通道，统一收敛到 ports 边界。"""
        ...

    def notify_after_compaction(self, ctx: Any, result: Any) -> Any:
        """进程内 AgentHook 迭代：广播 after_compaction。

        委托给 hook_registry.notify_after_compaction(ctx, result)；
        registry 不可用时 no-op。"""
        ...

    async def invoke_compaction_hooks(
        self,
        *,
        event: str,  # "PRE_COMPACT" | "POST_COMPACT"
        ctx: Any,
        payload: dict[str, Any] | None = None,
    ) -> HookInvocationResult | None:
        """调用 hook dispatcher 触发 PRE_COMPACT / POST_COMPACT 事件。

        返回 HookInvocationResult（outcome="SUCCESS"|"BLOCKING"，
        system_message）。当 hook_dispatcher 不可用时返回 None，
        调用方视为 SUCCESS 不阻断。event=PRE_COMPACT 时若返回
        outcome="BLOCKING"，调用方中止压缩。"""
        ...

    # ── security.gate.governance ──
    def raise_circuit_breaker(self, *, message: str) -> None:
        """抛出 CircuitBreakerException。

        compression 不再 import CircuitBreakerException，统一通过此方法
        在 execution 端抛出。调用方先本地 breaker.is_open(threshold)
        判定，再调用此方法抛出。"""
        ...

    def get_context_validator_chain(self) -> Any:
        """返回 ContextValidatorChain 实例，供 AssembleCoordinator 使用。"""
        ...

    # ── execution.checkpoint ──
    async def persist_run_snapshot(
        self,
        session_store: Any,
        tree: Any,
        snapshot: Any,
        *,
        snapshot_store: Any | None = None,
    ) -> None:
        """持久化 RunSnapshot（preservation.cleanup_on_terminal_exit 使用）。"""
        ...
