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
from prismclaw.schema.session.projection import SessionProjectionEngine

logger = logging.getLogger(__name__)


async def stream_llm(
    rt: LoopRuntime,
    state: RunLoopState,
) -> AsyncIterator[Any]:
    """LLM 流式调用。保留已有模块 call_llm_stream_with_outcome()。"""
    from prismclaw.engine.execution.control.kernel import evaluate_loop_control
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
        rt.terminated_by = TerminalReason.TIMEOUT
        from prismclaw.engine.execution.loop.terminal import mark_task_blocked

        await mark_task_blocked(rt, "timeout")
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
        run_budget=rt.run_budget,
        now=_now,
        phase="stream",
    )
    _remaining_hard = rt.run_state.loop_state.hard_at - _now
    if _ctrl.is_last_turn:
        _cap_s = min(rt.run_budget.cap_s, max(0.0, _remaining_hard))
    else:
        _cap_s = min(rt.run_budget.cap_s, max(0.0, _remaining_hard - rt.run_budget.reserve_s))

    # ── 软着陆引导：最后一轮时在 system 注入自然收尾指令 ──
    # 触发条件：is_last_turn=True（剩余时间 <= reserve_s 或只剩 1 次迭代机会）
    # 去重：rt.time_budget_warned 控制（独立于 iteration_budget_warned / token_budget_warned）
    # 注意：overflow recovery 会重建 prepared_turn，重试时如仍 is_last_turn
    # 会再注入一次——这是正确行为（新 request 需要新警告）
    if _ctrl.is_last_turn and not rt.time_budget_warned:
        rt.time_budget_warned = _append_system_directive(
            state,
            _SOFT_LANDING_PROMPT,
            reason="soft_landing",
            remaining_hard_s=f"{max(0.0, _remaining_hard):.1f}",
        )

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
                            rt.token_budget_warned = _append_system_directive(
                                state,
                                render_budget_warning(_verdict, soft_budget=_sb_cfg),
                                reason="token_budget_warning",
                                ratio=f"{_verdict.ratio:.3f}",
                            )
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
    )
    holder = _OutcomeHolder()
    async for event in call_llm_stream_with_outcome(rt, _pipeline_params, _outcome=holder):
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
            _snapshot = state.snapshot
            if _snapshot is not None:
                with contextlib.suppress(Exception):
                    _snapshot.compression_runtime.last_prompt_tokens = getattr(
                        _usage, "input_tokens", None
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
                        SessionProjectionEngine(rt.tree).history_compaction.get_count()
                        if rt.tree is not None
                        else 0
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

    if state.tree is None:
        logger.warning("overflow recovery: session tree unavailable")
        return False

    snapshot = state.snapshot
    if snapshot is None:
        logger.warning("overflow recovery: run snapshot unavailable")
        return False

    try:
        outcome = await recover_from_context_overflow(
            deps=rt.deps,
            tree=state.tree,
            request=rt.request,
            query_loop_state=prepared.query_loop_state,
            compression_runtime=snapshot.compression_runtime,
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
# 运行时指令注入（软着陆 / token 预算警告）
# ---------------------------------------------------------------------------

_SOFT_LANDING_PROMPT = """
<system_directive scope="final_turn">
本轮是最后一轮，必须给出最终回答，不再调用任何工具。
汇总已获取的信息与产物，直接回答用户最初的问题；未覆盖的部分如实说明。
不要提及预算、超时、轮次或工具冻结等内部状态。
</system_directive>
"""


def _append_system_directive(
    state: RunLoopState,
    text: str,
    *,
    reason: str,
    **fields: object,
) -> bool:
    """把运行时指令追加到打包后的 system，返回是否写入。

    system 恒为"单条消息的 content"，因此只有两种形态：
    - str：末尾追加段落
    - list[dict]：追加一个文本块 ``{"type": "text", ...}``（不是消息形状）

    注入发生在 packer 之后，故不重算 PackerFinalDone 的 sha1——那是打包层输出；
    实际发送字节的真值由 ``llm_request_start ... prefix_digest`` 提供。

    不抛异常、不修改 messages（保持 tool_use/tool_result 配对不变量）。
    """
    _pt = state.prepared_turn
    _pr = _pt.preflight.packed_request if _pt is not None and _pt.preflight is not None else None
    _body = text.strip()
    if _pr is None or not _body:
        return False
    _sys = getattr(_pr, "system", None)
    if isinstance(_sys, str):
        _pr.system = _sys.rstrip() + "\n\n" + _body
    elif isinstance(_sys, list):
        _pr.system = [*_sys, {"type": "text", "text": _body}]
    else:
        logger.info(
            "system_directive_skipped reason=%s system_type=%s",
            reason,
            type(_sys).__name__,
        )
        return False
    _extra = " ".join(f"{k}={v}" for k, v in fields.items())
    logger.info(
        "system_directive_applied reason=%s chars=%d%s",
        reason,
        len(_body),
        f" {_extra}" if _extra else "",
    )
    return True
