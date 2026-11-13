"""continue_ — 统一续跑/终态决策（完成控制的唯一出口）。

由 ``build_loop_config`` 绑定为 ``LoopConfig.should_continue``。
所有运行期依赖通过 ``rt: LoopRuntime`` 显式访问，``nonlocal`` 变量
（initial_task_spec、iteration_budget_warned）已改为 ``rt`` 属性读写。
"""
from __future__ import annotations

import logging
import time as _time
from dataclasses import replace
from typing import Any

from prismclaw.engine.execution.control.completion import build_completion_contract
from prismclaw.engine.execution.control.kernel import Decision, LoopEvent, TerminalReason
from prismclaw.engine.execution.loop import terminal as _terminal
from prismclaw.engine.execution.loop._loop_helpers import (
    _build_all_blocked_guidance,
    compute_result_fingerprint,
    compute_tool_signature,
)
from prismclaw.engine.execution.loop.runtime import LoopRuntime
from prismclaw.engine.execution.loop.types import RunLoopState
from prismclaw.schema.content.streaming import ErrorEvent

logger = logging.getLogger(__name__)


async def should_continue(
    rt: LoopRuntime,
    state: RunLoopState,
    response: Any | None = None,
    *,
    event: LoopEvent,
) -> Decision:
    """统一续跑/终态决策——完成控制的唯一出口。

    ``event.site`` 声明调用位置（字段而非分支选择器）：

    - ``preflight``（LLM 响应后、工具执行前）：预算 / abort / 无工具
      回合治理（完成申报）。工具循环策略不在此评估。
    - ``all_blocked``（before_tool_call 全拒后）：恢复熔断。
    - ``post``（execute_tools 之后）：record 工具调用观察（唯一写入点）
      + evaluate 工具循环策略。

    完成裁决全权委托 ``completion_kernel``；本函数只把裁决翻译成骨架
    动作。after_tool_call 已写入的终态优先级最高，任何阶段直接短路。
    """
    from prismclaw.engine.foundation.runtime_util import is_abort_set

    if state.terminal_error is not None:
        return Decision("TERMINATE", state.ctx.terminated_by or TerminalReason.ERROR)

    # 台账绑定：内核读的是与 ctx/state 同一个 list（append 语义共享）。
    rt.completion_state.ledger = state.ledger.evidence_ledger
    _sc_t0 = _time.monotonic()
    _gov_ms = -1.0

    # ── 完成契约构建 ──
    if not rt.completion_state.contract_resolved:
        rt.completion_state.contract_resolved = True
        rt.completion_state.contract = (
            build_completion_contract(
                rt.initial_task_spec,
                max_repairs=rt.completion_config.max_repairs,
            )
            if rt.initial_task_spec is not None
            else None
        )

    # ── 人/abort 通道：不属于资源预算，先于内核 ──
    if state.ctx is not None and is_abort_set(state.ctx.abort_event):
        state.ctx.terminated_by = TerminalReason.ABORTED
        state.terminal_error = ErrorEvent(error_code="aborted", message="run aborted")
        return Decision("TERMINATE", TerminalReason.ABORTED)

    # ── 无工具回合（preflight 独有）：模型停止调用工具 = 完成申报 ──
    if event.site == "preflight" and response is not None and not response.tool_calls:
        from prismclaw.engine.execution.state.governance import evaluate_no_tool_governance

        _stop_event = replace(
            event,
            is_stop=True,
            declares_blocked=bool(getattr(rt.tool_ctx, "goal_block_intent", None)),
        )
        _gov_t0 = _time.monotonic()
        gov = await evaluate_no_tool_governance(
            state.ctx,
            response=response,
            event=_stop_event,
            kernel=rt.completion_kernel,
            retry_counts=rt.retry_counts,
            response_policy=rt.response_policy,
            state_machine=rt.state_machine,
            prepared_turn=state.prepared_turn,
            total_input_tokens=state.ledger.total_input_tokens,
            total_output_tokens=state.ledger.total_output_tokens,
            total_cache_creation_tokens=state.ledger.total_cache_creation_tokens,
            total_cache_read_tokens=state.ledger.total_cache_read_tokens,
            run_state=rt.run_state,
        )
        if gov.appended_messages_override:
            state.pending_messages = list(gov.appended_messages_override)
        if gov.done_event is not None:
            state.done_event = gov.done_event
            state.ctx.terminated_by = (
                TerminalReason.MAX_TOKENS
                if state.max_tokens_hit_in_step
                else TerminalReason.DONE
            )
        if gov.terminal_code is not None:
            await _terminal.terminate(
                rt,
                state,
                gov.terminal_code,
                message=gov.terminal_message,
                terminated_by=gov.terminated_by,
            )
        _gov_ms = (_time.monotonic() - _gov_t0) * 1000
        _sc_total_ms = (_time.monotonic() - _sc_t0) * 1000
        if _sc_total_ms > 500.0:
            logger.info(
                "should_continue_timing site=%s iteration=%d total=%.0fms gov=%.0fms kernel=-",
                event.site,
                state.perf.llm_calls,
                _sc_total_ms,
                _gov_ms,
            )
        return gov.decision

    # ── 预算 + 恢复（非完成事件；内核是唯一预算权威）──
    _kernel_t0 = _time.monotonic()
    decision = rt.completion_kernel.decide(event)
    if decision.verdict == "TERMINATE":
        await _terminal.terminate(rt, state, decision.code)
        return decision
    if decision.verdict == "RESTART":
        if event.site == "all_blocked":
            # 盲重启 = 模型原样重发同一批被拒调用。RESTART 必须携带
            # 拒绝原因与「换工具/收敛」指令，否则这一轮重规划没有
            # 任何可用信息（旧实现即此空转）。
            state.pending_messages.append({
                "role": "user",
                "content": _build_all_blocked_guidance(state.blocked_tool_results),
            })
        return decision

    # ── 预算临限提醒（不改变裁决，只提醒一次）──
    _budget_dim = rt.budget_guard.warn(iteration=state.perf.llm_calls)
    if _budget_dim is not None and not rt.iteration_budget_warned:
        rt.iteration_budget_warned = True
        state.pending_messages.append(
            {
                "role": "user",
                "content": (
                    "[系统提示：本轮次迭代预算已用去九成。"
                    "收敛信号——不要再开新探索分支，"
                    "本轮只用自然语言交付已有结论；"
                    "末尾自然收尾点明尚未覆盖的方向，"
                    "让用户决定后续是否继续。]"
                ),
            },
        )

    # ── 工具循环策略（post 独有 — 先 record 再 evaluate）──
    if event.site == "post" and response is not None and response.tool_calls:
        from prismclaw.engine.execution.dispatch.call_handling import (
            evaluate_tool_loop_policies,
            parse_tool_calls,
            record_tool_call_observation,
        )

        _tool_name: str | None = None
        _tool_args: dict = {}
        _first_id = ""
        _parsed = parse_tool_calls(response.tool_calls[:1])
        if _parsed:
            _tool_name = _parsed[0].name or None
            _tool_args = _parsed[0].arguments or {}
            _first_id = _parsed[0].call_id
        _tool_sig = compute_tool_signature(_tool_name or "", _tool_args)

        _first_result = None
        if _first_id:
            for _r in state.last_tool_results or []:
                if getattr(_r, "tool_call_id", "") == _first_id:
                    _first_result = _r
                    break
        elif state.last_tool_results:
            _first_result = state.last_tool_results[0]

        record_tool_call_observation(
            rt.tool_loop_state,
            tool_name=_tool_name,
            tool_signature=_tool_sig,
            result_fingerprint=compute_result_fingerprint(_first_result),
            is_error=bool(
                _first_result is None or getattr(_first_result, "is_error", False)
            ),
            error_origin=(
                getattr(_first_result, "error_origin", None)
                if _first_result is not None
                else None
            ),
        )
        loop_outcome = evaluate_tool_loop_policies(
            state=rt.tool_loop_state,
            config=(
                getattr(rt.deps.config, "run_loop", None)
                if hasattr(rt.deps, "config")
                else None
            ),
        )
        rt.state_machine.update_tool_loop_state(rt.tool_loop_state)

        if loop_outcome.action == "warn" and loop_outcome.guidance is not None:
            state.pending_messages.append(
                {"role": "user", "content": loop_outcome.guidance},
            )

        if loop_outcome.action == "freeze":
            if loop_outcome.frozen_signature:
                rt.tool_loop_state.frozen_call_signatures.setdefault(
                    loop_outcome.frozen_signature, 0,
                )
            state.pending_messages.append(
                {"role": "user", "content": loop_outcome.guidance},
            )
            rt.state_machine.update_tool_loop_state(rt.tool_loop_state)

    _sc_total_ms = (_time.monotonic() - _sc_t0) * 1000
    if _sc_total_ms > 500.0:
        logger.info(
            "should_continue_timing site=%s iteration=%d total=%.0fms gov=%.0fms kernel=%.0fms",
            event.site,
            state.perf.llm_calls,
            _sc_total_ms,
            _gov_ms if _gov_ms >= 0 else -1.0,
            (_time.monotonic() - _kernel_t0) * 1000,
        )
    return decision
