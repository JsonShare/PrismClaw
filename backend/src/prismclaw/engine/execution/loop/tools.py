"""tools — 工具执行前/后回调与 execute_tools 主体。

由 ``build_loop_config`` 绑定到 ExecutePort：
- ``gate_tool_call``（原 before_tool_call）：冻结签名 + 安全门
- ``after_tool_call``：完成契约收敛提示
- ``execute_tools``：工具执行主流程（handoff dispatch → 执行 → 证据 → 投影 → 落树）

所有运行期依赖通过 ``rt: LoopRuntime`` 显式访问。
"""
from __future__ import annotations

import contextlib
import logging
import time as _time
from collections.abc import AsyncIterator
from typing import Any

from prismclaw.engine.execution.loop._loop_helpers import (
    _close_orphaned_tool_calls,
    _persist_blocked_tool_results,
    compute_tool_signature,
)
from prismclaw.engine.execution.loop.runtime import LoopRuntime
from prismclaw.engine.execution.loop.types import RunLoopState
from prismclaw.schema.content.streaming import ErrorEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# before_tool_call → gate_tool_call
# ---------------------------------------------------------------------------

async def gate_tool_call(
    rt: LoopRuntime,
    before_ctx: Any,
) -> Any:
    """工具执行前检查。

    1. doom-loop 冻结签名命中 → block；反复重试被冻结的调用达升级阈值
       → 置 frozen_call_defied 嫌疑标志（只作观测，不产生终态）。
    2. evaluate_safety_gate deny → block
    都不 deny → None（执行）
    """
    run_state = rt.run_state
    tool_loop_state = rt.tool_loop_state
    deps = rt.deps
    state_machine = rt.state_machine
    _mode = rt.mode
    request = rt.request
    _traj_scope = rt.traj_scope

    # tool_calls_spent 按尝试计，含被拒；并行 N 个调用计 N。
    run_state.loop_state.tool_calls_spent += 1
    from prismclaw.engine.execution.dispatch.call_handling import check_frozen_call
    from prismclaw.engine.execution.dispatch.tools import evaluate_safety_gate

    _bstate = before_ctx.state
    _bctx = _bstate.ctx if _bstate is not None else None
    call = before_ctx.tool_call

    # ── 1. doom-loop 冻结检查（在安全门之前——冻结是确定性决策）──
    _args = before_ctx.args if isinstance(before_ctx.args, dict) else {}
    _sig = compute_tool_signature(call.name or "", _args)
    frozen_outcome = check_frozen_call(
        state=tool_loop_state,
        tool_signature=_sig,
        config=getattr(deps.config, "run_loop", None) if hasattr(deps, "config") else None,
        counted_this_turn=_bstate.frozen_counted_this_turn if _bstate is not None else set(),
    )
    if frozen_outcome.action == "freeze":
        state_machine.update_tool_loop_state(tool_loop_state)
        from prismclaw.engine.execution.loop.types import BeforeToolCallResult
        return BeforeToolCallResult(
            block=True, reason=frozen_outcome.guidance or "该工具调用已被死循环防护冻结",
        )

    # ── 2. 安全门 ──
    _gate_denied, _pre_tool_denied, _allowed = await evaluate_safety_gate(
        _bctx,
        [call],
        mode=_mode,
        workspace_tag=getattr(request, "workspace_tag", None),
        traj_turn_counter=_traj_scope.turn_counter if _traj_scope is not None else 0,
        iteration=_bstate.perf.llm_calls if _bstate is not None else 0,
    )
    all_denied = {**_gate_denied, **_pre_tool_denied}
    call_id = getattr(call, "call_id", None) or (
        call.call.get("id", "") if isinstance(getattr(call, "call", None), dict) else ""
    )
    if call_id in all_denied:
        reason = all_denied.get(call_id, "blocked")
        from prismclaw.engine.execution.loop.types import BeforeToolCallResult
        return BeforeToolCallResult(block=True, reason=reason)
    return None


# ---------------------------------------------------------------------------
# after_tool_call
# ---------------------------------------------------------------------------

async def after_tool_call(
    rt: LoopRuntime,
    after_ctx: Any,
) -> Any:
    """工具执行后处理（只做观测与一次收尾提示，不做裁决）。

    POST_TOOL hooks 已下沉到 execute_tool_calls 逐调用派发。此处保留：
    * 收敛软提示：完成契约机械核验无缺口时注入一次"请收尾"提示。
    """
    _completion_state = rt.completion_state
    tool_loop_state = rt.tool_loop_state
    state_machine = rt.state_machine

    _state = after_ctx.state
    _ctx = _state.ctx if _state is not None else None

    if (
        _state is not None
        and after_ctx.tool_calls
        and _ctx is not None
        and _completion_state.contract is not None
    ):
        from prismclaw.engine.execution.control.artifacts import ArtifactScanner
        from prismclaw.engine.execution.control.kernel import CompletionReviewer

        _review = CompletionReviewer(
            ArtifactScanner(_ctx._tool_workspace_path)
        ).evaluate(_completion_state.contract, _ctx.evidence_ledger)
        if _review.accepted and not tool_loop_state.convergence_advisory_sent:
            tool_loop_state.convergence_advisory_sent = True
            state_machine.update_tool_loop_state(tool_loop_state)
            logger.info(
                "convergence: deliverables complete at iter=%d "
                "— injecting final-answer advisory",
                _state.perf.llm_calls,
            )
            from prismclaw.engine.execution.loop.types import AfterToolCallResult
            return AfterToolCallResult(
                inject_messages=[{
                    "role": "user",
                    "content": (
                        "[系统提示：所有必需的交付物均已产出并通过校验"
                        "（写入与完成策略证据齐全）。不要再调用任何工具，"
                        "请立即给出最终答复。]"
                    ),
                }]
            )
    return None


