"""stream — stream_llm（LLM 流式调用 + usage 埋点）。

由 ``build_loop_config`` 绑定为 ``StreamPort.stream_llm``。
"""
from __future__ import annotations

import contextlib
import logging
import time as _time
from collections.abc import AsyncIterator
from typing import Any

from prismclaw.engine.execution.control.kernel import TerminalReason
from prismclaw.engine.execution.loop.runtime import LoopRuntime
from prismclaw.engine.execution.loop.types import RunLoopState

logger = logging.getLogger(__name__)


async def stream_llm(
    rt: LoopRuntime,
    state: RunLoopState,
) -> AsyncIterator[Any]:
    """LLM 流式调用。保留已有模块 call_llm_stream_with_outcome()。"""
    from prismclaw.engine.execution.control.kernel import (
        evaluate_loop_control,
        reasoning_effort_for,
    )
    from prismclaw.engine.execution.dispatch.pipeline import (
        LLMStreamError,
        LLMStreamResult,
        LLMStreamRetry,
        PipelineParams,
        _OutcomeHolder,
        call_llm_stream_with_outcome,
    )
    from prismclaw.engine.foundation.runtime_util import (
        remaining_run_seconds as _remaining_run_seconds,
    )
    from prismclaw.schema.content.streaming import ErrorEvent

    rt.run_state.loop_state.steps_spent += 1
    _remaining_run = _remaining_run_seconds(rt.run_deadline_s, rt.run_started)

    if _remaining_run is not None and _remaining_run <= 0:
        state.ctx.terminated_by = TerminalReason.TIMEOUT
        from prismclaw.engine.execution.loop.arbiter import mark_task_blocked
        await mark_task_blocked(state.ctx, "timeout")
        _timeout_error = ErrorEvent(error_code="timeout", message="run exceeded run_seconds")
        state.llm_outcome = LLMStreamError(error_event=_timeout_error)
        return

    state.frame.llm_start_monotonic = _time.monotonic()

    if state.prepared_turn is None:
        logger.error(
            "stream_llm: state.prepared_turn is None — prepare_next_turn did not write it",
        )
        state.llm_outcome = LLMStreamError(
            error_event=ErrorEvent(error_code="internal", message="prepared_turn is None"),
        )
        return

    _now = _time.monotonic()
    _ctrl = evaluate_loop_control(
        run_state=rt.run_state,
        effort_budget=rt.effort_budget,
        now=_now,
    )
    _remaining_hard = rt.run_state.loop_state.hard_at - _now
    if _ctrl.is_last_turn:
        _cap_s = min(rt.effort_budget.cap_s, max(0.0, _remaining_hard))
    else:
        _cap_s = min(rt.effort_budget.cap_s, max(0.0, _remaining_hard - rt.effort_budget.reserve_s))

    # ── 软着陆引导：最后一轮时在 system message 注入自然收尾指令 ──
    # 触发条件：is_last_turn=True（剩余时间 <= reserve_s 或只剩 1 次迭代机会）
    # 注入位置：packed_request.system（string 末尾追加；list 末尾 append 一条）
    # 去重：rt.time_budget_warned 控制（独立于 iteration_budget_warned / token_budget_warned）
    # 注意：overflow recovery 会重建 prepared_turn，重试时如仍 is_last_turn
    # 会再注入一次——这是正确行为（新 request 需要新警告）
    if _ctrl.is_last_turn and not rt.time_budget_warned:
        _inject_soft_landing_prompt(state, _remaining_hard)
        rt.time_budget_warned = True

    # ── Token/上下文预算警告：ratio ≥ warning_ratio 时注入收敛指令 ──
    # 与时间/迭代 warn 独立——三个 flag 互不吞警告。
    # evaluate_soft_budget + render_budget_warning 复用 _soft_budget 已有逻辑，
    # 不重建阈值/模板解析。
    if not rt.token_budget_warned:
        _pt = state.prepared_turn
        if _pt is not None and _pt.preflight is not None:
            _tokens = int(_pt.preflight.history_tokens or 0)
            _budget = int(_pt.history_budget or 0)
            if _tokens > 0 and _budget > 0:
                try:
                    _safety = rt.deps.config.context.compaction.safety
                    _sb_cfg = _safety.soft_budget
                    _sb_on = bool(_safety.enable_soft_budget)
                    if _sb_on and _sb_cfg is not None:
                        from prismclaw.engine.compression._soft_budget import (
                            evaluate_soft_budget,
                            render_budget_warning,
                        )
                        _verdict = evaluate_soft_budget(
                            token_usage=_tokens,
                            effective_window=_budget,
                            soft_budget=_sb_cfg,
                        )
                        if _verdict.should_warn:
                            _inject_token_budget_warning(
                                state, _verdict, soft_budget=_sb_cfg,
                            )
                            rt.token_budget_warned = True
                except Exception as exc:
                    logger.debug("token_budget_warning.skipped err=%s", exc)

    _pipeline_params = PipelineParams(
        prepared_turn=state.prepared_turn,
        effective_model=rt.effective_model or "",
        remaining_run=_remaining_run,
        run_deadline_s=rt.run_deadline_s,
        run_started=rt.run_started,
        traj_turn_counter=rt.traj_scope.turn_counter if rt.traj_scope is not None else 0,
        iteration_pre_advance=state.perf.llm_calls,
        abort_event=rt.abort_event,
        max_idle_retries=1,
        cap_s=_cap_s,
        is_last_turn=_ctrl.is_last_turn,
        reasoning_effort=reasoning_effort_for(rt.effort_level),
    )
    holder = _OutcomeHolder()
    async for event in call_llm_stream_with_outcome(state.ctx, _pipeline_params, _outcome=holder):
        yield event
    state.llm_outcome = holder.outcome

    # ── 上下文溢出恢复：只重试一次 ──
    # provider_resolver.classify_error 把 prompt-too-long 归为 context_overflow。
    # 此前该错误码无人消费（直接终态）。此处尝试强制压缩后重跑本回合；
    # 第二次 overflow 直接终态，避免无限压缩循环。
    if isinstance(holder.outcome, LLMStreamError):
        _err_code = holder.outcome.error_event.error_code
        if _err_code == "context_overflow" and not rt.overflow_recovered:
            _recovered = await _try_overflow_recovery(rt, state)
            if _recovered:
                rt.overflow_recovered = True
                state.llm_outcome = LLMStreamRetry(reason="compression_restart")
                return

    if isinstance(holder.outcome, LLMStreamResult):
        state.llm_response = holder.outcome.response
        _usage = getattr(holder.outcome.response, "usage", None)
        if _usage is not None:
            state.ledger.total_input_tokens += getattr(_usage, "input_tokens", 0) or 0
            state.ledger.total_output_tokens += getattr(_usage, "output_tokens", 0) or 0
            state.ledger.total_cache_creation_tokens += (
                getattr(_usage, "cache_creation_input_tokens", 0) or 0
            )
            state.ledger.total_cache_read_tokens += (
                getattr(_usage, "cache_read_input_tokens", 0) or 0
            )
            _turn_input = int(getattr(_usage, "input_tokens", 0) or 0)
            _turn_cache_read = int(getattr(_usage, "cache_read_input_tokens", 0) or 0)
            if _turn_input > 0:
                logger.info(
                    "turn_cache_hit cache_read=%d input=%d cache_hit_ratio=%.3f",
                    _turn_cache_read,
                    _turn_input,
                    _turn_cache_read / _turn_input,
                )
            with contextlib.suppress(Exception):
                state.snapshot.compression_runtime.last_prompt_tokens = (
                    getattr(_usage, "input_tokens", None)
                )
            _pt = state.prepared_turn
            if _pt is not None and _pt.preflight is not None:
                from prismclaw.engine.execution.query._turn_helpers import log_turn_token_usage
                log_turn_token_usage(
                    logger_=logger,
                    iteration=state.perf.llm_calls,
                    telemetry=_pt.preflight.compute_turn_telemetry(),
                    usage=_usage,
                    history_budget=_pt.history_budget,
                    history_compaction_count=(
                        rt.tree.get_history_compaction_count() if rt.tree is not None else 0
                    ),
                    tools_tokens=_pt.tools_wire_tokens,
                )


