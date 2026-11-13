"""prepare — prepare_next_turn（每轮上下文重建）。

由 ``build_loop_config`` 绑定为 ``PreparePort.prepare_next_turn``。
"""
from __future__ import annotations

import logging
import time as _time

from prismclaw.engine.execution.control.kernel import TerminalReason, evaluate_loop_control
from prismclaw.engine.execution.loop import terminal as _terminal
from prismclaw.engine.execution.loop.runtime import LoopRuntime
from prismclaw.engine.execution.loop.types import RunLoopState, TurnUpdate

logger = logging.getLogger(__name__)


async def prepare_next_turn(
    rt: LoopRuntime,
    state: RunLoopState,
) -> TurnUpdate | None:
    """准备下一轮上下文。

    每迭代从 tree 重建 query_loop_state（投影语义：树是唯一真相源，
    无跨迭代缓存、无手工失效标志）。
    """
    if rt.pipeline is None:
        return None

    from prismclaw.engine.execution.query.context_helpers import (
        _build_source_query_loop_state,
    )

    query_loop_state = _build_source_query_loop_state(
        tree=state.tree,
        request=rt.request,
        deps=rt.deps,
        tool_ctx=rt.tool_ctx,
        effective_model=rt.effective_model or "",
        attachment_guidance=rt.attachment_guidance,
        tool_loop_state=rt.tool_loop_state,
        pending_meta_messages=state.pending_meta_messages,
        appended_messages_override=state.pending_messages or None,
    )
    state.pending_messages.clear()

    rt.run_projector.attach(getattr(rt.deps, "context_assembler", None))

    _ctrl = evaluate_loop_control(
        run_state=rt.run_state,
        effort_budget=rt.effort_budget,
        now=_time.monotonic(),
    )
    if _ctrl.should_stop:
        # should_stop 只在已用完最后一轮（turns_left<=0 / remaining_hard<=0）时触发。
        # 此时不能再调 LLM——设 terminal_error 让 agent_loop 直接 yield + return。
        from prismclaw.engine.execution.control.kernel import TerminalReason
        from prismclaw.engine.execution.loop import terminal as _terminal
        await _terminal.terminate(
            rt,
            state,
            "budget_iterations",
            message=rt.terminal_messages.get("budget_iterations"),
            terminated_by=TerminalReason.BUDGET_ITERATIONS,
        )
        return None

    if _ctrl.freeze_tools and rt.tool_loop_state is not None:
        _run_loop_cfg = getattr(rt.deps.config, "run_loop", None)
        if _ctrl.is_last_turn:
            # 最后一轮：冻结所有工具，强制模型做综合回答。
            # 软着陆 prompt 已注入「不要调用工具」，此处用强制执行兜底——
            # 哪怕模型忽略 prompt 也调不起来工具，保证这一轮产出的是纯文本答案。
            for _tname in rt.deps.tools.names():
                rt.tool_loop_state.frozen_tools.add(_tname)
        else:
            _search_tools = set(getattr(_run_loop_cfg, "search_intent_tools", []) or [])
            rt.tool_loop_state.frozen_tools.update(_search_tools)
        rt.state_machine.update_tool_loop_state(rt.tool_loop_state)

    result = await rt.pipeline.run(
        request=rt.request,
        deps=rt.deps,
        tree=state.tree,
        turn_state=state.snapshot.turn_state,
        tool_workspace_path=rt.tool_workspace_path,
        effective_model=rt.effective_model,
        tool_ctx=rt.tool_ctx,
        skill_invocation=state.active_skill_invocation,
        attachment_guidance=rt.attachment_guidance,
        query_loop_state=query_loop_state,
        compression_runtime=state.snapshot.compression_runtime,
        abort_event=rt.abort_event,
        compression_orchestrator=rt.deps.session_compressor,
    )

    state.prepared_turn = result
    logger.debug(
        "prepare_next_turn: wrote state.prepared_turn (preflight=%s)",
        "present" if result.preflight is not None else "None",
    )

    compression_preparation = result.compression_preparation
    if compression_preparation is not None and compression_preparation.action == "restart_turn":
        code = rt.recovery.consult("compression_restart")
        if code is not None:
            _replay = list(
                result.query_loop_state.query_view.source_messages.appended_messages
            )
            rt.run_projector.invalidate(getattr(rt.request, "session_id", None))
            return TurnUpdate(restart=True, pending_messages=_replay)
        await _terminal.terminate(
            rt, state, "error", message="恢复链已耗尽（压缩重启被熔断）",
        )
        return TurnUpdate(restart=False)

    return None
