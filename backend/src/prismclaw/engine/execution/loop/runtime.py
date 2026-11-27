"""LoopRuntime — 单次 run 的唯一状态容器。

prepare 阶段构造并填充「入参 / run 级状态」，``build_runtime`` 原地填充
「装配产物」。stage 函数以 ``rt: LoopRuntime`` 为第一参数显式读写，不依赖
隐式作用域，也不存在第二份 run 级上下文。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from prismclaw.engine.execution.control.kernel import CompletionKernel


@dataclass
class LoopRuntime:
    """单次 run 的唯一状态容器（纯数据，无运行期行为）。

    字段按生命周期分四组：
    - 入参：prepare 构造时确定
    - run 级状态：prepare 填充，stage 在 run 内读写
    - 装配产物：build_runtime 原地填充
    - 可变标志：run 级一次性
    """

    # ── 入参 ──
    deps: Any
    request: Any

    # ── prepare 填充的 run 级状态 ──
    recovery: Any = None
    state_machine: Any = None
    tool_loop_state: Any = None
    run_state: Any = None
    initial_task_spec: Any = None
    retry_counts: Any = None
    response_policy: Any = None
    pipeline: Any = None
    tool_ctx: Any = None
    tool_workspace_path: Any = None
    effective_model: str | None = None
    abort_event: Any = None
    run_deadline_s: float = 0.0
    run_started: float = 0.0
    task_runtime: Any = None
    snapshot: Any = None
    tree: Any = None
    traj_scope: Any = None
    attachment_guidance: str | None = None
    # 专家作用域：prepare 判定一次，run 内（工具面 / 归属门控 / 人格 / 团队校验）全部复用。
    # 无 environment catalog 的 headless 路径不构建，保持 None。
    session_scope: Any = None

    # run 级状态（prepare 填充，stage 在 run 内读写）
    hotpath: Any = None
    terminated_by: Any = None  # TerminalReason
    iteration: int = 0
    evidence_ledger: list[Any] = field(default_factory=list)
    latest_review_decision: Any = None
    llm_start_monotonic: float = 0.0
    perf: Any = None
    pending_messages: list[Any] = field(default_factory=list)
    active_skill_invocation: Any = None
    ownership_lease: Any = None
    profiler: Any = None

    # ── 装配产物（build_runtime 原地填充）──
    completion_config: Any = None
    completion_state: Any = None
    budget_guard: Any = None
    run_budget: Any = None
    completion_kernel: CompletionKernel | None = None
    terminal_messages: dict[str, str] = field(default_factory=dict)
    run_projector: Any = None

    # ── 可变标志（run 级一次性）──
    # 迭代预算警告（continue_.py 注入 user message 收敛指令）
    iteration_budget_warned: bool = False
    # 时间/硬墙钟预算警告（stream.py 在 is_last_turn 时注入 system prompt）
    time_budget_warned: bool = False
    # Token/上下文预算警告（stream.py 在 ratio≥warning_ratio 时注入 system prompt）
    token_budget_warned: bool = False
    # 三个 flag 独立：迭代先耗尽不阻断时间警告，反之亦然；token 警告
    # 基于上下文窗口占用率，与时间/迭代无耦合。
    # context_overflow 恢复只重试一次：True 表示已尝试过压缩恢复，
    # 再次 overflow 直接终态（避免无限压缩循环）。
    overflow_recovered: bool = False


# ---------------------------------------------------------------------------
# 装配 — LoopRuntime 的原地填充点
# ---------------------------------------------------------------------------

# 终态码 → 用户可见文案（terminate 的唯一文案源）
TERMINAL_MESSAGES: dict[str, str] = {
    "budget_iterations": "任务已用尽本轮预算（步数或工具调用上限）。",
    "budget_seconds": "任务已超出运行时上限（硬边界）。",
    "blocked_no_progress": "完成要求未满足且修复额度已用尽，任务终止。",
    "blocked_tools_denied": "工具调用被策略整体拒绝且重规划机会已用尽，任务终止。",
    "error": "恢复链已耗尽，完成控制无法继续。",
    "aborted": "run aborted",
}


def _artifact_candidates(rt: LoopRuntime) -> Callable[[], tuple[str, ...]] | None:
    """本次 run 的产物增量（session outbox 相对 run 起点）。

    ``Criterion(target=None)`` 需要「本次运行产出的文件」作为候选。仅靠工具
    入参推导 ``wrote_paths`` 会漏掉 bash / 脚本内部写文件的场景；这里对
    session outbox 做一次基线快照，判定时取增量绝对路径。
    """
    from prismclaw.engine.execution.control.artifacts import ArtifactScanner, Scope
    from prismclaw.infra.workspace_paths import ensure_session_layout

    session_id = getattr(rt.request, "session_id", "") or ""
    if not rt.tool_workspace_path or not session_id:
        return None
    session_root = ensure_session_layout(rt.tool_workspace_path, session_id)
    scope = Scope(kind="session", root=str(session_root), prefixes=("outbox",))
    scanner = ArtifactScanner(session_root)
    baseline = scanner.scan(scope)

    def _delta() -> tuple[str, ...]:
        root = Path(scope.root)
        return tuple(
            str(root / path)
            for path, signature in scanner.scan(scope).items()
            if baseline.get(path) != signature
        )

    return _delta


def build_runtime(rt: LoopRuntime) -> LoopRuntime:
    """装配 LoopRuntime：预算、完成内核、终态文案、责任投影器一次生成。

    这是**唯一知道完成控制子系统存在的地方**；stage 模块只消费 ``rt``
    的字段，不知道 CompletionKernel / BudgetGuard 如何构造。

    原地填充同一 ``rt`` 并返回它——全程只有一个对象，teardown 持有的
    引用与 loop 内看到的是同一份状态。
    """
    from prismclaw.engine.execution.control.artifacts import ArtifactScanner
    from prismclaw.engine.execution.control.completion import CompletionReviewer
    from prismclaw.engine.execution.control.kernel import (
        Budget,
        BudgetGuard,
        CompletionKernel,
        CompletionState,
        resolve_run_budget,
    )
    from prismclaw.engine.execution.projection.project import RunProjector
    from prismclaw.engine.execution.query.pre_request_pipeline import PreRequestPipeline

    if rt.pipeline is None:
        rt.pipeline = PreRequestPipeline()
    if rt.tool_loop_state is None:
        rt.tool_loop_state = rt.state_machine.load_tool_loop_state()
    if rt.retry_counts is None:
        rt.retry_counts = rt.state_machine.retry_counts_view()

    rt.completion_config = rt.deps.config.control
    rt.completion_state = CompletionState(contract=None, contract_resolved=False)
    rt.run_budget = resolve_run_budget(config=rt.deps.config.budget)
    rt.run_state.loop_state.soft_at = float(rt.run_started) + rt.run_budget.soft_s
    rt.run_state.loop_state.hard_at = float(rt.run_started) + rt.run_budget.hard_s
    rt.budget_guard = BudgetGuard(
        Budget(
            max_iterations=rt.run_budget.steps,
            run_deadline_s=float(rt.run_deadline_s or 0.0),
            run_started=float(rt.run_started or 0.0),
        )
    )
    rt.completion_kernel = CompletionKernel(
        state=rt.completion_state,
        reviewer=CompletionReviewer(
            ArtifactScanner(rt.tool_workspace_path),
            artifact_candidates=_artifact_candidates(rt),
        ),
        budget=rt.budget_guard,
        recovery=rt.recovery,
    )
    rt.terminal_messages = TERMINAL_MESSAGES
    rt.run_projector = RunProjector(label=getattr(rt.request, "task_id", "") or "run")
    return rt
