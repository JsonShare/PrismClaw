"""SessionManager — 会话生命周期 + 持久化 + 清理。

从 run_agent_stream 的三个段落逐步抽取：
1. finally 清理段（L2470-2575）— 最独立，无循环依赖 ✅
2. trajectory 生命周期（L555-637, L1317-1459, 循环内 iteration 追踪）— ✅
3. 会话准备段（L447-1209）— ensure_session / snapshot / recovery / hooks / confirmation

职责：
- 会话生命周期：ensure_session、snapshot 加载、recovery 处理
- 持久化：run snapshot 持久化、partial assistant 持久化、task-block 标记
- 清理：run 结束后的遥测、ownership 释放、hooks 通知、skill 清理
- trajectory：bus/auditor 初始化、teardown、iteration 追踪
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from prismclaw.engine.execution.loop.arbiter import RunContext
from prismclaw.engine.execution.control.kernel import TerminalReason
from prismclaw.engine.execution.loop.ownership import _RunOwnershipLease, _release_run_ownership
from prismclaw.engine.security.gate.constraint_guard import detect_constraint_breaches
from prismclaw.engine.execution.loop.housekeeping import _log_exec_phase
from prismclaw.engine.compression.preservation import cleanup_preservation_runtime_on_terminal_exit
from prismclaw.engine.execution.query.context_helpers import _post_baseline_stable_prefix_follow_up_turn_count
from prismclaw.engine.execution.query.turn_preparation import (
    log_session_runtime_metrics,
    log_session_success_verdict,
)
from prismclaw.engine.hooks import HookEvent, _log_hook_outcome
from prismclaw.engine.foundation.metrics import emit

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
    run_state: Any = None
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

    对应原 runner.py L2470-2575。从 SessionManager 调用，
    确保 run 结束后的所有清理工作正确执行：
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
            "turn_perf session=%s mode=%s model=%s iterations=%d llm_calls=%d "
            "tool_calls=%d wall_ms=%.0f llm_ms=%.0f tools_ms=%.0f prep_ms=%.0f "
            "loop_self_ms=%.0f recovery_n=%d recovery=%s terminated_by=%s",
            request.session_id,
            getattr(request, "mode_override", None) or "chat",
            getattr(request, "model", None) or "-",
            _perf.iterations,
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
    try:
        _r2_iterations = float(params.run_state.iteration) if params.run_state is not None else 0.0
    except (NameError, UnboundLocalError):
        _r2_iterations = float(_phase_iter or 0)
    emit("turn.loop.iterations", _r2_iterations, kind="histogram")

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
        stable_prefix_follow_up_turns = _post_baseline_stable_prefix_follow_up_turn_count(tree)
        log_session_runtime_metrics(
            logger_=logger,
            turn_metrics_snapshots=turn_metrics_snapshots,
            history_compaction_count=tree.get_history_compaction_count(),
            stable_prefix_follow_up_turns=stable_prefix_follow_up_turns,
            stable_prefix_reuse_turns=tree.get_stable_prefix_reuse_turn_count(),
            stable_prefix_digest_changes=tree.get_stable_prefix_digest_change_count(),
        )
        log_session_success_verdict(
            logger_=logger,
            turn_metrics_snapshots=turn_metrics_snapshots,
            stable_prefix_follow_up_turns=stable_prefix_follow_up_turns,
            stable_prefix_reuse_turns=tree.get_stable_prefix_reuse_turn_count(),
            stable_prefix_digest_changes=tree.get_stable_prefix_digest_change_count(),
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


# ---------------------------------------------------------------------------
# TrajectoryScope: trajectory 生命周期
# ---------------------------------------------------------------------------

@dataclass
class TrajectoryScope:
    """trajectory 生命周期：bus + auditor 初始化 → teardown → iteration 追踪。

    封装了原 runner.py 中 5 段散布的 trajectory 代码：
    - 早期初始化（L555-637）：SessionBus 创建 + auditor 安装
    - 主初始化（L1317-1459）：noop fallback + 3 个闭包 + SessionTurnStarted
    - async with 入口（L1466）：AsyncExitStack 管理
    - 循环内 iteration-start 发布（L1488-1537）
    - 循环内 iteration-end 发布（L2443-2472）

    用法::

        scope = setup_trajectory_early(tree, request, deps)
        scope.setup_main(request)
        async with scope:
            while True:
                scope.publish_iteration_start(session_id, iteration)
                ...
                scope.publish_iteration_end(session_id, iteration)
    """

    bus: Any  # SessionBus
    turn_counter: int
    uncommitted_iter: list[int | None] = field(default_factory=lambda: [None])
    # 跨闭包传递 turn-end reason（由 runner 在 run_loop 结束后写入，teardown 时读取）
    _terminated_by_cell: list[TerminalReason | None] = field(default_factory=lambda: [None])
    _auditor_svc: Any = None
    _stack: AsyncExitStack = field(default_factory=AsyncExitStack)

    # ── 主初始化：noop fallback + 3 个闭包 + SessionTurnStarted ──

    def setup_main(self, request: Any) -> None:
        """注册 teardown 闭包 + 发布 SessionTurnStarted。

        对应原 runner.py L1317-1459。应在早期初始化之后、
        进入主循环之前调用。
        """
        # Noop fallback: if early setup failed, bus is already noop.
        if self.bus is None:
            from prismclaw.engine.trajectory.bus import SessionBus
            self.bus = SessionBus(is_noop=True)

        try:
            # 1) Async teardown: close auditor + drain bus
            async def _traj_async_teardown(
                svc_ref=self._auditor_svc,
                bus_ref=self.bus,
            ) -> None:
                try:
                    if svc_ref is not None:
                        await svc_ref.close(force_flush=True)
                except Exception as _te:
                    logger.debug("trajectory: service.close teardown failed: %s", _te)
                try:
                    await bus_ref.wait_drained(timeout_s=3.0)
                except Exception as _te:
                    logger.debug("trajectory: wait_drained teardown failed: %s", _te)

            self._stack.push_async_callback(_traj_async_teardown)

            # 2) Set session-scoped identity via ContextVar
            from prismclaw.engine.trajectory.bus_context import trajectory_runtime, TrajectoryRuntimeCtx
            _runtime_reset_token = trajectory_runtime.set(
                TrajectoryRuntimeCtx(
                    session_id=request.session_id,
                    turn=self.turn_counter,
                    iteration=0,
                ),
            )

            def _reset_runtime_tok(_tok=_runtime_reset_token) -> None:
                if _tok is not None:
                    from prismclaw.engine.trajectory.bus_context import trajectory_runtime
                    try:
                        trajectory_runtime.reset(_tok)
                    except ValueError:
                        # 同 bus.py _BusCtxManager.__exit__ — 跨 async context
                        # 关闭时 token 无法 reset，让 context 自己销毁清理。
                        pass

            self._stack.callback(_reset_runtime_tok)

            # 3) Publish SessionTurnStarted
            try:
                from prismclaw.engine.trajectory.bus_events import SessionTurnStarted
                self.bus.publish(SessionTurnStarted(
                    session_id=request.session_id,
                    turn_counter=self.turn_counter,
                    started_at_ms=int(time.time() * 1000),
                ))
            except Exception:
                pass

            # 4) Register turn-finished sync callback (LIFO: pushed last = first run)
            def _turn_finished_sync(
                sid=request.session_id,
                tc=self.turn_counter,
                bus_ref=self.bus,
                cell=self.uncommitted_iter,
                tb_cell=self._terminated_by_cell,
            ) -> None:
                # Commit any uncommitted last iter before turn ends.
                _last_iter = cell[0]
                if _last_iter is not None:
                    try:
                        from prismclaw.engine.trajectory.bus_events import SessionIterationFinished
                        bus_ref.publish(SessionIterationFinished(
                            session_id=sid, turn_counter=tc,
                            iteration=_last_iter,
                            finished_at_ms=int(time.time() * 1000),
                        ))
                    except Exception:
                        pass
                    cell[0] = None
                # Publish SessionTurnFinished (带 terminated_by)
                try:
                    from prismclaw.engine.trajectory.bus_events import SessionTurnFinished
                    bus_ref.publish(SessionTurnFinished(
                        session_id=sid, turn_counter=tc,
                        finished_at_ms=int(time.time() * 1000),
                        terminated_by=tb_cell[0],
                    ))
                except Exception:
                    pass

            self._stack.callback(_turn_finished_sync)

        except Exception as _exc:
            logger.debug("trajectory: bus + auditor init skipped (disabled?): %s", _exc)

    # ── Async context manager ──

    async def __aenter__(self) -> TrajectoryScope:
        await self._stack.__aenter__()
        return self

    async def __aexit__(self, *exc_info: Any) -> Any:
        return await self._stack.__aexit__(*exc_info)

    # ── Iteration 追踪 ──

    def publish_iteration_start(self, session_id: str, iteration: int) -> None:
        """发布 SessionIterationStarted + 更新 ContextVar + lazy-commit 前一轮。

        对应原 runner.py L1488-1537。在 while 循环体开头调用。
        """
        try:
            from prismclaw.engine.trajectory.bus import current_bus
            from prismclaw.engine.trajectory.bus_events import SessionIterationFinished, SessionIterationStarted
            from prismclaw.engine.trajectory.bus_context import trajectory_runtime, TrajectoryRuntimeCtx

            # Lazy-commit the PREVIOUS iter if it exited via an early-return path.
            _prev_uncommitted = self.uncommitted_iter[0]
            if _prev_uncommitted is not None:
                try:
                    _bus_lazy = current_bus()
                    _bus_lazy.publish(SessionIterationFinished(
                        session_id=session_id,
                        turn_counter=self.turn_counter,
                        iteration=_prev_uncommitted,
                        finished_at_ms=int(time.time() * 1000),
                    ))
                except Exception:
                    pass
                self.uncommitted_iter[0] = None

            # Update identity context var (fresh mutable ctx per iteration).
            trajectory_runtime.set(TrajectoryRuntimeCtx(
                session_id=session_id,
                turn=self.turn_counter,
                iteration=iteration,
            ))

            _bus_iter = current_bus()
            _bus_iter.publish(SessionIterationStarted(
                session_id=session_id,
                turn_counter=self.turn_counter,
                iteration=iteration,
                started_at_ms=int(time.time() * 1000),
            ))
            # Mark this iter as uncommitted.
            self.uncommitted_iter[0] = iteration
        except Exception:
            pass

    def publish_iteration_end(self, session_id: str, iteration: int) -> None:
        """发布 SessionIterationFinished + 清除 uncommitted 标记。

        对应原 runner.py L2443-2472。在 while 循环体末尾调用。
        """
        try:
            from prismclaw.engine.trajectory.bus import current_bus
            from prismclaw.engine.trajectory.bus_events import SessionIterationFinished

            current_bus().publish(SessionIterationFinished(
                session_id=session_id,
                turn_counter=self.turn_counter,
                iteration=iteration,
                finished_at_ms=int(time.time() * 1000),
            ))
            # Clear the uncommitted marker — happy-path commit done.
            self.uncommitted_iter[0] = None
        except Exception:
            pass


def setup_trajectory_early(tree: Any, request: Any, deps: Any) -> TrajectoryScope:
    """早期初始化 trajectory bus + auditor。

    对应原 runner.py L555-637。MUST be called BEFORE _append() so
    bootstrap._append()'s current_bus() returns the real bus and
    ConversationNodeAdded events are published for user messages.

    Returns a TrajectoryScope whose bus/auditor are ready for use.
    If early setup fails, bus stays None and the caller should pass the
    scope to setup_main() which will create a noop fallback.
    """
    _auditor_svc = None
    _bus = None

    # Derive turn_counter (user-message count + 1)
    _traj_turn_counter: int = 1
    try:
        _tc_raw = getattr(tree, "turn_counter", None)
        if isinstance(_tc_raw, int):
            _traj_turn_counter = int(_tc_raw) & 0xFFFFFFFF
        else:
            _entries = getattr(tree, "entries", {}) or {}
            _order = getattr(tree, "order", ()) or ()
            _user_count = sum(
                1 for _eid in _order
                if (_entries.get(_eid) is not None
                    and getattr(_entries.get(_eid), "role", None) == "user")
            )
            _traj_turn_counter = (_user_count + 1) & 0xFFFFFFFF
    except Exception:
        _traj_turn_counter = (abs(hash(request.request_id)) & 0xFFFFFFFF) or 1

    try:
        from prismclaw.engine.trajectory.bus import SessionBus
        from prismclaw.engine.trajectory import install_auditor_if_enabled

        _bus = SessionBus()
        _stack = AsyncExitStack()
        _stack.enter_context(_bus.bind())

        # Resolve trajectory_persistence from deps.persistence
        _traj_persistence = None
        try:
            _traj_persistence = deps.persistence.trajectory_persistence if deps.persistence is not None else None
        except Exception:
            logger.debug("trajectory: failed to resolve trajectory_persistence from persistence bundle")

        _traj_header = None
        try:
            _traj_header = getattr(request, "trajectory_audit_header", None)
        except Exception:
            _traj_header = None

        _auditor_svc = install_auditor_if_enabled(
            session_id=request.session_id,
            bus=_bus,
            persistence=_traj_persistence,
            header=_traj_header,
            tool_registry=deps.tools,
        )
        logger.warning(
            "trajectory audit setup (early): sid=%s header=%s persistence=%s svc_created=%s",
            request.session_id, _traj_header,
            "YES" if _traj_persistence is not None else "NO",
            _auditor_svc is not None,
        )

        # Pre-register handlers synchronously so subscriptions are in
        # place before _append() publishes ConversationNodeAdded.
        if _auditor_svc is not None:
            _auditor_svc.preregister_handlers()
            # Schedule async start() (restore_from_db + flush_worker)
            try:
                _traj_loop = asyncio.get_running_loop()
                _svc_start_task = _traj_loop.create_task(
                    _auditor_svc.start(restore_from_db=True),
                )
                _bus._in_flight.add(_svc_start_task)
                _svc_start_task.add_done_callback(_bus._in_flight.discard)
            except RuntimeError:
                pass

        return TrajectoryScope(
            bus=_bus,
            turn_counter=_traj_turn_counter,
            _auditor_svc=_auditor_svc,
            _stack=_stack,
        )

    except Exception:
        # If early setup fails, fall back to None bus; setup_main()
        # will create a noop fallback.
        logger.debug("trajectory: early setup failed, returning scope with bus=None")
        return TrajectoryScope(
            bus=None,
            turn_counter=_traj_turn_counter,
        )
