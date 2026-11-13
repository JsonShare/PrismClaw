"""AgentLoop — 纯控制流骨架。

约束：
- 只消费 ``LoopConfig`` 的三端口 + 可选插槽，不 import 任何 deps 子系统；
- 所有状态走 ``RunLoopState``，所有输出走 yield；
- 终止裁决唯一来自 ``should_continue``（CompletionKernel 在装配层绑定）。

循环：单层 ``while True``。压缩重启由 ``TurnUpdate.restart`` 回到顶部。
"""
from __future__ import annotations

import logging
import time as _time
from collections.abc import AsyncIterator
from typing import Any

from prismclaw.engine.execution.control.kernel import LoopEvent
from prismclaw.engine.execution.dispatch.pipeline import (
    LLMStreamError,
    LLMStreamNoResponse,
    LLMStreamRetry,
)
from prismclaw.engine.execution.loop._loop_helpers import (
    _create_blocked_tool_result,
    _create_clarify_tool_end,
    _parse_tool_calls,
    _tool_call_start_events,
)
from prismclaw.engine.execution.loop.types import (
    AfterToolCallContext,
    BeforeToolCallContext,
    LoopConfig,
    RunLoopState,
)
from prismclaw.schema.content.streaming import ErrorEvent, IterationBoundary

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# run_loop — 纯控制流骨架
# ---------------------------------------------------------------------------

