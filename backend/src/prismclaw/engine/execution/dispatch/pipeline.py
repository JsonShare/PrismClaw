"""LLM call pipeline: stream processing, chunk accumulation, error handling.

Extracted from ``runner.py`` to isolate the LLM interaction layer from the
main agent loop orchestration.  This module owns:

* Constructing the guarded stream iterator
* Accumulating text / reasoning / tool-call deltas
* Heartbeat throttling during generation
* First-token timing (TTFT)
* LLM metrics snapshot construction
* Per-chunk event emission (TextChunk, ReasoningChunk, HeartbeatEvent)
* Retry / timeout / abort error classification

The caller (``run_agent_stream``) retains responsibility for:
* Pipeline preparation (``PreRequestPipeline.run``)
* Cache-break detection and token accounting
* Budget enforcement
* Post-response governance (no-tool-turn logic, completion intake)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

from prismclaw.schema.llm.errors import LLMError
from prismclaw.schema.llm.types import LLMResponse, LLMUsage
from prismclaw.engine.foundation.runtime_util import (
    AgentAbortedError,
    AgentTimeoutError,
    is_abort_set,
    iterate_with_deadline,
)
from prismclaw.schema.llm.tool_call_types import ToolCallBuffer
from prismclaw.engine.execution.loop.housekeeping import _heartbeat_decision, _log_exec_phase
from prismclaw.engine.execution.loop.arbiter import RunContext
from prismclaw.engine.trajectory.bus_context import LLMMetricsSnapshot, trajectory_runtime
from prismclaw.engine.foundation.metrics import emit
from prismclaw.schema.content.streaming import ErrorEvent, HeartbeatEvent, ReasoningChunk, TextChunk
from prismclaw.engine.execution.dispatch.turn_utils import strip_thinking
from prismclaw.schema.session.types import generate_entry_id, now_iso

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Streaming result dataclass
# ---------------------------------------------------------------------------

@dataclass
class LLMStreamResult:
    """Result of a completed (successful) LLM streaming call.

    Contains the assembled response plus timing metadata; does NOT contain
    the per-chunk streaming events (those were yielded during the call).
    """

    response: LLMResponse
    llm_end: float
    metrics_snapshot: LLMMetricsSnapshot
    first_token_at: float | None


@dataclass
class LLMStreamError:
    """Terminal error produced by the LLM streaming call.

    The caller should ``yield error_event`` and ``return``.
    """

    error_event: ErrorEvent


@dataclass
class LLMStreamRetry:
    """Non-terminal: the current attempt should be retried (idle timeout / transient)."""

    reason: str  # "idle_timeout" | "compression_restart"


@dataclass
class LLMStreamNoResponse:
    """All attempts exhausted without a response — caller should continue loop."""

    pass


# Union of possible outcomes from a single call_llm_stream invocation.
LLMStreamOutcome = LLMStreamResult | LLMStreamError | LLMStreamRetry | LLMStreamNoResponse


# ---------------------------------------------------------------------------
# Pipeline config (params the caller passes in)
# ---------------------------------------------------------------------------

@dataclass
class PipelineParams:
    """Parameters needed by ``call_llm_stream`` that are derived from the
    outer loop state but are NOT stored on ``RunContext``.
    """

    prepared_turn: Any
    effective_model: str
    remaining_run: float | None
    run_deadline_s: float
    run_started: float
    traj_turn_counter: int
    iteration_pre_advance: int  # _iter (pre-advance) for audit events
    abort_event: Any
    max_idle_retries: int = 1
    cap_s: float = 0.0  # 单请求墙钟硬上限（effort 派生），0 表示不设
    is_last_turn: bool = False  # cap_s 超时后非最后一轮重试一次，最后一轮交付部分内容
    reasoning_effort: str | None = None  # effort 派生的推理强度（low/medium/high），None=模型默认


# ---------------------------------------------------------------------------
# Outcome holder — mutable container for the async generator to write its
# final result into, since Python async generators cannot return a value.
# ---------------------------------------------------------------------------

class _OutcomeHolder:
    """Mutable slot for the generator to write its outcome into."""
    __slots__ = ("outcome",)
    def __init__(self) -> None:
        self.outcome: LLMStreamOutcome | None = None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def call_llm_stream_with_outcome(
    ctx: RunContext,
    params: PipelineParams,
    *,
    _outcome: _OutcomeHolder,
) -> AsyncIterator[Any]:
    """Call the LLM and yield streaming events in real-time.

    Events (TextChunk, ReasoningChunk, HeartbeatEvent) are yielded as they
    arrive from the LLM, preserving the natural inter-chunk timing that allows
    downstream consumers (e.g. WebSocket → browser) to process them correctly.

    The final outcome is written into ``_outcome.outcome`` when the generator
    finishes.  The caller creates an ``_OutcomeHolder`` and passes it in::

        oh = _OutcomeHolder()
        async for event in call_llm_stream_with_outcome(ctx, params, _outcome=oh):
            yield event
        outcome = oh.outcome  # LLMStreamResult / LLMStreamError / ...
    """
    deps = ctx.deps
    request = ctx.request
    tree = ctx.tree
    snapshot = ctx.snapshot
    abort_event = params.abort_event

    _llm_start = time.monotonic()
    _first_token_recorded = False
    _first_token_at_local: float | None = None
    _generating_logged = False
    _hb_last_chunk_at = _llm_start
    _hb_last_emit_at = _llm_start
    _hb_delta_count = 0
    _hb_delta_chars = 0
    _hb_partial_name: str | None = None

    response: LLMResponse | None = None

    def _run_timed_out() -> bool:
        return (
            params.run_deadline_s > 0
            and (time.monotonic() - params.run_started) > params.run_deadline_s
        )

    for attempt in range(params.max_idle_retries + 1):
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls_buffer = ToolCallBuffer()
        finish_reason: str | None = None
        stream_usage = LLMUsage()
        preflight = params.prepared_turn.preflight
        if preflight is None:
            raise RuntimeError("PreparedTurn.preflight is required before calling the LLM")

        try:
            _log_exec_phase(
                conv_id=request.session_id,
                phase="waiting_llm",
                iteration=ctx.iteration,
                elapsed_s=time.monotonic() - params.run_started,
            )
            stream_iter = deps.llm.stream(
                request=preflight.packed_request,
                model=params.effective_model,
                tools=params.prepared_turn.tools_for_llm or None,
                prompt_cache_runtime=snapshot.prompt_cache_runtime,
                idle_seconds=deps.config.timeouts.idle_seconds,
                abort_event=abort_event,
                reasoning_effort=params.reasoning_effort,
            )
            # 单请求墙钟硬上限 cap_s（effort 派生）；cap_s 未设时回退到剩余 run 时间。
            _request_deadline = (
                params.cap_s if params.cap_s and params.cap_s > 0
                else (params.remaining_run or 0.0)
            )
            guarded_iter = iterate_with_deadline(
                stream_iter,
                deadline_s=_request_deadline,
                abort_event=abort_event,
                kind="run",
            )
            async for chunk in guarded_iter:
                if _run_timed_out():
                    _outcome.outcome = LLMStreamError(
                        error_event=ErrorEvent(
                            error_code="timeout",
                            message=f"run exceeded {params.run_deadline_s}s hard deadline during stream",
                        ),
                    )
                    return
                if is_abort_set(abort_event):
                    logger.info(
                        "runner abort during LLM stream: conv_id=%s, iteration=%s, text_parts=%d",
                        request.session_id,
                        ctx.iteration,
                        len(text_parts),
                    )
                    _outcome.outcome = LLMStreamError(
                        error_event=ErrorEvent(
                            error_code="aborted",
                            message="run aborted during llm stream",
                        ),
                    )
                    return
                if chunk.reasoning_delta:
                    if not _first_token_recorded:
                        _first_token_recorded = True
                        _first_token_at_local = time.monotonic()
                        emit(
                            "turn.llm.first_token_ms",
                            (_first_token_at_local - _llm_start) * 1000.0,
                            kind="histogram",
                        )
                    reasoning_parts.append(chunk.reasoning_delta)
                    yield ReasoningChunk(reasoning=chunk.reasoning_delta)
                if chunk.text_delta:
                    if not _first_token_recorded:
                        _first_token_recorded = True
                        _first_token_at_local = time.monotonic()
                        emit(
                            "turn.llm.first_token_ms",
                            (_first_token_at_local - _llm_start) * 1000.0,
                            kind="histogram",
                        )
                    text_parts.append(chunk.text_delta)
                    yield TextChunk(text=chunk.text_delta)
                if chunk.tool_call_deltas:
                    if not _first_token_recorded:
                        _first_token_recorded = True
                        _first_token_at_local = time.monotonic()
                        emit(
                            "turn.llm.first_token_ms",
                            (_first_token_at_local - _llm_start) * 1000.0,
                            kind="histogram",
                        )
                    tool_calls_buffer.absorb(chunk.tool_call_deltas)
                    for _dc in chunk.tool_call_deltas:
                        _hb_delta_count += 1
                        _hb_delta_chars += len(_dc.get("function", {}).get("arguments", "") or "")
                        _name = _dc.get("function", {}).get("name")
                        if _name:
                            _hb_partial_name = _name
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason
                if chunk.usage:
                    stream_usage = chunk.usage
                if not _generating_logged and (chunk.text_delta or chunk.tool_call_deltas):
                    _generating_logged = True
                    _log_exec_phase(
                        conv_id=request.session_id,
                        phase="generating",
                        iteration=ctx.iteration,
                        elapsed_s=time.monotonic() - params.run_started,
                    )
                _hb_last_chunk_at = time.monotonic()
                _hb_detail = _heartbeat_decision(
                    now=_hb_last_chunk_at,
                    turn_start=_llm_start,
                    last_hb_at=_hb_last_emit_at,
                    last_chunk_at=_hb_last_chunk_at,
                    delta_count=_hb_delta_count,
                    delta_chars=_hb_delta_chars,
                    partial_name=_hb_partial_name,
                    current_phase="generating",
                )
                if _hb_detail is not None:
                    _hb_last_emit_at = time.monotonic()
                    _hb_delta_count = 0
                    _hb_delta_chars = 0
                    yield HeartbeatEvent(
                        phase="generating",
                        detail=_hb_detail,
                        elapsed_s=time.monotonic() - params.run_started,
                    )

            # ── Assemble response ──
            assembled_text = "".join(text_parts)
            assembled_tool_calls = tool_calls_buffer.finalize()
            response = LLMResponse(
                text=assembled_text,
                tool_calls=assembled_tool_calls,
                usage=stream_usage,
                finish_reason=finish_reason,
                reasoning="".join(reasoning_parts),
            )
            _llm_end = time.monotonic()
            _metrics_snap = LLMMetricsSnapshot.build(
                model=params.effective_model,
                finish_reason=finish_reason,
                usage_input_tokens=response.usage.input_tokens or None,
                usage_output_tokens=response.usage.output_tokens or None,
                usage_cache_read_tokens=response.usage.cache_read_input_tokens or None,
                usage_cache_write_tokens=response.usage.cache_creation_input_tokens or None,
                usage_reasoning_tokens=response.usage.reasoning_tokens or None,
                llm_start=_llm_start,
                first_token_at=_first_token_at_local,
                llm_end=_llm_end,
            )
            try:
                _rt_ctx_metrics = trajectory_runtime.get(None)
                if _rt_ctx_metrics is not None:
                    _rt_ctx_metrics.llm_metrics = _metrics_snap
                    _rt_ctx_metrics.assistant_reasoning = response.reasoning or None
            except Exception:
                pass
            await _publish_llm_response(
                session_id=request.session_id,
                turn_counter=params.traj_turn_counter,
                iteration=params.iteration_pre_advance,
                metrics=_metrics_snap,
                assistant_text=strip_thinking(response.text or ""),
                tool_calls=response.tool_calls,
            )
            _outcome.outcome = LLMStreamResult(
                response=response,
                llm_end=_llm_end,
                metrics_snapshot=_metrics_snap,
                first_token_at=_first_token_at_local,
            )
            return

        except AgentTimeoutError as te:
            if (
                te.kind == "idle"
                and attempt < params.max_idle_retries
                and not text_parts
                and not tool_calls_buffer
            ):
                continue
            # cap_s 超时：非最后一轮重试一次（给模型再一次机会），最后一轮交付已有内容。
            if (
                te.kind == "run"
                and not params.is_last_turn
                and attempt < 1
                and not text_parts
                and not tool_calls_buffer
            ):
                continue
            await _publish_llm_failure(
                session_id=request.session_id,
                turn_counter=params.traj_turn_counter,
                iteration=params.iteration_pre_advance,
                model=params.effective_model,
                error_code="timeout",
                error_message=str(te),
                llm_start=_llm_start,
                first_token_at=_first_token_at_local,
            )
            _outcome.outcome = LLMStreamError(
                error_event=ErrorEvent(error_code="timeout", message=str(te)),
            )
            return

        except AgentAbortedError:
            await _publish_llm_failure(
                session_id=request.session_id,
                turn_counter=params.traj_turn_counter,
                iteration=params.iteration_pre_advance,
                model=params.effective_model,
                error_code="aborted",
                error_message="run aborted during llm call",
                llm_start=_llm_start,
                first_token_at=_first_token_at_local,
            )
            _outcome.outcome = LLMStreamError(
                error_event=ErrorEvent(error_code="aborted", message="run aborted during llm call"),
            )
            return

        except LLMError as exc:
            if (
                exc.code == "timeout"
                and "idle timeout after" in str(exc)
                and attempt < params.max_idle_retries
                and not text_parts
                and not tool_calls_buffer
            ):
                # LLM 空闲超时重试不写入循环状态；max_idle_retries 是本地
                # 硬上限，达到即继续下一轮。
                continue

            from prismclaw.schema.session.types import CustomEntry, TRANSIENT_RETRY_NOTICE_CUSTOM_TYPE
            from prismclaw.engine.execution.loop.tree_writer import _append

            if exc.code in ("timeout", "rate_limit", "server_error", "connection_error"):
                logger.warning("LLM API transient error (%s): %s. Attempting to retry.", exc.code, str(exc))
                if len(tree.order) > 3:
                    retry_note = CustomEntry(
                        id=generate_entry_id(set(tree.entries.keys())),
                        parent_id=tree.leaf_id,
                        timestamp=now_iso(),
                        custom_type=TRANSIENT_RETRY_NOTICE_CUSTOM_TYPE,
                        data={
                            "error_code": exc.code,
                            "message": str(exc),
                        },
                    )
                    await _append(deps, tree, retry_note)
                # 连接错误若已向客户端产出 token/工具调用，重发会从头再流一遍
                # 造成重复输出——仅在尚未产出内容时重试；已产出则保留部分内容
                # 并以 connection_error 终态结束本回合。
                _can_retry = attempt < params.max_idle_retries
                if exc.code == "connection_error" and (text_parts or tool_calls_buffer):
                    _can_retry = False
                if _can_retry:
                    continue

            logger.error("LLM error: code=%s message=%s", exc.code, str(exc)[:500])
            await _publish_llm_failure(
                session_id=request.session_id,
                turn_counter=params.traj_turn_counter,
                iteration=params.iteration_pre_advance,
                model=params.effective_model,
                error_code=exc.code,
                error_message=str(exc),
                llm_start=_llm_start,
                first_token_at=_first_token_at_local,
            )
            _outcome.outcome = LLMStreamError(
                error_event=ErrorEvent(error_code=exc.code, message=str(exc)),
            )
            return

    # All attempts exhausted without response
    _outcome.outcome = LLMStreamNoResponse()


# ---------------------------------------------------------------------------
# LLM response-side audit publish helpers (module-level for testability)
#
# These are intentionally module-level (not nested closures) so they can be
# unit-tested without spinning up the full run_agent_stream. They read the
# SessionBus from the current ContextVar and publish typed events that the
# trajectory projection folds into pending.llm_* fields.
#
# Critical invariants:
#   * Both helpers NO-OP when bus is None or NOOP_BUS (feature disabled).
#   * Both swallow all exceptions — audit failures MUST NOT break core flow.
#   * Retry paths in caller do NOT call these — only final outcome per
#     (turn, iter) is published, mirroring how `response` is only assigned
#     on the success path.
# ---------------------------------------------------------------------------

async def _publish_llm_response(
    *,
    session_id: str,
    turn_counter: int,
    iteration: int,
    metrics: LLMMetricsSnapshot,
    assistant_text: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
) -> None:
    """成功路径 publish LLMResponseReceived（迭代级审计记录链路）。

    Called right after the LLM response returns, before `break`.
    SessionIterationFinished fires later → pending still alive → fields
    land on the same record that gets committed.

    All timing/usage arithmetic lives in the ``metrics`` snapshot (built
    once by the caller and also stashed into the trajectory runtime ctx
    for the assistant cell publish) — no duplicated computation here.
    """
    try:
        from prismclaw.engine.trajectory.bus import current_bus
        from prismclaw.engine.trajectory.bus_events import LLMResponseReceived

        _bus_resp = current_bus()
        if _bus_resp is None or _bus_resp._is_noop:
            return
 # derive content previews at the publish boundary so
        # downstream subscribers (projection → persistence → API → UI)
        # receive ready-to-store strings, not raw response objects.
        _assistant_preview: str | None = None
        if assistant_text:
            _assistant_preview = assistant_text[:500]
            if len(assistant_text) > 500:
                _assistant_preview = _assistant_preview + "…"
        _tool_calls_summary: tuple[str, ...] = ()
        if tool_calls:
            import json as _tc_json
            _summaries: list[str] = []
            for _tc in tool_calls:
                try:
                    _tc_name = str(_tc.get("name") or _tc.get("function", {}).get("name") or "?")
                    _tc_args = _tc.get("arguments") or _tc.get("function", {}).get("arguments") or {}
                    if isinstance(_tc_args, str):
                        _tc_args_str = _tc_args
                    else:
                        _tc_args_str = _tc_json.dumps(_tc_args, ensure_ascii=False, default=str)
                    _summaries.append(f"{_tc_name}({_tc_args_str[:200]})")
                except Exception:
                    _summaries.append("?")
            _tool_calls_summary = tuple(_summaries)
        _bus_resp.publish(LLMResponseReceived(
            session_id=session_id,
            turn_counter=turn_counter,
            iteration=iteration,
            model=metrics.model or "",
            finish_reason=metrics.finish_reason,
            usage_input_tokens=metrics.usage_input_tokens,
            usage_output_tokens=metrics.usage_output_tokens,
            usage_cache_read_tokens=metrics.usage_cache_read_tokens,
            usage_cache_write_tokens=metrics.usage_cache_write_tokens,
            usage_reasoning_tokens=metrics.usage_reasoning_tokens,
            ttft_ms=metrics.ttft_ms,
            total_ms=metrics.total_ms,
            completed_at_ms=metrics.completed_at_ms,
            assistant_text_preview=_assistant_preview,
            tool_calls_summary=_tool_calls_summary,
        ))
    except Exception:
        logger.debug("audit publish failed", exc_info=True)


async def _publish_llm_failure(
    *,
    session_id: str,
    turn_counter: int,
    iteration: int,
    model: str,
    error_code: str,
    error_message: str,
    llm_start: float,
    first_token_at: float | None,
) -> None:
    """失败路径 publish LLMRequestFailed.

    Called from each except branch of the LLM call site (AgentTimeoutError /
    AgentAbortedError / LLMError final failure) before `yield ErrorEvent`.
    已流出的部分正文由调用方（Web 收尾路径）负责持久化，本层不再落库。

    重构清理:
      * 删除 attempt 参数 (无消费者, 见 LLMRequestFailed docstring)
      * 删除 _log_exec_phase(phase='llm_failed') (phase log 6 写入方 0 读取方)
      * 删除 error_message 双重截断 (pydantic validator 已截断到 256)
      * 保留 logger.warning 统一 3 个失败分支的日志格式 (原 2/3 分支无 logger)
    """
    try:
        import time as _traj_t5
        # 结构化 logger.warning, 统一 3 个失败分支的日志格式
        _ttft_str = (
            f"{((first_token_at - llm_start) * 1000.0):.0f}"
            if first_token_at is not None else "none"
        )
        logger.warning(
            "llm_request_failed session_id=%s iteration=%d "
            "error_code=%s ttft_ms=%s total_ms=%.1f model=%s",
            session_id,
            iteration,
            error_code,
            _ttft_str,
            (time.monotonic() - llm_start) * 1000.0,
            model,
        )
        from prismclaw.engine.trajectory.bus import current_bus
        from prismclaw.engine.trajectory.bus_events import LLMRequestFailed
        _bus_fail = current_bus()
        if _bus_fail is None or _bus_fail._is_noop:
            return
        _ttft_val = (
            (first_token_at - llm_start) * 1000.0
            if first_token_at is not None else None
        )
        _bus_fail.publish(LLMRequestFailed(
            session_id=session_id,
            turn_counter=turn_counter,
            iteration=iteration,
            model=model,
            error_code=error_code,
            error_message=error_message,
            ttft_ms=_ttft_val,
            total_ms=(time.monotonic() - llm_start) * 1000.0,
            failed_at_ms=int(_traj_t5.time() * 1000),
        ))
    except Exception:
        logger.debug("audit publish failed", exc_info=True)
