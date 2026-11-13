"""trajectory 生命周期：bus + auditor 初始化 → teardown → iteration 追踪。

职责：SessionBus 创建、auditor 安装、teardown 闭包注册、
SessionTurnStarted/Finished 与 iteration 起止事件发布。
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from prismclaw.engine.execution.control.kernel import TerminalReason

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TrajectoryScope: trajectory 生命周期
# ---------------------------------------------------------------------------

@dataclass
class TrajectoryScope:
    """trajectory 生命周期：bus + auditor 初始化 → teardown → iteration 追踪。

    封装 trajectory 的四段职责：早期初始化、主初始化（teardown 闭包 +
    SessionTurnStarted）、async with 入口、循环内 iteration 起止发布。

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
    uncommitted_iter: int | None = None
    # 跨闭包传递 turn-end reason（由 runner 在 run_loop 结束后写入，teardown 时读取）
    terminated_by: TerminalReason | None = None
    _auditor_svc: Any = None
    _stack: AsyncExitStack = field(default_factory=AsyncExitStack)

    # ── 主初始化：noop fallback + 3 个闭包 + SessionTurnStarted ──

    def setup_main(self, request: Any) -> None:
        """注册 teardown 闭包 + 发布 SessionTurnStarted。

        应在早期初始化之后、进入主循环之前调用。
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
            from prismclaw.engine.trajectory.bus_context import (
                TrajectoryRuntimeCtx,
                trajectory_runtime,
            )
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
                scope=self,
            ) -> None:
                # Commit any uncommitted last iter before turn ends.
                _last_iter = scope.uncommitted_iter
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
                    scope.uncommitted_iter = None
                # Publish SessionTurnFinished (带 terminated_by)
                try:
                    from prismclaw.engine.trajectory.bus_events import SessionTurnFinished
                    bus_ref.publish(SessionTurnFinished(
                        session_id=sid, turn_counter=tc,
                        finished_at_ms=int(time.time() * 1000),
                        terminated_by=scope.terminated_by,
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

        在 while 循环体开头调用。
        """
        try:
            from prismclaw.engine.trajectory.bus import current_bus
            from prismclaw.engine.trajectory.bus_context import (
                TrajectoryRuntimeCtx,
                trajectory_runtime,
            )
            from prismclaw.engine.trajectory.bus_events import (
                SessionIterationFinished,
                SessionIterationStarted,
            )

            # Lazy-commit the PREVIOUS iter if it exited via an early-return path.
            _prev_uncommitted = self.uncommitted_iter
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
                self.uncommitted_iter = None

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
            self.uncommitted_iter = iteration
        except Exception:
            pass

    def publish_iteration_end(self, session_id: str, iteration: int) -> None:
        """发布 SessionIterationFinished + 清除 uncommitted 标记。

        在 while 循环体末尾调用。
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
            self.uncommitted_iter = None
        except Exception:
            pass


def setup_trajectory_early(tree: Any, request: Any, deps: Any) -> TrajectoryScope:
    """早期初始化 trajectory bus + auditor。

    MUST be called BEFORE _append() so tree_writer._append()'s current_bus()
    returns the real bus and ConversationNodeAdded events are published for
    user messages.

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
        from prismclaw.engine.trajectory import install_auditor_if_enabled
        from prismclaw.engine.trajectory.bus import SessionBus

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