# ---------------------------------------------------------------------------
# overflow recovery — context_overflow 时强制压缩恢复
# ---------------------------------------------------------------------------

async def _try_overflow_recovery(
    rt: LoopRuntime,
    state: RunLoopState,
) -> bool:
    """context_overflow 时尝试强制压缩。

    返回 True 表示压缩成功（树已更新，下轮 prepare 从压缩后的树重建）；
    返回 False 表示压缩失败或不可用，调用方应保留原 LLMStreamError 终态。

    只调一次（由 rt.overflow_recovered 守卫）。
    """
    prepared = state.prepared_turn
    if prepared is None:
        logger.warning("overflow recovery: prepared_turn is None, cannot recover")
        return False

    try:
        from prismclaw.engine.compression.history_compaction import (
            recover_from_context_overflow,
        )
    except Exception:
        logger.warning(
            "overflow recovery: recover_from_context_overflow unavailable",
            exc_info=True,
        )
        return False

    try:
        outcome = await recover_from_context_overflow(
            deps=rt.deps,
            tree=state.tree,
            request=rt.request,
            query_loop_state=prepared.query_loop_state,
            compression_runtime=state.snapshot.compression_runtime,
            history_budget=prepared.history_budget,
            abort_event=rt.abort_event,
        )
    except Exception:
        logger.warning("overflow recovery: compaction raised", exc_info=True)
        return False

    if outcome is None or not getattr(outcome, "ok", False):
        logger.warning(
            "overflow recovery: compaction not ok outcome=%s",
            getattr(outcome, "error_code", outcome),
        )
        return False

    logger.info(
        "overflow recovery: compaction succeeded compacted=%s, restarting turn",
        getattr(outcome, "compacted", False),
    )
    return True


