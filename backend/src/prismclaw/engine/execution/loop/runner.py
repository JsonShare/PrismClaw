from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from prismclaw.engine.execution.control.kernel import TerminalReason
from prismclaw.engine.execution.loop.agent_loop import run_loop
from prismclaw.engine.execution.loop.deps import (
    AgentRunnerDeps,
)
from prismclaw.engine.execution.loop.preparer import (
    EarlyExit,
    RunPreparer,
)
from prismclaw.engine.execution.loop.teardown import run_teardown
from prismclaw.engine.execution.loop.types import IterationFrame, RunLedger, RunLoopState
from prismclaw.engine.execution.loop.wiring import build_loop_config
from prismclaw.schema.agent.run_control_types import RunControl
from prismclaw.schema.agent.types import RunRequest as RunRequest
from prismclaw.schema.content.streaming import IterationBoundary
from prismclaw.schema.session.projection import SessionProjectionEngine

__all__ = ["run_agent_stream"]

logger = logging.getLogger(__name__)


async def run_agent_stream(
    request: RunRequest,
    deps: AgentRunnerDeps,
    *,
    tool_workspace_path: str | Path,
    control: RunControl | None = None,
    abort: asyncio.Event | None = None,
) -> AsyncIterator[Any]:
    if control is None:
        control = RunControl(abort_event=abort) if abort is not None else RunControl()

    # ── Phase 1: 启动路径判定（全部在 RunPreparer 内闭合）──
    preparer = RunPreparer(
        request,
        deps,
        tool_workspace_path=tool_workspace_path,
        control=control,
    )
    result = await preparer.prepare()

    # ── EarlyExit: hook 阻断 / bootstrap 错误 / 确认拒绝等 ──
    if isinstance(result, EarlyExit):
        async for event in result.events:
            yield event
        result.runtime.terminated_by = TerminalReason.EARLY_EXIT
        await run_teardown(result.runtime)
        return

    # ── Phase 2: 主循环（rt 是唯一 run 级容器）──
    rt = result
    snapshot = rt.snapshot
    total_output_tokens = (
        snapshot.total_output_tokens if isinstance(snapshot.total_output_tokens, int) else 0
    )
    total_cache_creation_tokens = (
        snapshot.total_cache_creation_tokens
        if isinstance(snapshot.total_cache_creation_tokens, int)
        else 0
    )

    try:
        async with rt.traj_scope:
            # ── 先原地装配 rt（build_runtime 填充 tool_loop_state / retry_counts /
            #    pipeline 等），再据此构建 RunLoopState —— 顺序不能反，否则
            #    RunLoopState 会拿到未装配的 None 字段。──
            _loop_config = build_loop_config(rt)

            # ── 构建 RunLoopState（三周期载体：frame / ledger / Run 级对象）──
            _loop_state = RunLoopState(
                iteration=rt.iteration,
                tree=rt.tree,
                snapshot=rt.snapshot,
                run_state=rt.run_state,
                state_machine=rt.state_machine,
                tool_loop_state=rt.tool_loop_state,
                frame=IterationFrame(pending_messages=rt.pending_messages),
                ledger=RunLedger(
                    evidence_ledger=rt.evidence_ledger,
                    total_input_tokens=0,
                    total_output_tokens=total_output_tokens,
                    total_cache_creation_tokens=total_cache_creation_tokens,
                    total_cache_read_tokens=0,
                ),
                active_skill_invocation=rt.active_skill_invocation,
                tool_ctx=rt.tool_ctx,
                ownership_lease=rt.ownership_lease,
                profiler=rt.profiler,
            )

            # ── 运行循环 ──
            async for event in run_loop(_loop_state, _loop_config):
                yield event
                # 轨迹追踪
                if isinstance(event, IterationBoundary):
                    if event.kind == "llm_end":
                        rt.traj_scope.publish_iteration_end(
                            request.session_id, _loop_state.perf.llm_calls
                        )
                    elif event.kind == "llm_start":
                        rt.traj_scope.publish_iteration_start(
                            request.session_id, _loop_state.perf.llm_calls
                        )

            # ── Post-loop: abort 场景合成未启动工具的 ToolCallEnd ──
            if rt.terminated_by in (TerminalReason.ABORTED, TerminalReason.TIMEOUT):
                _pending_ids = _loop_state.frame._pending_tool_call_ids or []
                _completed_ids = {
                    getattr(r, "tool_call_id", None)
                    for r in (_loop_state.frame.last_tool_results or [])
                }
                for call_id in _pending_ids:
                    if call_id not in _completed_ids:
                        from prismclaw.engine.execution.loop._loop_helpers import (
                            _create_aborted_tool_result,
                        )

                        yield _create_aborted_tool_result(call_id)

            # ── 写入 turn-end reason 到 trajectory scope ──
            rt.traj_scope.terminated_by = rt.terminated_by

            # ── 把 loop 期累积的 run 级状态归拢回 rt（同一对象，供 teardown 读取）──
            rt.iteration = _loop_state.perf.llm_calls
            rt.active_skill_invocation = _loop_state.active_skill_invocation
            rt.snapshot = _loop_state.snapshot
            rt.tree = _loop_state.tree
            rt.tool_ctx = _loop_state.tool_ctx
            rt.ownership_lease = _loop_state.ownership_lease
            rt.profiler = _loop_state.profiler
            rt.latest_review_decision = (
                SessionProjectionEngine(rt.tree).task.get_latest_review_decision()
                if rt.tree is not None
                else None
            )
            rt.llm_start_monotonic = _loop_state.frame.llm_start_monotonic
            # 回合级计时聚合（度量用；异常路径下保持 None，teardown 会跳过）
            rt.perf = _loop_state.perf

    finally:
        await run_teardown(rt)
