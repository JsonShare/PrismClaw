"""LoopRuntime — stage 函数的显式依赖容器。

各 stage 函数以 ``rt: LoopRuntime`` 为第一参数，显式读写受控字段，不依赖
隐式作用域。不是框架：字段 + 一个装配函数，无运行期行为。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LoopRuntime:
    """stage 函数的显式依赖容器。

    字段按来源分三组：
    - 入参：build_loop_config 的形参
    - 装配产物：completion_kernel / budget_guard 等构造期一次生成
    - 可变标志：iteration_budget_warned / time_budget_warned / overflow_recovered（run 级一次性）
    """

    # ── 入参 ──
    deps: Any
    request: Any
    recovery: Any
    state_machine: Any
    tool_loop_state: Any
    run_state: Any
    initial_task_spec: Any
    retry_counts: Any
    response_policy: Any
    pipeline: Any = None
    tool_ctx: Any = None
    tool_workspace_path: Any = None
    effective_model: str | None = None
    abort_event: Any = None
    run_deadline_s: float = 0.0
    run_started: float = 0.0
    mode: str = "standard"
    task_runtime: Any = None
    snapshot: Any = None
    tree: Any = None
    traj_scope: Any = None
    attachment_guidance: str | None = None

    # ── 装配产物 ──
    completion_config: Any = None
    completion_state: Any = None
    budget_guard: Any = None
    effort_level: str = "standard"
    effort_budget: Any = None
    completion_kernel: Any = None
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
# 装配 — LoopRuntime 的构造点
# ---------------------------------------------------------------------------

# 终态码 → 用户可见文案（terminate 的唯一文案源）
TERMINAL_MESSAGES: dict[str, str] = {
    "budget_iterations": "任务已用尽迭代预算（硬边界）。",
    "budget_seconds": "任务已超出运行时上限（硬边界）。",
    "blocked_no_progress": "完成要求未满足且修复额度已用尽，任务终止。",
    "blocked_tools_denied": "工具调用被策略整体拒绝且重规划机会已用尽，任务终止。",
    "error": "恢复链已耗尽，完成控制无法继续。",
    "aborted": "run aborted",
}


def build_runtime(
    deps: Any,
    request: Any,
    recovery: Any,
    state_machine: Any,
    tool_loop_state: Any,
    run_state: Any,
    initial_task_spec: Any,
    retry_counts: Any,
    response_policy: Any,
    *,
    pipeline: Any = None,
    tool_ctx: Any = None,
    tool_workspace_path: Any = None,
    effective_model: str | None = None,
    abort_event: Any = None,
    run_deadline_s: float = 0.0,
    run_started: float = 0.0,
    mode: str = "standard",
    task_runtime: Any = None,
    snapshot: Any = None,
    tree: Any = None,
    traj_scope: Any = None,
    attachment_guidance: str | None = None,
) -> LoopRuntime:
    """装配 LoopRuntime：预算、完成内核、终态文案、责任投影器一次生成。

    这是**唯一知道完成控制子系统存在的地方**；stage 模块只消费 ``rt``
    的字段，不知道 CompletionKernel / BudgetGuard 如何构造。
    """
    from prismclaw.engine.execution.control.artifacts import ArtifactScanner
    from prismclaw.engine.execution.control.completion import CompletionReviewer
    from prismclaw.engine.execution.control.kernel import (
        Budget,
        BudgetGuard,
        CompletionKernel,
        CompletionState,
        resolve_effort_budget,
    )
    from prismclaw.engine.execution.projection.project import RunProjector

    completion_config = deps.config.control
    completion_state = CompletionState(contract=None, contract_resolved=False)
    effort_level = getattr(request, "effort", "standard") or "standard"
    effort_budget = resolve_effort_budget(
        effort=effort_level,
        long_running=bool(getattr(request, "long_running", False)),
        tiers=deps.config.effort,
    )
    run_state.loop_state.soft_at = float(run_started) + effort_budget.soft_s
    run_state.loop_state.hard_at = float(run_started) + effort_budget.hard_s
    budget_guard = BudgetGuard(
        Budget(
            max_iterations=effort_budget.steps,
            run_deadline_s=float(run_deadline_s or 0.0),
            run_started=float(run_started or 0.0),
        )
    )

    return LoopRuntime(
        deps=deps,
        request=request,
        recovery=recovery,
        state_machine=state_machine,
        tool_loop_state=tool_loop_state,
        run_state=run_state,
        initial_task_spec=initial_task_spec,
        retry_counts=retry_counts,
        response_policy=response_policy,
        pipeline=pipeline,
        tool_ctx=tool_ctx,
        tool_workspace_path=tool_workspace_path,
        effective_model=effective_model,
        abort_event=abort_event,
        run_deadline_s=run_deadline_s,
        run_started=run_started,
        mode=mode,
        task_runtime=task_runtime,
        snapshot=snapshot,
        tree=tree,
        traj_scope=traj_scope,
        attachment_guidance=attachment_guidance,
        completion_config=completion_config,
        completion_state=completion_state,
        budget_guard=budget_guard,
        effort_level=effort_level,
        effort_budget=effort_budget,
        completion_kernel=CompletionKernel(
            state=completion_state,
            reviewer=CompletionReviewer(ArtifactScanner(tool_workspace_path)),
            budget=budget_guard,
            recovery=recovery,
        ),
        terminal_messages=TERMINAL_MESSAGES,
        run_projector=RunProjector(label=getattr(request, "task_id", "") or "run"),
    )
