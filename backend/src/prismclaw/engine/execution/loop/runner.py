from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from pathlib import Path

from prismclaw.schema.agent.run_control_types import RunControl
from prismclaw.schema.content.streaming import IterationBoundary

__all__ = ["run_agent_stream"]

logger = logging.getLogger(__name__)


from prismclaw.engine.execution.loop.bootstrap import (
    AgentRunnerDeps,
)

from prismclaw.engine.execution.control.kernel import TerminalReason
from prismclaw.engine.execution.query.pre_request_pipeline import PreRequestPipeline
from prismclaw.engine.execution.loop.session_manager import (
    TeardownParams,
    run_teardown,
)
from prismclaw.engine.execution.loop.agent_loop import run_loop, create_loop_config
from prismclaw.engine.execution.loop.types import IterationFrame, RunLedger, RunLoopState
from prismclaw.schema.agent.types import RunRequest
from prismclaw.engine.execution.loop.preparer import (
    EarlyExit,
    PreparedRun,
    RunPreparer,
)


async def run_agent_stream(
    request: RunRequest,
    deps: AgentRunnerDeps,
    *,
    tool_workspace_path,
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
        # EarlyExit 也需要 teardown（telemetry / ownership release / hooks 通知）
        await run_teardown(
            TeardownParams(
                ctx=result.ctx,
                iteration=result.iteration,
                terminated_by=TerminalReason.EARLY_EXIT,
                initial_task_spec=result.initial_task_spec,
                run_state=result.run_state,
                tree=result.tree,
                snapshot=result.snapshot,
                tool_ctx=result.tool_ctx,
                ownership_lease=result.ownership_lease,
                profiler=result.profiler,
                active_skill_invocation=result.active_skill_invocation,
            ),
            deps=deps,
            request=request,
        )
        return

    # ── Phase 2: 主循环（从 PreparedRun 拿所有确定性初始值）──
    prepared: PreparedRun = result
    ctx = prepared.ctx
    tree = prepared.tree
    snapshot = prepared.snapshot
    run_state = prepared.run_state
    task_runtime = prepared.task_runtime
    initial_task_spec = prepared.initial_task_spec
    recovery = prepared.recovery
    state_machine = prepared.state_machine
    tool_ctx = prepared.tool_ctx
    ownership_lease = prepared.ownership_lease
    profiler = prepared.profiler
    _traj_scope = prepared.traj_scope
    effective_model = prepared.effective_model
    active_skill_invocation = prepared.active_skill_invocation
    prompt_cache_runtime = prepared.prompt_cache_runtime
    evidence_ledger = prepared.evidence_ledger
    total_input_tokens = prepared.total_input_tokens
    total_output_tokens = prepared.total_output_tokens
    total_cache_creation_tokens = prepared.total_cache_creation_tokens
    total_cache_read_tokens = prepared.total_cache_read_tokens
    attachment_decision = prepared.attachment_decision
    _mode = prepared._mode
    resolved_ws_path = Path(tool_workspace_path).resolve()

    abort_event = control.abort_event
    from prismclaw.engine.execution.control.kernel import resolve_effort_budget
    _effort_budget = resolve_effort_budget(
        effort=getattr(request, "effort", "standard") or "standard",
        long_running=bool(getattr(request, "long_running", False)),
        tiers=deps.config.effort,
    )
    run_deadline_s = _effort_budget.hard_s
    run_started = ctx.run_started if ctx is not None else time.monotonic()

    try:
        tool_loop_state = state_machine.load_tool_loop_state()
        retry_counts = state_machine.retry_counts_view()

        async with _traj_scope:

            # ── 构建 RunLoopState（三周期载体：frame / ledger / Run 级对象）──
            _loop_state = RunLoopState(
                iteration=prepared.iteration,
                ctx=ctx,
                tree=tree,
                snapshot=snapshot,
                run_state=run_state,
                state_machine=state_machine,
                tool_loop_state=tool_loop_state,
                task_runtime=task_runtime,
                frame=IterationFrame(pending_messages=prepared.pending_messages),
                ledger=RunLedger(
                    evidence_ledger=evidence_ledger,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    total_cache_creation_tokens=total_cache_creation_tokens,
                    total_cache_read_tokens=total_cache_read_tokens,
                ),
                active_skill_invocation=active_skill_invocation,
                tool_ctx=tool_ctx,
                ownership_lease=ownership_lease,
                profiler=profiler,
                prompt_cache_runtime=prompt_cache_runtime,
            )
            _loop_config = create_loop_config(
                deps=deps,
                request=request,
                recovery=recovery,
                state_machine=state_machine,
                tool_loop_state=tool_loop_state,
                run_state=run_state,
                initial_task_spec=initial_task_spec,
                retry_counts=retry_counts,
                response_policy=None,  # computed per-turn inside closure
                pipeline=PreRequestPipeline(),
                task_runtime=task_runtime,
                snapshot=snapshot,
                tree=tree,
                tool_ctx=tool_ctx,
                tool_workspace_path=resolved_ws_path,
                effective_model=effective_model,
                abort_event=abort_event,
                run_deadline_s=run_deadline_s,
                run_started=run_started,
                _mode=_mode,
                _traj_scope=_traj_scope,
                attachment_guidance=attachment_decision.attachment_guidance if attachment_decision else None,
            )

            # ── 运行循环 ──
            async for event in run_loop(_loop_state, _loop_config):
                yield event
                # 轨迹追踪
                if isinstance(event, IterationBoundary):
                    if event.kind == "llm_end":
                        _traj_scope.publish_iteration_end(request.session_id, _loop_state.perf.llm_calls)
                    elif event.kind == "llm_start":
                        _traj_scope.publish_iteration_start(request.session_id, _loop_state.perf.llm_calls)

            # ── Post-loop: abort 场景合成未启动工具的 ToolCallEnd ──
            _term_reason = _loop_state.ctx.terminated_by if _loop_state.ctx is not None else None
            if _term_reason in (TerminalReason.ABORTED, TerminalReason.TIMEOUT):
                _pending_ids = _loop_state.frame._pending_tool_call_ids or []
                _completed_ids = {
                    getattr(r, "tool_call_id", None) for r in (_loop_state.frame.last_tool_results or [])
                }
                for call_id in _pending_ids:
                    if call_id not in _completed_ids:
                        from prismclaw.engine.execution.loop._loop_helpers import _create_aborted_tool_result
                        yield _create_aborted_tool_result(call_id)

            # ── 写入 turn-end reason 到 trajectory scope ──
            _traj_scope._terminated_by_cell[0] = (
                _loop_state.ctx.terminated_by if _loop_state.ctx is not None else prepared.terminated_by
            )

            # Sync state back from RunLoopState for the finally block
            prepared.iteration = _loop_state.perf.llm_calls
            if _loop_state.ctx is not None:
                prepared.terminated_by = _loop_state.ctx.terminated_by
            prepared.active_skill_invocation = _loop_state.active_skill_invocation
            prepared.run_state = _loop_state.run_state
            prepared.snapshot = _loop_state.snapshot
            prepared.tree = _loop_state.tree
            prepared.ctx = _loop_state.ctx
            prepared.tool_ctx = _loop_state.tool_ctx
            prepared.ownership_lease = _loop_state.ownership_lease
            prepared.profiler = _loop_state.profiler
            prepared.total_input_tokens = _loop_state.ledger.total_input_tokens
            prepared.total_output_tokens = _loop_state.ledger.total_output_tokens
            prepared.latest_review_decision = tree.get_latest_review_decision() if tree is not None else None
            prepared.llm_start_monotonic = _loop_state.frame.llm_start_monotonic
            # 回合级计时聚合（度量用；异常路径下保持 None，teardown 会跳过）
            prepared.perf = _loop_state.perf

    finally:
        # 不再需要 try/except NameError 守护——PreparedRun 中所有字段有确定初始值
        await run_teardown(
            TeardownParams(
                ctx=prepared.ctx,
                iteration=prepared.iteration,
                llm_start=prepared.llm_start_monotonic,
                terminated_by=prepared.terminated_by,
                initial_task_spec=prepared.initial_task_spec,
                latest_review_decision=prepared.latest_review_decision,
                run_state=prepared.run_state,
                tree=prepared.tree,
                snapshot=prepared.snapshot,
                tool_ctx=prepared.tool_ctx,
                ownership_lease=prepared.ownership_lease,
                profiler=prepared.profiler,
                perf=prepared.perf,
                recovery_chain=recovery.chain if recovery is not None else [],
                active_skill_invocation=prepared.active_skill_invocation,
            ),
            deps=deps,
            request=request,
        )
