"""run 结束后的清理（teardown）。

职责：遥测输出、skill invocation 清理、ownership 释放、session 运行时
指标日志、hooks 通知。由 runner 在 finally 段调用。

唯一入参是 ``LoopRuntime``：teardown 读到的就是 loop 内写过的同一份状态，
不存在第二份容器，也不需要任何回填。
"""

from __future__ import annotations

import logging
import time

from prismclaw.engine.compression.preservation import cleanup_preservation_runtime_on_terminal_exit
from prismclaw.engine.execution.control.kernel import TerminalReason
from prismclaw.engine.execution.loop.housekeeping import _log_exec_phase
from prismclaw.engine.execution.loop.ownership import _release_run_ownership
from prismclaw.engine.execution.loop.runtime import LoopRuntime
from prismclaw.engine.execution.query._turn_helpers import (
    log_session_runtime_metrics,
    log_session_success_verdict,
)
from prismclaw.engine.foundation.metrics import emit
from prismclaw.engine.hooks import HookEvent, _log_hook_outcome
from prismclaw.engine.security.gate.constraint_guard import detect_constraint_breaches
from prismclaw.schema.session.projection import SessionProjectionEngine

logger = logging.getLogger(__name__)


async def run_teardown(rt: LoopRuntime) -> None:
    """run_agent_stream 的 finally 清理逻辑。

    1. 遥测输出（R2 drift/goal/constraint/iteration 指标）
    2. Skill invocation 清理
    3. Ownership 释放（finally 保证不被前置异常跳过）
    4. Session 运行时指标日志
    5. Hooks 通知（SESSION_END）
    """
    deps = rt.deps
    request = rt.request
    try:
        _phase_elapsed = time.monotonic() - rt.llm_start_monotonic
    except Exception:
        _phase_elapsed = 0.0

    try:
        _log_exec_phase(
            conv_id=request.session_id,
            phase="done",
            iteration=rt.iteration,
            elapsed_s=_phase_elapsed,
        )

        if rt.profiler is not None:
            rt.profiler.log_summary(status=rt.terminated_by)

        # ── 回合级性能汇总（单行；用于回答"咨询为什么慢/发散"）──
        # wall_ms        回合总墙钟
        # llm/tools/prep 三段主耗时（TurnPerf 在计时当处累加 —— 纯文本回合
        #                不经过 loop 底部 bookkeeping，LoopTiming 对它无读数）
        # loop_self_ms   = wall - 三段，含内核/治理/hook/落树等 loop 自身开销
        # iterations     经过的迭代数（延迟与发散的放大器）
        # recovery_n     自治层恢复链长度；>0 表示 restart/recovery/spiral 真的触发了
        _perf = rt.perf
        if _perf is not None:
            _recovery_chain = list(getattr(rt.recovery, "chain", None) or [])
            logger.info(
                "turn_perf session=%s model=%s steps=%d "
                "tool_calls=%d wall_ms=%.0f llm_ms=%.0f tools_ms=%.0f prep_ms=%.0f "
                "loop_self_ms=%.0f recovery_n=%d recovery=%s terminated_by=%s",
                request.session_id,
                getattr(request, "model", None) or "-",
                _perf.llm_calls,
                _perf.tool_calls,
                _perf.wall_ms(),
                _perf.llm_ms,
                _perf.tools_ms,
                _perf.prep_ms,
                _perf.loop_self_ms(),
                len(_recovery_chain),
                ",".join(_recovery_chain) or "-",
                rt.terminated_by,
            )

        # ── R2 遥测 ──
        _r2_goal_unachieved = 1.0 if rt.terminated_by != TerminalReason.DONE else 0.0
        emit("turn.goal.unachieved", _r2_goal_unachieved, kind="gauge")

        _r2_breaches = detect_constraint_breaches(
            task_spec=rt.initial_task_spec,
            review_decision=rt.latest_review_decision,
        )
        emit("turn.constraint.breach", float(len(_r2_breaches)), kind="gauge")

        emit("turn.loop.outcome", 1.0, kind="counter", labels={"reason_code": rt.terminated_by})
        _r2_steps = float(_perf.llm_calls) if _perf is not None else 0.0
        emit("turn.loop.steps", _r2_steps, kind="histogram")

        logger.info(
            (
                "R2 turn-loop telemetry: iterations=%s "
                "goal_unachieved=%s constraint_breach=%s terminated_by=%s"
            ),
            rt.iteration,
            bool(_r2_goal_unachieved),
            len(_r2_breaches),
            rt.terminated_by,
        )

        # ── Skill invocation 清理 ──
        rt.active_skill_invocation = await cleanup_preservation_runtime_on_terminal_exit(
            session_store=deps.session_store,
            run_snapshot_store=deps.persistence.run_snapshot_store,
            request=request,
            tree=rt.tree,
            snapshot=rt.snapshot,
            tool_ctx=rt.tool_ctx,
            ports=deps.ports,
        )

        # ── Session 运行时指标日志 ──
        tree = rt.tree
        engine = SessionProjectionEngine(tree) if tree is not None else None
        if engine is not None and engine.cache.get_turn_metrics_snapshots():
            turn_metrics_snapshots = engine.cache.get_turn_metrics_snapshots()
            log_session_runtime_metrics(
                logger_=logger,
                turn_metrics_snapshots=turn_metrics_snapshots,
                history_compaction_count=engine.history_compaction.get_count(),
            )
            log_session_success_verdict(
                logger_=logger,
                turn_metrics_snapshots=turn_metrics_snapshots,
            )
    finally:
        # Ownership 释放必须无条件执行：前置遥测/技能清理异常时，
        # 漏放会让 heartbeat task 与 ownership 记录永久残留。
        await _release_run_ownership(rt.ownership_lease, deps)

    # ── Hooks 通知 ──
    await deps.hooks.notify_run_end(request.session_id, rt.terminated_by)
    if deps.hook_dispatcher is not None:
        hook_result = await deps.hook_dispatcher.dispatch(
            HookEvent.SESSION_END,
            {
                "session_id": request.session_id,
                "workspace_id": request.workspace_id,
                "agent_id": request.agent_id,
                "terminated_by": rt.terminated_by,
            },
        )
        _log_hook_outcome(HookEvent.SESSION_END, hook_result)
