"""run 结束后的清理（teardown）。

职责：遥测输出、skill invocation 清理、ownership 释放、session 运行时
指标日志、hooks 通知。由 runner 在 finally 段调用。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from prismclaw.engine.compression.preservation import cleanup_preservation_runtime_on_terminal_exit
from prismclaw.engine.execution.control.kernel import TerminalReason
from prismclaw.engine.execution.loop.arbiter import RunContext
from prismclaw.engine.execution.loop.housekeeping import _log_exec_phase
from prismclaw.engine.execution.loop.ownership import _release_run_ownership, _RunOwnershipLease
from prismclaw.engine.execution.query._turn_helpers import (
    log_session_runtime_metrics,
    log_session_success_verdict,
)
from prismclaw.engine.foundation.metrics import emit
from prismclaw.engine.hooks import HookEvent, _log_hook_outcome
from prismclaw.engine.security.gate.constraint_guard import detect_constraint_breaches

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TeardownParams + run_teardown: finally 清理段
# ---------------------------------------------------------------------------

@dataclass
class TeardownParams:
    """run_agent_stream finally 段需要的参数集合。

    因为 finally 段访问了 15+ 个局部变量，将其打包为 dataclass
    避免参数爆炸。这些变量在 try 块的不同退出路径上可能未定义，
    因此所有字段都有默认值。
    """

    ctx: RunContext | None = None
    iteration: int | None = None
    llm_start: float = 0.0
    terminated_by: TerminalReason = TerminalReason.DONE
    initial_task_spec: Any = None
    latest_review_decision: Any = None
    tree: Any = None
    snapshot: Any = None
    tool_ctx: Any = None
    ownership_lease: _RunOwnershipLease | None = None
    profiler: Any = None
    # 回合级计时聚合（TurnPerf）+ 自治层恢复链快照（度量用，可为 None/空）
    perf: Any = None
    recovery_chain: list[str] = field(default_factory=list)
    active_skill_invocation: Any = None


async def run_teardown(
    params: TeardownParams,
    *,
    deps: Any,
    request: Any,
) -> None:
    """run_agent_stream 的 finally 清理逻辑。

    1. 遥测输出（R2 drift/goal/constraint/iteration 指标）
    2. Skill invocation 清理
    3. Ownership 释放
    4. Session 运行时指标日志
    5. Hooks 通知（SESSION_END）
    """
    ctx = params.ctx
    if ctx is not None:
        # Sync terminated_by from ctx (terminal paths write to ctx).
        params.terminated_by = ctx.terminated_by or params.terminated_by

    _phase_iter = params.iteration
    try:
        _phase_elapsed = time.monotonic() - params.llm_start
    except Exception:
        _phase_elapsed = 0.0

    _log_exec_phase(
        conv_id=request.session_id,
        phase="done",
        iteration=_phase_iter,
        elapsed_s=_phase_elapsed,
    )

    if params.profiler is not None:
        params.profiler.log_summary(status=params.terminated_by)

    # ── 回合级性能汇总（单行；用于回答"咨询为什么慢/发散"）──
    # wall_ms        回合总墙钟
    # llm/tools/prep 三段主耗时（TurnPerf 在计时当处累加 —— 纯文本回合
    #                不经过 loop 底部 bookkeeping，LoopTiming 对它无读数）
    # loop_self_ms   = wall - 三段，含内核/治理/hook/落树等 loop 自身开销
    # iterations     经过的迭代数（延迟与发散的放大器）
    # recovery_n     自治层恢复链长度；>0 表示 restart/recovery/spiral 真的触发了
    _perf = params.perf
    if _perf is not None:
        logger.info(
            "turn_perf session=%s mode=%s model=%s steps=%d "
            "tool_calls=%d wall_ms=%.0f llm_ms=%.0f tools_ms=%.0f prep_ms=%.0f "
            "loop_self_ms=%.0f recovery_n=%d recovery=%s terminated_by=%s",
            request.session_id,
            getattr(request, "mode_override", None) or "chat",
            getattr(request, "model", None) or "-",
            _perf.llm_calls,
            _perf.tool_calls,
            _perf.wall_ms(),
            _perf.llm_ms,
            _perf.tools_ms,
            _perf.prep_ms,
            _perf.loop_self_ms(),
            len(params.recovery_chain),
            ",".join(params.recovery_chain) or "-",
            params.terminated_by,
        )

    # ── R2 遥测 ──
    _r2_goal_unachieved = 1.0 if params.terminated_by != TerminalReason.DONE else 0.0
    emit("turn.goal.unachieved", _r2_goal_unachieved, kind="gauge")

    _r2_breaches = detect_constraint_breaches(
        task_spec=params.initial_task_spec,
        review_decision=params.latest_review_decision,
    )
    emit("turn.constraint.breach", float(len(_r2_breaches)), kind="gauge")

    emit("turn.loop.outcome", 1.0, kind="counter", labels={"reason_code": params.terminated_by})
    _r2_steps = float(_perf.llm_calls) if _perf is not None else 0.0
    emit("turn.loop.steps", _r2_steps, kind="histogram")

    logger.info(
        "R2 turn-loop telemetry: iterations=%s goal_unachieved=%s constraint_breach=%s terminated_by=%s",
        _phase_iter,
        bool(_r2_goal_unachieved), len(_r2_breaches), params.terminated_by,
    )

    # ── Skill invocation 清理 ──
    params.active_skill_invocation = await cleanup_preservation_runtime_on_terminal_exit(
        session_store=deps.session_store,
        run_snapshot_store=deps.persistence.run_snapshot_store,
        request=request,
        tree=params.tree,
        snapshot=params.snapshot,
        tool_ctx=params.tool_ctx,
        ports=deps.ports,
    )

    # ── Ownership 释放 ──
    await _release_run_ownership(params.ownership_lease, deps)

    # ── Session 运行时指标日志 ──
    tree = params.tree
    if tree is not None and tree.get_turn_runtime_metrics_snapshots():
        turn_metrics_snapshots = tree.get_turn_runtime_metrics_snapshots()
        log_session_runtime_metrics(
            logger_=logger,
            turn_metrics_snapshots=turn_metrics_snapshots,
            history_compaction_count=tree.get_history_compaction_count(),
        )
        log_session_success_verdict(
            logger_=logger,
            turn_metrics_snapshots=turn_metrics_snapshots,
        )

    # ── Hooks 通知 ──
    await deps.hooks.notify_run_end(request.session_id, params.terminated_by)
    if deps.hook_dispatcher is not None:
        hook_result = await deps.hook_dispatcher.dispatch(
            HookEvent.SESSION_END,
            {
                "session_id": request.session_id,
                "workspace_id": request.workspace_id,
                "agent_id": request.agent_id,
                "terminated_by": params.terminated_by,
            },
        )
        _log_hook_outcome(HookEvent.SESSION_END, hook_result)
