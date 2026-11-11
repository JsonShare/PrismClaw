"""DecisionState — 跨迭代累积的决策状态（可变但受限写入）。

从 RunLoopState 中"可变但语义受限"的字段提取。
这些字段不是从事件推导的，而是累积决策的结果——
但它们的写入模式受限（如 loop_count 只增不降、recovery_mode 只能开启）。

迁移策略：与 RunLoopState 并存，提供受限写入方法，防止非法状态转换。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prismclaw.schema.agent.task_runtime import EvidenceRecord


@dataclass
class DecisionState:
    """跨迭代累积的决策状态——可变，但写入点受限。

    与 RunLoopState 中自由可变的字段不同，DecisionState 的每个字段
    都有语义约束，通过受限写入方法强制执行。
    """

    # ── 循环检测计数器（只增不降，除非 clear_loops） ──
    tool_loop_count: int = 0
    search_loop_count: int = 0

    # ── 执行证据累积（只追加，不删除） ──
    execution_evidence: list[EvidenceRecord] = field(default_factory=list)

    # ── 恢复模式（只能开启，不能关闭——需要外部重置） ──
    recovery_mode: bool = False

    # ── 溢出恢复使用标记 ──
    overflow_recovery_used: bool = False

    # ── 待处理移交 ──
    pending_handoffs: list[str] = field(default_factory=list)

    # ── 延迟动作 ──
    deferred_actions: list[str] = field(default_factory=list)

    # ── 受限写入方法 ──

    def record_tool_loop(self) -> None:
        """记录一次工具循环。只增不降。"""
        self.tool_loop_count += 1

    def record_search_loop(self) -> None:
        """记录一次搜索循环。只增不降。"""
        self.search_loop_count += 1

    def record_evidence(self, evidence: EvidenceRecord) -> None:
        """追加执行证据。只追加，不删除。"""
        self.execution_evidence.append(evidence)

    def enter_recovery(self) -> None:
        """进入恢复模式。不可逆。"""
        self.recovery_mode = True

    def mark_overflow_recovery_used(self) -> None:
        """标记溢出恢复已使用。不可逆。"""
        self.overflow_recovery_used = True

    def add_pending_handoff(self, agent_name: str) -> None:
        """添加待处理移交。"""
        self.pending_handoffs.append(agent_name)

    def clear_loops(self) -> None:
        """清除循环计数器。仅在特定恢复路径调用。"""
        self.tool_loop_count = 0
        self.search_loop_count = 0

    @classmethod
    def from_state(cls, state: object) -> DecisionState:
        """从 RunLoopState 提取决策状态视图。"""
        evidence = getattr(state, "evidence_ledger", [])
        return cls(
            tool_loop_count=getattr(state, "tool_loop_count", 0) if hasattr(state, "tool_loop_count") else 0,
            search_loop_count=getattr(state, "search_loop_count", 0) if hasattr(state, "search_loop_count") else 0,
            execution_evidence=list(evidence) if evidence else [],
            recovery_mode=getattr(state, "recovery_mode", False) if hasattr(state, "recovery_mode") else False,
            overflow_recovery_used=getattr(state, "overflow_recovery_used", False) if hasattr(state, "overflow_recovery_used") else False,
        )