async def run_loop(
    state: RunLoopState,
    config: LoopConfig,
) -> AsyncIterator[Any]:
    """驱动单次 run：prepare → stream → gate → execute → 裁决，直到终态。"""
    # 回合级计时起点：仅首次进入时打点（TurnPerf 跨迭代累加，决策不读）。
    state.perf.started_at = state.perf.started_at or _time.monotonic()
    while True:
        # ── 迭代开始计时 ──
        _iter_start = _time.monotonic()
        state.timing.iteration = state.perf.llm_calls + 1
        state.iteration = state.perf.llm_calls
        state.timing.pre_request_pipeline_ms = 0.0
        state.timing.llm_stream_ms = 0.0
        state.timing.tool_execution_ms = 0.0

        # ── Step 1: steering（iteration 边界注入用户中途纠偏）──
        # get_steering_messages 负责 drain + 落树（role="user"）；落树的
        # steering 随树进入下一轮 prepare 投影。pending_messages 不走这里：
        # 它们是 LLM 注入消息（预算警告 / 工具循环指导），由 prepare 每轮
        # 合并进 query_loop_state，不 yield。
        if config.get_steering_messages is not None:
            _steering_texts = await config.get_steering_messages()
            if _steering_texts:
                logger.info(
                    "steering: injected %d user message(s) at iteration %d boundary",
                    len(_steering_texts),
                    state.perf.llm_calls,
                )

        # ── Step 2: prepare turn + stream LLM ──

        # 2a. prepare turn (在 LLM 调用之前准备上下文)
        _prep_start = _time.monotonic()
        turn_update = await config.prepare_next_turn(state)
        _prep_ms = (_time.monotonic() - _prep_start) * 1000
        state.timing.pre_request_pipeline_ms = _prep_ms
        state.perf.prep_ms += _prep_ms
        if turn_update is not None:
            # 压缩重启的重放注入；拷贝避免与生产方列表别名。
            if turn_update.pending_messages is not None:
                state.pending_messages = list(turn_update.pending_messages)
            if turn_update.restart:
                # CompactionEntry 已落树，下一轮 prepare 从 tree 重建即含
                # 摘要。restart 发生在 LLM 调用之前，本轮的 appended 注入
                # 尚未送达模型且不落树，不重放即永久丢失。
                continue
            if state.terminal_error is not None:
                yield state.terminal_error
                return
            # 非 restart 的 TurnUpdate：pending 已应用，回到顶部重新注入。
            continue

        # prepare 里 should_stop 已设 terminal_error——直接走终态，不再调 LLM
        if state.terminal_error is not None:
            yield state.terminal_error
            return

        # ── Iteration boundary: LLM start ──
        yield IterationBoundary(iteration=state.perf.llm_calls, kind="llm_start")

        # 2b. stream LLM
        _llm_start = _time.monotonic()
        state.perf.llm_calls += 1
        async for event in config.stream_llm(state):
            yield event
        _llm_ms = (_time.monotonic() - _llm_start) * 1000
        state.timing.llm_stream_ms = _llm_ms
        state.perf.llm_ms += _llm_ms

        # 2c. LLM 终态
        llm_outcome = state.llm_outcome
        if llm_outcome is not None:
            if isinstance(llm_outcome, LLMStreamError):
                yield llm_outcome.error_event
                return
            if isinstance(llm_outcome, LLMStreamRetry):
                # LLM 层重试：不写循环状态，回顶部重发。
                continue
            if isinstance(llm_outcome, LLMStreamNoResponse):
                continue

        response = state.llm_response
        if response is None:
            continue

        # 只标记当前 iteration 是否触发 max_tokens：正常完成时重置，
        # 避免上一轮截断污染本轮终止判定。
        state.max_tokens_hit_in_step = (
            getattr(response, "finish_reason", None) == "length"
        )

        # ── Step 2d: should_continue (preflight) ──
        # 覆盖 budget / abort / max_iterations / 无工具回合治理（路径 C）；
        # 工具循环（路径 D）在 post 评估，全拒（路径 E）在 all_blocked。
        _decision = await config.should_continue(
            state, response, event=LoopEvent(site="preflight", iteration=state.perf.llm_calls),
        )
        if not _decision.should_continue:
            if state.terminal_error is not None:
                yield state.terminal_error
            elif state.done_event is not None:
                yield state.done_event
            return

        # ── Step 3: execute tool calls ──

        # 本回合观察起点：清空上回合结果。否则 execute_tools 异常退出时
        # 残留旧结果会让「无配对结果→按失败计」的推导误判
        # （streak 虚增 + error-fatigue 漏计）。
        state.last_tool_results = []
        state.blocked_tool_results = []
        state.frozen_counted_this_turn = set()

        if not response.tool_calls:
            # 无工具调用 → preflight 已裁决 done/restart。跳过底部
            # bookkeeping：下轮 prepare 从 tree 重建，本回合落树的
            # assistant 消息自动进入上下文。
            continue

        # 3a. 逐调用过 before_tool_call（被阻止则产出即时错误结果，不执行）
        parsed_calls = _parse_tool_calls(response.tool_calls)
        for event in _tool_call_start_events(parsed_calls):
            yield event
        # 记录本批次所有 parsed call ids，供 abort 路径闭合
        state._pending_tool_call_ids = [c.call_id for c in parsed_calls if c.call_id]

        allowed_calls: list[Any] = []
        for call in parsed_calls:
            if config.before_tool_call is not None:
                before_ctx = BeforeToolCallContext(
                    tool_call=call,
                    args=call.arguments,
                    state=state,
                )
                before_result = await config.before_tool_call(before_ctx)
                if before_result is not None and before_result.block:
                    # 被阻止的工具返回即时错误结果。
                    # 同步收集到 state.blocked_tool_results：若本回合还有
                    # 其他调用被执行，execute_tools 会把 assistant 消息
                    # （含 tool_calls）落树，被拒结果也必须落树才能维持
                    # tool_calls ↔ tool 消息配对（否则下轮 API 400）。
                    _blocked_evt = _create_blocked_tool_result(
                        call, before_result.reason or "工具执行被阻止",
                    )
                    yield _blocked_evt
                    state.blocked_tool_results.append(_blocked_evt)
                    continue  # 跳过本调用，处理下一个
            allowed_calls.append(call)

        if not allowed_calls:
            # 所有工具被阻止 → should_continue 的 all_blocked 恢复路径处理
            _decision = await config.should_continue(
                state,
                response,
                event=LoopEvent(site="all_blocked", iteration=state.perf.llm_calls),
            )
            if not _decision.should_continue:
                if state.terminal_error is not None:
                    yield state.terminal_error
                return
            # 全拒也落树（assistant + 被拒结果）：模型下轮才能看到自己发过
            # 什么、为何被拒，否则会重发同一批调用直至 recovery_chain 耗尽。
            # 此路径不经 execute_tools 的落树步骤。
            if config.persist_denied_turn is not None:
                await config.persist_denied_turn(state, response, state.blocked_tool_results)
                state.blocked_tool_results = []
                # 落树即上下文源变化：下轮 prepare 从 tree 重建（含本回合
                # assistant + 被拒结果）——无需失效标志，树是唯一真相源。
            continue

        # 3b. HITL check (B/S 架构独有 — AskUserQuestion 需要跨 yield 边界)
        if config.check_hitl is not None:
            hitl_result = config.check_hitl(allowed_calls)
            if hitl_result is not None and hitl_result.needs_clarification:
                yield hitl_result.clarification_request
                _clarify_answer: str | None = None
                if config.wait_for_clarification is not None:
                    # 只等待并返回作答；不落树。落树统一由下方
                    # persist_clarified_turn 负责（避免 answer 重复注入）。
                    _clarify_answer = await config.wait_for_clarification(hitl_result)
                else:
                    # 卡片已弹出（clarification_request 已 yield）但没有任何等待：
                    # 本轮直接 continue，模型下一轮照常执行 → 「提问不阻断」。
                    logger.warning(
                        "hitl.wait.skipped: config.wait_for_clarification is None "
                        "— AskUserQuestion 已发出但未暂停执行",
                    )
                # 时间线闭合：ask 的 ToolCallStart 已在 3a 发出，但本分支
                # 不经 execute_tools，必须补发 ToolCallEnd（结果=用户答案），
                # 否则时间线停在 running。
                _clarify_req = hitl_result.clarification_request
                _ask_call_id = getattr(_clarify_req, "tool_call_id", "") or ""
                # 只有拿到用户作答才继续。None = 非用户作答（abort / stale /
                # 会话结束 / 装配层没接等待）——此时必须终止，不能带着空答案
                # 进入下一轮（那正是「提问了却继续执行」的表现）。
                if _clarify_answer is None:
                    state.terminal_error = ErrorEvent(
                        error_code="clarification_not_answered",
                        message="已停止执行：等待用户回答期间未收到作答",
                    )
                    yield state.terminal_error
                    return
                # HITL 回合落树：assistant（含 tool_calls）+ ask 合成结果
                # + 同批次其余调用的跳过结果。本分支不经 execute_tools，
                # 不落树会让 answer 成为孤儿消息，且树中孤立 tool_calls
                # 会被 provider 拒绝（400）。
                if config.persist_clarified_turn is not None:
                    await config.persist_clarified_turn(
                        state, response, allowed_calls, _ask_call_id, _clarify_answer,
                    )
                if _ask_call_id:
                    yield _create_clarify_tool_end(_ask_call_id, _clarify_answer)
                continue

        # 3c. execute tools
        _exec_start = _time.monotonic()
        state.perf.tool_calls += len(allowed_calls)
        async for event in config.execute_tools(state, response, allowed_calls):
            yield event
        _tools_ms = (_time.monotonic() - _exec_start) * 1000
        state.timing.tool_execution_ms = _tools_ms
        state.perf.tools_ms += _tools_ms

        # 3d. after_tool_call (可覆盖 result.content/details/isError、注入轮间消息)
        if config.after_tool_call is not None:
            after_ctx = AfterToolCallContext(
                tool_calls=allowed_calls,
                state=state,
            )
            after_result = await config.after_tool_call(after_ctx)
            if after_result is not None and after_result.inject_messages:
                state.pending_messages = after_result.inject_messages

        # ── Step 4: should_continue (post-turn) ──
        # 覆盖：tool_loop 进度观察（record + evaluate）、budget、abort
        _decision = await config.should_continue(
            state, response, event=LoopEvent(site="post", iteration=state.perf.llm_calls),
        )
        if not _decision.should_continue:
            if state.terminal_error is not None:
                yield state.terminal_error
            elif state.done_event is not None:
                yield state.done_event
            return

        # ── bookkeeping ──
        yield IterationBoundary(iteration=state.perf.llm_calls, kind="llm_end")
        state.timing.total_iteration_ms = (_time.monotonic() - _iter_start) * 1000
        logger.debug(
            "loop_timing iter=%d pipeline=%.1fms llm=%.1fms tools=%.1fms total=%.1fms",
            state.timing.iteration,
            state.timing.pre_request_pipeline_ms,
            state.timing.llm_stream_ms,
            state.timing.tool_execution_ms,
            state.timing.total_iteration_ms,
        )
        # 清理本回合的 LLM 结果，下一轮 prepare 从 tree 重建上下文。
        state.llm_outcome = None
        state.llm_response = None