# ---------------------------------------------------------------------------
# 软着陆 prompt 注入
# ---------------------------------------------------------------------------

_SOFT_LANDING_PROMPT = """
[系统指令：这是本轮执行的最后一轮，你必须在本轮完成最终回答。]

执行要求——本轮只输出综合回答，禁止任何工具调用：
1. 立即停止一切工具调用行为——你无法再调用任何工具（工具已全部冻结）。
2. 回顾整个会话上下文，汇总你已获取的全部信息、发现和产物。
3. 直接回答用户最初提出的问题，给出完整的最终答案。
4. 如果某些方面尚未覆盖，诚实点明即可，不要找借口或说"我应该去..."。
5. 不要提及任何预算、超时、最后一轮、工具冻结等内部状态。
"""


def _inject_soft_landing_prompt(state: RunLoopState, remaining_s: float) -> None:
    """在 packed_request.system 里注入软着陆引导文案。

    两种 system 形态都处理：
    - str：末尾追加 "\n\n" + 提示
    - list[dict]：末尾 append {"role": "system", "content": 提示}

    防御：preflight / packed_request / system 任一缺失时静默跳过。
    不抛异常、不修改 messages（保持 tool_use/tool_result 配对不变量）。
    """
    _pt = state.prepared_turn
    if _pt is None or _pt.preflight is None:
        return
    _pr = _pt.preflight.packed_request
    if _pr is None:
        return
    _sys = getattr(_pr, "system", None)
    if _sys is None:
        return

    if isinstance(_sys, str):
        _pr.system = _sys.rstrip() + "\n\n" + _SOFT_LANDING_PROMPT.strip()
    elif isinstance(_sys, list):
        _pr.system = list(_sys) + [{"role": "system", "content": _SOFT_LANDING_PROMPT.strip()}]
    else:
        logger.warning(
            "soft_landing: packed_request.system has unexpected type %s, skip",
            type(_sys).__name__,
        )
        return

    logger.warning(
        "soft_landing.prompt_injected remaining_hard_s=%.1f",
        max(0.0, remaining_s),
    )


def _inject_token_budget_warning(
    state: RunLoopState,
    verdict: Any,
    *,
    soft_budget: Any | None,
) -> None:
    """在 packed_request.system 里注入 token 预算警告文案。

    复用 _inject_soft_landing_prompt 的两种 system 形态处理：
    - str：末尾追加 warning_text
    - list[dict]：末尾 append {"role": "system", "content": warning_text}

    防御：preflight / packed_request / system 任一缺失时静默跳过。
    警告文案由 render_budget_warning 生成（模板解析 + ratio 填充）。
    """
    from prismclaw.engine.compression._soft_budget import render_budget_warning

    _pt = state.prepared_turn
    if _pt is None or _pt.preflight is None:
        return
    _pr = _pt.preflight.packed_request
    if _pr is None:
        return
    _sys = getattr(_pr, "system", None)
    if _sys is None:
        return

    warning_text = render_budget_warning(verdict, soft_budget=soft_budget)

    if isinstance(_sys, str):
        _pr.system = _sys.rstrip() + "\n\n" + warning_text.strip()
    elif isinstance(_sys, list):
        _pr.system = list(_sys) + [{"role": "system", "content": warning_text.strip()}]
    else:
        logger.warning(
            "token_budget_warning: packed_request.system has unexpected type %s, skip",
            type(_sys).__name__,
        )
        return

    logger.warning(
        "soft_landing.token_budget_prompt_injected ratio=%.3f",
        verdict.ratio,
    )