# ---------------------------------------------------------------------------
# execute_tools
# ---------------------------------------------------------------------------

async def execute_tools(
    rt: LoopRuntime,
    state: RunLoopState,
    response: Any,
    allowed_calls: list[Any],
) -> AsyncIterator[Any]:
    """工具执行。B/S 架构有 handoff dispatch + subagent，因此提取为独立 callback。"""
    deps = rt.deps
    tree = rt.tree
    request = rt.request
    tool_ctx = rt.tool_ctx
    tool_workspace_path = rt.tool_workspace_path
    run_started = rt.run_started
    snapshot = rt.snapshot
    task_runtime = rt.task_runtime

    from prismclaw.engine.execution.control.artifacts import ArtifactScanner
    from prismclaw.engine.execution.control.evidence import declared_paths, record_tool_evidence
    from prismclaw.engine.execution.dispatch.call_handling import parse_tool_calls
    from prismclaw.engine.execution.dispatch.execution import (
        apply_handoff_dispatch_plan,
        execute_tool_calls_with_handoff,
        persist_projected_tool_result_entries,
        plan_handoff_dispatch,
        postprocess_tool_results,
        project_tool_result_entries,
    )
    from prismclaw.engine.execution.loop.arbiter import persist_snapshot_fn
    from prismclaw.engine.execution.loop.tree_writer import (
        _append,
        _persist_turn_runtime_metrics,
        publish_context_node,
    )
    from prismclaw.engine.execution.skills.skill_dispatch import (
        _apply_context_modifiers_from_results,
        _execute_skill_execution_strategys_from_results,
    )
    from prismclaw.engine.execution.state.task_persistence import (
        _persist_execution_evidence,
        _record_latest_tool_effect,
        _record_result_compaction_state,
    )
    from prismclaw.engine.toolkit.builtin.tool_helpers import _extract_file_paths_from_tool_calls
    from prismclaw.engine.toolkit.subagent.subagent_progress_emitter import (
        _emit_cap_exceeded_synthetic_progress,
    )
    from prismclaw.schema.agent import HeartbeatEvent

    try:
        # ── 0. Persist assistant message (with tool_calls) ──
        from prismclaw.engine.execution.dispatch.response_handling import (
            append_assistant_response,
        )
        from prismclaw.engine.execution.dispatch.turn_utils import strip_thinking

        await append_assistant_response(
            deps=deps,
            tree=tree,
            append_entry=_append,
            text=strip_thinking(response.text or ""),
            tool_calls=response.tool_calls,
        )
        # 被拒调用（before_tool_call block）的 tool 结果随之落树。
        if state.blocked_tool_results:
            await _persist_blocked_tool_results(
                deps=deps,
                tree=tree,
                blocked_events=state.blocked_tool_results,
            )
            state.blocked_tool_results = []

        # ── 1. Handoff dispatch ──
        _allowed_raw_calls = [c.call for c in allowed_calls]
        handoff_result = await plan_handoff_dispatch(
            deps=deps,
            request=request,
            tool_calls=_allowed_raw_calls,
        )
        await apply_handoff_dispatch_plan(
            tool_ctx=tool_ctx,
            handoff_plan=handoff_result,
            subagent_progress_sink=deps.callbacks.on_subagent_progress if deps.callbacks is not None else None,
            emit_cap_exceeded_progress=_emit_cap_exceeded_synthetic_progress,
        )

        # ── 1.5 Artifact baseline ──
        _artifact_scanner = ArtifactScanner(tool_workspace_path)
        _parsed_calls = parse_tool_calls(handoff_result.tool_calls)
        _declared = tuple(
            path for _call in _parsed_calls for path in declared_paths(_call)
        )
        _artifacts_before = _artifact_scanner.stat_paths(_declared)

        # ── 2. Execute tools ──
        _exec_elapsed = _time.monotonic() - run_started if run_started > 0 else 0.0
        yield HeartbeatEvent(phase="executing", elapsed_s=_exec_elapsed)

        execution_artifacts = await execute_tool_calls_with_handoff(
            deps=deps,
            request=request,
            tool_calls=handoff_result.tool_calls,
            tool_ctx=tool_ctx,
            cap_exceeded_call_ids=handoff_result.cap_exceeded_call_ids,
            finish_reason=(state.llm_response.finish_reason if state.llm_response else None),
        )
        execution_artifacts = await postprocess_tool_results(
            deps=deps,
            tool_ctx=tool_ctx,
            execution_artifacts=execution_artifacts,
        )

        # ── 3. Skill dispatch strategies from results ──
        execution_artifacts.results = await _execute_skill_execution_strategys_from_results(
            deps=deps,
            tool_ctx=tool_ctx,
            results=execution_artifacts.results,
        )

        # ── 5. Dynamic skill refresh ──
        _file_paths = _extract_file_paths_from_tool_calls(execution_artifacts.tool_calls)
        if _file_paths and hasattr(deps, "skill_provider") and deps.skill_provider is not None:
            deps.skill_provider.refresh_heavyweight(
                workspace_path=tool_workspace_path,
                file_paths=_file_paths,
            )

        # ── 8. Skill context modifiers ──
        (
            _updated_skill_invocation,
            _updated_messages_override,
            _context_injections,
        ) = _apply_context_modifiers_from_results(
            deps=deps,
            request=request,
            snapshot=snapshot,
            tool_ctx=tool_ctx,
            results=execution_artifacts.results,
            skill_invocation=state.active_skill_invocation,
            messages_override=state.pending_meta_messages,
        )
        if _updated_skill_invocation is not state.active_skill_invocation:
            state.active_skill_invocation = _updated_skill_invocation
        if _updated_messages_override is not None:
            state.pending_meta_messages = _updated_messages_override
        for _inj in _context_injections:
            with contextlib.suppress(Exception):
                publish_context_node(
                    tree,
                    entry_id=_inj.entry_id,
                    label=_inj.skill_name,
                    content=_inj.content,
                )

        # ── 9. Evidence ledger: one record per tool result ──
        _artifacts_after = _artifact_scanner.stat_paths(_declared)
        _calls_by_id = {_call.call_id: _call for _call in _parsed_calls}
        for _result in execution_artifacts.results:
            _record = record_tool_evidence(
                task_id=task_runtime.task_id,
                call=_calls_by_id.get(getattr(_result, "tool_call_id", "")),
                result=_result,
                before=_artifacts_before,
                after=_artifacts_after,
            )
            await _persist_execution_evidence(deps, tree, evidence=_record)
            state.ledger.evidence_ledger.append(_record)

        # ── 10. Tool result projection and persistence ──
        _projection = project_tool_result_entries(
            tree=tree,
            tool_calls=execution_artifacts.tool_calls,
            results=execution_artifacts.results,
            compaction_turn=execution_artifacts.compaction_turn,
            current_step_id=None,
        )
        _projected_events = await persist_projected_tool_result_entries(
            append_entry=lambda entry: _append(deps, tree, entry),
            projection=_projection,
        )
        for _evt in _projected_events:
            yield _evt

        # ── 11. Store results for after_tool_call ──
        state.frame.last_tool_results = list(execution_artifacts.results)

        # ── 12. Snapshot recording ──
        _record_latest_tool_effect(
            snapshot,
            deps=deps,
            tool_calls=execution_artifacts.tool_calls,
            results=execution_artifacts.results,
        )
        _record_result_compaction_state(
            snapshot,
            compaction_turn=execution_artifacts.compaction_turn,
        )

        # ── 13. Persist snapshot ──
        await persist_snapshot_fn(state.ctx)

        # ── 14. Turn runtime metrics persistence ──
        _dynamic_payload = 0
        for _r in execution_artifacts.results:
            _c = getattr(_r, "content", None)
            if isinstance(_c, str):
                _dynamic_payload += len(_c)
            elif isinstance(_c, list):
                for _blk in _c:
                    _t = getattr(_blk, "text", None)
                    if _t:
                        _dynamic_payload += len(_t)
        _result_payload_reduction = 0
        if execution_artifacts.compaction_turn is not None:
            _result_payload_reduction = getattr(
                execution_artifacts.compaction_turn, "bytes_saved", 0,
            ) or 0
        await _persist_turn_runtime_metrics(
            deps,
            tree,
            request_mode=getattr(request, "mode", None),
            dynamic_payload=_dynamic_payload,
            input_tokens=state.ledger.total_input_tokens,
            cache_read_tokens=state.ledger.total_cache_read_tokens,
            result_payload_reduction=_result_payload_reduction,
        )

    except Exception as _exec_exc:
        logger.warning(
            "execute_tools error: %s", _exec_exc, exc_info=True,
        )
        # Step 0 已把 assistant（含 tool_calls）落树——中途异常时
        # 尚未执行的调用必须在树层闭合配对（合成 error 结果）。
        await _close_orphaned_tool_calls(
            deps=deps,
            tree=tree,
            tool_calls=response.tool_calls or [],
        )
        yield ErrorEvent(
            error_code="tool_execution_error",
            message=f"工具执行失败：{_exec_exc}",
        )
