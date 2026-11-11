"""Subagent 侧 run_loop 骨架适配器（P3b 步骤 2）。

将 LoopMixin._loop 的手写 while 循环迁移到 engine/execution/loop.run_loop 骨架，
行为由 tests/engine/subagent/test_loop_characterization.py 的 parity 基准锁定
（断言逐字不变）。

骨架语义（0a 冒烟实证，见 tests/engine/execution/loop/test_run_loop_minimal.py）：
- preflight should_continue 在 LLM 调用之后、iteration 递增之前运行
  事件用 ``LoopEvent(site=...)`` 声明位置，裁决返回 ``Decision``
- 骨架自身无 abort/墙钟感知 → 终局检查放在 stream_llm 入口（对齐现实现
  轮顶检查 L50-62，发生在下一次 LLM 调用之前）
- 消费至枯竭或异常，从不 break → 无需 aclose

turns 映射（现实现 turns 在 tool_calls 判断前自增；骨架 iteration 在轮底自增）：
- 自然完成 / break_flag / max_turns / dispatch 前超时 → turns = iteration + 1
- LLM 调用前 abort / 超时 → turns = iteration
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any, AsyncIterator

from prismclaw.engine.execution.control.kernel import Decision, LoopEvent
from prismclaw.engine.execution.dispatch.pipeline import LLMStreamError
from prismclaw.engine.execution.loop.agent_loop import run_loop
from prismclaw.engine.execution.loop.types import (
    ExecutePort,
    LoopConfig,
    PreparePort,
    RunLoopState,
    StreamPort,
)
from prismclaw.engine.execution.output.tool_result_rendering import (
    tool_result_to_llm_content,
)
from prismclaw.engine.subagent.core._result_types import SettledSubagentToolSurface
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.content.streaming import ErrorEvent
from prismclaw.schema.llm.request import PackedLLMRequest

if TYPE_CHECKING:
    from prismclaw.engine.subagent.core._loop_mixin import LoopMixin

logger = logging.getLogger(__name__)

_OUTCOME_BY_CAUSE = {
    "natural": "success",
    "break_flag": "success",
    "max_turns": "max_turns_exceeded",
    "aborted": "aborted",
    "timeout": "timeout",
    "blocked": "failed",
}

_TERMINAL_STATUS = {
    "success": "completed",
    "aborted": "aborted",
    "max_turns_exceeded": "failed",
}


async def run_subagent_turn_loop(
    instance: "LoopMixin",
    *,
    messages: list[dict[str, Any]],
    system_prompt: str,
    model: str,
    max_turns: int,
    timeout_s: float,
    tool_surface: SettledSubagentToolSurface,
    tool_schemas: list[dict[str, Any]],
    sub_ctx: ToolContext,
    parent_ctx: ToolContext,
    manifest: Any,
) -> tuple[str, int, dict[str, int]]:
    """Run the canonical sub-agent turn loop on the shared run_loop skeleton.

    返回 (outcome, turns, usage_acc)；outcome 字符串与旧 _loop 完全一致。
    """
    emitter = instance._resolve_progress_emitter(sub_ctx, manifest)
    loop_started_at = time.monotonic()
    # timeout_s <= 0 表示「无墙钟上限」（fork 子代理沿用主流程哲学）：
    # deadline 置 None，整个循环不检查时间。fork 场景通常 max_turns 也为 0，
    # 此时退出条件仅剩 abort / 正常完成 / 单工具时限。
    deadline = (loop_started_at + timeout_s) if timeout_s > 0 else None
    # 骨架终止原因与末轮计数：cause 由 should_continue / stream_llm 入口写入
    end: dict[str, Any] = {"cause": None, "turns": 0, "llm_turn": None}

    state = RunLoopState(tool_ctx=sub_ctx)

    async def prepare_next_turn(_s: RunLoopState) -> None:
        """Prepare 端口空实现：子代理无上下文重装配。

        上下文由调用方 ``messages`` 列表持有（本函数每次 LLM 往返直接
        append），不存在 prepare 阶段。
        """
        return None

    async def stream_llm(s: RunLoopState) -> AsyncIterator[None]:
        # 轮顶终局检查（对齐现实现 L50-62：abort / 墙钟，均在 LLM 调用之前）
        if sub_ctx.abort.is_set():
            end["cause"], end["turns"] = "aborted", s.iteration
            s.llm_outcome = LLMStreamError(
                error_event=ErrorEvent(
                    error_code="aborted",
                    message="子代理在 LLM 调用前被中止",
                ),
            )
            return
            yield  # pragma: no cover — 使本函数成为 async generator
        if deadline is not None and deadline - time.monotonic() <= 0:
            logger.warning(
                "AgentInstance '%s' exhausted time budget before LLM call.",
                manifest.name,
            )
            end["cause"], end["turns"] = "timeout", s.iteration
            s.llm_outcome = LLMStreamError(
                error_event=ErrorEvent(
                    error_code="timeout",
                    message="子代理时间预算在 LLM 调用前已耗尽",
                ),
            )
            return
            yield  # pragma: no cover

        # D1 advisory 预留通道（当前恒空；启用后 advisory 经此进下一轮上下文）
        if s.pending_messages:
            messages.extend(s.pending_messages)
            s.pending_messages = []

        await asyncio.sleep(0)
        logger.info(
            "llm_request_start session_id=%s subagent=%s turn=%d model=%s tools=%d",
            sub_ctx.session_id,
            manifest.name,
            s.iteration + 1,
            model,
            len(tool_schemas),
        )
        _t0 = time.monotonic()
        end["llm_turn"] = None  # 本轮 LLM 未完成；异常终态 turn 以此区分阶段
        try:
            response = await asyncio.wait_for(
                instance._llm.complete(
                    request=PackedLLMRequest(
                        messages=messages,
                        system=system_prompt,
                        context_payload=None,
                        cache_strategy={},
                    ),
                    model=model,
                    tools=tool_schemas,
                    abort_event=sub_ctx.abort,
                ),
                timeout=(
                    max(deadline - time.monotonic(), 0.001)
                    if deadline is not None
                    else None
                ),
            )
        except TimeoutError:
            logger.info(
                "llm_request_end session_id=%s subagent=%s turn=%d status=timeout duration_ms=%d",
                sub_ctx.session_id,
                manifest.name,
                s.iteration + 1,
                int((time.monotonic() - _t0) * 1000),
            )
            logger.warning(
                "AgentInstance '%s' timed out waiting for LLM response.",
                manifest.name,
            )
            end["cause"], end["turns"] = "timeout", s.iteration
            s.llm_outcome = LLMStreamError(
                error_event=ErrorEvent(
                    error_code="timeout",
                    message="LLM 调用超出子代理时间预算",
                ),
            )
            return
            yield  # pragma: no cover

        logger.info(
            "llm_request_end session_id=%s subagent=%s turn=%d status=ok duration_ms=%d",
            sub_ctx.session_id,
            manifest.name,
            s.iteration + 1,
            int((time.monotonic() - _t0) * 1000),
        )
        end["llm_turn"] = s.iteration + 1  # 对齐旧实现：LLM 成功后 turns 即自增

        usage = getattr(response, "usage", None)
        if usage is not None:
            s.ledger.total_input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
            s.ledger.total_output_tokens += int(getattr(usage, "output_tokens", 0) or 0)

        assistant_text = getattr(response, "text", "") or ""
        tool_calls = list(getattr(response, "tool_calls", None) or [])
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": assistant_text}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)

        turn_no = s.iteration + 1
        instance._emit_progress(
            emitter,
            sub_ctx=sub_ctx,
            manifest=manifest,
            turn=turn_no,
            max_turns=max_turns,
            partial_status="tool_calling" if tool_calls else "thinking",
            current_tool=None,
            started_at=loop_started_at,
        )
        s.llm_response = response
        return
        yield  # pragma: no cover

    async def execute_tools(
        s: RunLoopState, response: Any, allowed_calls: list
    ) -> AsyncIterator[None]:
        # 子代理无 before_tool_call 门控（恒 None），allowed_calls 即全量解析结果；
        # 分发走原始 tool_calls（与旧 _loop 同一入参形态），坏调用由
        # execute_preflighted_tool_calls 自行转错误结果。
        tool_calls = list(getattr(response, "tool_calls", None) or [])
        turn_no = s.iteration + 1
        instance._emit_progress(
            emitter,
            sub_ctx=sub_ctx,
            manifest=manifest,
            turn=turn_no,
            max_turns=max_turns,
            partial_status="tool_running",
            current_tool=instance._first_tool_name(tool_calls),
            started_at=loop_started_at,
        )
        # dispatch 前墙钟门（对齐现实现 L168-174：不打工具，assistant tool_calls
        # 保持孤儿状态——现状语义，parity 保持）
        if deadline is not None and deadline - time.monotonic() <= 0:
            logger.warning(
                "AgentInstance '%s' exhausted time budget before tool dispatch.",
                manifest.name,
            )
            end["cause"], end["turns"] = "timeout", turn_no
            return
            yield  # pragma: no cover
        tool_results = await instance._dispatch_tool_calls(
            tool_calls,
            sub_ctx=sub_ctx,
            parent_ctx=parent_ctx,
            manifest=manifest,
        )
        instance._emit_progress(
            emitter,
            sub_ctx=sub_ctx,
            manifest=manifest,
            turn=turn_no,
            max_turns=max_turns,
            partial_status="tool_calling",
            current_tool=None,
            started_at=loop_started_at,
        )
        for call, result in zip(tool_calls, tool_results, strict=False):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", "") or result.tool_call_id,
                    "content": tool_result_to_llm_content(result),
                }
            )
        s.last_tool_results = list(tool_results)
        return
        yield  # pragma: no cover

    async def should_continue(
        s: RunLoopState, response: Any, *, event: LoopEvent,
    ) -> Decision:
        if end["cause"] is not None:
            # 终局已定（如 dispatch 前墙钟超时），post 相位不得覆盖
            return Decision("TERMINATE", "subagent_terminal_pre_set")
        if event.site == "preflight":
            tool_calls = list(getattr(response, "tool_calls", None) or [])
            if not tool_calls:
                # 自然完成（thinking 已在 stream_llm 发出）
                end["cause"], end["turns"] = "natural", s.iteration + 1
                return Decision("COMPLETE", "subagent_natural")
            return Decision("PROCEED", "ok")
        if event.site == "all_blocked":
            # 子代理无全拒治理路径（before_tool_call 恒 None）；防御性终止
            end["cause"], end["turns"] = "blocked", s.iteration
            return Decision("TERMINATE", "subagent_blocked")
        # site == "post"
        if sub_ctx.should_break_after_results:
            end["cause"], end["turns"] = "break_flag", s.iteration + 1
            return Decision("COMPLETE", "subagent_break_flag")
        if max_turns > 0 and s.iteration + 1 >= max_turns:
            end["cause"], end["turns"] = "max_turns", s.iteration + 1
            return Decision("COMPLETE", "subagent_max_turns")
        return Decision("PROCEED", "ok")

    config = LoopConfig(
        prepare=PreparePort(prepare_next_turn=prepare_next_turn),
        stream=StreamPort(stream_llm=stream_llm),
        execute=ExecutePort(execute_tools=execute_tools),
        should_continue=should_continue,
    )

    try:
        async for _event in run_loop(state, config):
            pass  # 子代理无事件流消费者；骨架内部事件一律忽略
    except asyncio.CancelledError:
        instance._emit_terminal(
            emitter,
            sub_ctx=sub_ctx,
            manifest=manifest,
            turn=end["llm_turn"] if end["llm_turn"] is not None else state.iteration,
            max_turns=max_turns,
            partial_status="aborted",
            started_at=loop_started_at,
        )
        raise
    except Exception:
        instance._emit_terminal(
            emitter,
            sub_ctx=sub_ctx,
            manifest=manifest,
            turn=end["llm_turn"] if end["llm_turn"] is not None else state.iteration,
            max_turns=max_turns,
            partial_status="failed",
            started_at=loop_started_at,
        )
        raise

    if end["cause"] is None:
        # 防御：骨架终止必经上述路径之一
        outcome, turns = "failed", state.iteration
    else:
        outcome = _OUTCOME_BY_CAUSE[end["cause"]]
        turns = end["turns"]

    usage_acc = {
        "input_tokens": state.ledger.total_input_tokens,
        "output_tokens": state.ledger.total_output_tokens,
    }

    instance._emit_terminal(
        emitter,
        sub_ctx=sub_ctx,
        manifest=manifest,
        turn=turns,
        max_turns=max_turns,
        partial_status=_TERMINAL_STATUS.get(outcome, "failed"),
        started_at=loop_started_at,
    )
    return outcome, turns, usage_acc
