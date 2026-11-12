"""AgentLoop — 纯控制流骨架 + 闭包组装器。

设计模式：
1. 双层循环 → 单层 while True + restart_turn
2. immediate/prepared 分离 → before_tool_call block → 即时错误结果
3. 终止权威单一（2026-10-30 收权）：`control.kernel.CompletionKernel` 是
   唯一裁决者——预算上限（轮次/墙钟/金额）、完成契约准则、恢复熔断。
   工具循环策略（doom-loop 阶梯 / freeze）仍归 call_handling，只产引导。
   人/abort 通道先于内核检查，不受资源预算约束。
4. shouldStopAfterTurn → should_continue 统一出口（返回 Decision）
5. prepareNextTurn 可变更新 → TurnUpdate（仅压缩重启载体，无 override）
6. handleRunFailure → terminal_error → yield
7. 三端口必填注入（Prepare/Stream/Execute）+ 可选插槽（steering/HITL）

模块：
- run_loop(): 纯控制流骨架。不 import 任何 deps 子系统。
- create_loop_config(): 唯一知道子系统存在的地方。
"""
from __future__ import annotations

import logging
import time as _time
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

from prismclaw.engine.execution.loop.types import (
    AfterToolCallContext,
    AfterToolCallResult,
    BeforeToolCallContext,
    BeforeToolCallResult,
    ExecutePort,
    HITLCheckResult,
    LoopConfig,
    PreparePort,
    RunLoopState,
    StreamPort,
    TurnUpdate,
)
from prismclaw.engine.execution.dispatch.pipeline import (
    LLMStreamError,
    LLMStreamNoResponse,
    LLMStreamRetry,
)
from prismclaw.schema.content.streaming import ErrorEvent, IterationBoundary
from prismclaw.engine.execution.control.artifacts import ArtifactScanner
from prismclaw.engine.execution.control.completion import (
    CompletionReviewer,
    build_completion_contract,
)
from prismclaw.engine.execution.control.kernel import (
    Budget,
    BudgetGuard,
    CompletionKernel,
    CompletionState,
    Decision,
    LoopEvent,
    TerminalReason,
    evaluate_loop_control,
    reasoning_effort_for,
    resolve_effort_budget,
)
from prismclaw.engine.execution.projection.project import RunProjector
from prismclaw.engine.execution.loop._loop_helpers import (
    _parse_tool_calls,
    _tool_call_start_events,
    _create_blocked_tool_result,
    _persist_blocked_tool_results,
    _close_orphaned_tool_calls,
    _build_clarify_request,
    _create_clarify_tool_end,
    _persist_clarified_turn,
    _build_all_blocked_guidance,
    compute_tool_signature,
    compute_result_fingerprint,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# run_loop — 纯控制流骨架
# ---------------------------------------------------------------------------

async def run_loop(
    state: RunLoopState,
    config: LoopConfig,
) -> AsyncIterator[Any]:
    """纯控制流骨架。

    不 import 任何 deps 子系统。
    不直接调 safety_gate / hook_dispatcher。
    所有行为通过 config callback 注入。
    所有状态通过 state 参数传入/修改。
    所有输出通过 yield 推送。
    """
    # 单层 while True + restart_turn。
    # 回合级计时起点：仅首次进入时打点（TurnPerf 跨迭代累加，决策不读）。
    state.perf.started_at = state.perf.started_at or _time.monotonic()
    while True:
        # ── 迭代开始计时 ──
        _iter_start = _time.monotonic()
        state.timing.iteration = state.perf.llm_calls + 1
        state.timing.pre_request_pipeline_ms = 0.0
        state.timing.llm_stream_ms = 0.0
        state.timing.tool_execution_ms = 0.0
        # ── Step 1: inject pending messages into LLM context ──
        # pending_messages 是 LLM 注入消息（预算警告、工具循环指导等），
        # 不是 WebSocket 事件，不应该 yield 出去。
        # 它们会通过 prepare_next_turn → pipeline.run() →
        # build_runtime_query_state(appended_messages_override=...) 注入到 LLM 上下文中。

        # ── Step 1.5: steering 消费（iteration 边界注入用户中途纠偏） ──
        # 闭包已完成 drain + 落树（role="user"，原样内容，无前缀）。落树的
        # steering 消息随树进入 LLM 上下文——prepare 端口每迭代从 tree
        # 重建（投影语义，树是唯一真相源）；appended/pending 通道不落树，
        # 压缩重启即丢。stalled 连击归零（用户方向调整是新进展信号，
        # 用户意图优先于机械评审）。
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
        if config.prepare_next_turn is not None:
            _prep_start = _time.monotonic()
            turn_update = await config.prepare_next_turn(state)
            _prep_ms = (_time.monotonic() - _prep_start) * 1000
            state.timing.pre_request_pipeline_ms = _prep_ms
            state.perf.prep_ms += _prep_ms
            if turn_update is not None:
                # 统一应用 turn_update.pending_messages（压缩重启重放与
                # 模式 5 共用同一入口；拷贝避免与生产方列表别名）。
                if turn_update.pending_messages is not None:
                    state.pending_messages = list(turn_update.pending_messages)
                if turn_update.restart:
                    # 压缩重启：CompactionEntry 已落树，下一轮 prepare
                    # 从 tree 重建即含压缩摘要（CompactionEntry +
                    # first_kept 边界）——无需失效标志，树是唯一真相源。
                    # pending_messages（若生产方提供）已在上文重放：
                    # restart 检测发生在 LLM 调用之前，本轮合并进
                    # query_loop_state 的一次性注入尚未送达模型；appended
                    # 通道不落树，树重建无法恢复，不重放即永久丢失。
                    continue
                # prepare_next_turn returned restart=False — check terminal_error
                if state.terminal_error is not None:
                    yield state.terminal_error
                    return
                # 模式 5 — 可变更新（TurnUpdate 仅剩 restart / pending，
                # 无 override 字段可应用；防御性回归循环顶部）
                continue  # 重新进入 Step 1 注入更新的消息

        # ── Iteration boundary: LLM start ──
        yield IterationBoundary(iteration=state.perf.llm_calls, kind="llm_start")

        # 2b. stream LLM
        if config.stream_llm is not None:
            _llm_start = _time.monotonic()
            state.perf.llm_calls += 1
            async for event in config.stream_llm(state):
                yield event
            _llm_ms = (_time.monotonic() - _llm_start) * 1000
            state.timing.llm_stream_ms = _llm_ms
            state.perf.llm_ms += _llm_ms

        # 2c. check LLM result
        llm_outcome = state.llm_outcome
        if llm_outcome is not None:
            if isinstance(llm_outcome, LLMStreamError):
                # 模式 6 — handleRunFailure
                yield llm_outcome.error_event
                return
            if isinstance(llm_outcome, LLMStreamRetry):
                # LLM 重试不写循环状态；max_idle_retries 是本地硬上限。
                continue
            if isinstance(llm_outcome, LLMStreamNoResponse):
                # 重试额度内无可响应内容，继续下一轮迭代。
                continue

        response = state.llm_response
        if response is None:
            continue

        # ── finish_reason 检查：只标记当前 iteration 是否触发 max_tokens ──
        # 在每次 LLM 返回后检查，不跨 iteration 保留
        # 重置点：while True 循环顶部 state 准备后
        if getattr(response, "finish_reason", None) == "length":
            state.max_tokens_hit_in_step = True
        else:
            # 正常完成 → 重置，避免上一个 iteration 的 max_tokens 污染当前 iteration 的终止判定
            # （例：Iter1 截断 + Iter2 续行成功 → 最终不应标记为 max_tokens）
            state.max_tokens_hit_in_step = False

        # ── Step 2d: should_continue (preflight) ──
        # 在 LLM 响应后、工具执行前调用一次
        # 覆盖：budget check, abort check, max_iterations, 无工具回合治理（路径 C）
        # 路径 D（工具循环，需工具结果指纹）只在 post 阶段运行；
        # 路径 E（全拒判定）只在 all_blocked 阶段运行。
        if config.should_continue is not None:
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

        # 本回合观察起点：清空上回合结果。路径 D 的"无配对结果→按失败
        # 计"推导依赖这一点——execute_tools 异常退出时残留旧结果会把
        # 执行异常伪装成"同调用同结果"（streak 虚增 + error-fatigue 漏计）。
        state.last_tool_results = []
        state.blocked_tool_results = []
        state.frozen_counted_this_turn = set()

        if not response.tool_calls:
            # 无工具调用 → 路径 C（preflight）已处理 done/restart 决策，继续下一轮。
            # 此处 continue 跳过底部 bookkeeping——prepare 每迭代从 tree
            # 重建，本回合落树的 assistant 消息自动进入下轮上下文
            # （HIGH-3 同族手工失效标志已删除，树是唯一真相源）。
            # steps 真值为 perf.llm_calls（在 stream_llm 调用前递增），
            # 含重试/空响应路径，此处无需额外计数。
            continue

        # 3a. before_tool_call per call (immediate/prepared 分离)
        #   if blocked → 返回即时错误结果 (不执行)
        #   else → 正常执行
        parsed_calls = _parse_tool_calls(response.tool_calls)
        for event in _tool_call_start_events(parsed_calls):
            yield event
        # 记录本批次所有 parsed call ids，供 abort 路径闭合
        # ParsedToolCall.call_id property = self.call.get("id", "")
        # 同时兼容 raw dict（理论上不会走到这里，但防御性处理）
        _raw_ids: list[str] = []
        for c in parsed_calls:
            _cid = (
                getattr(c, "call_id", None)
                or (c.get("id") if isinstance(c, dict) else None)
                or None
            )
            if _cid:
                _raw_ids.append(_cid)
        state._pending_tool_call_ids = _raw_ids

        allowed_calls: list[Any] = []
        for call in parsed_calls:
            if config.before_tool_call is not None:
                before_ctx = BeforeToolCallContext(
                    assistant_message=response,
                    tool_call=call,
                    args=call.arguments,
                    context=None,
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
                    continue  # skip this call, process next
            allowed_calls.append(call)

        if not allowed_calls:
            # 所有工具被阻止 → should_continue 的 all_blocked 恢复路径处理
            if config.should_continue is not None:
                _decision = await config.should_continue(
                    state,
                    response,
                    event=LoopEvent(site="all_blocked", iteration=state.perf.llm_calls),
                )
                if not _decision.should_continue:
                    if state.terminal_error is not None:
                        yield state.terminal_error
                    return
            # 全拒也落树（assistant + 被拒结果）——模型下轮才能看到自己
            # 发过什么、为何被拒，否则会重发同一批调用直至 recovery_chain
            # 耗尽（HIGH-3 同族）。此路径不经 execute_tools 的 Step 0。
            if config.persist_denied_turn is not None:
                await config.persist_denied_turn(state, response, state.blocked_tool_results)
                state.blocked_tool_results = []
                # 落树即上下文源变化：下轮 prepare 从 tree 重建（含本回合
                # assistant + 被拒结果）——无需失效标志，树是唯一真相源。
            continue

        # 3b. HITL check (B/S 架构独有 — AskUserQuestion 需要跨 yield 边界)
        if config.check_hitl is not None:
            hitl_result = config.check_hitl(state, allowed_calls)
            if hitl_result is not None and hitl_result.needs_clarification:
                yield hitl_result.clarification_request
                _clarify_answer: str | None = None
                if config.wait_for_clarification is not None:
                    # answer 由 wait_for_clarification 闭包落树为 user
                    # 消息（B/S 实现）。骨架不注入 pending——prepare 每迭代
                    # 从 tree 重建即含 answer，再注入会重复。
                    _clarify_answer = await config.wait_for_clarification(hitl_result)
                else:
                    # 卡片已弹出（clarification_request 已 yield）但没有任何等待：
                    # 本轮直接 continue，模型下一轮照常执行 → 「提问不阻断」。
                    logger.warning(
                        "hitl.wait.skipped: config.wait_for_clarification is None "
                        "— AskUserQuestion 已发出但未暂停执行",
                    )
                # HITL 时间线闭合：ask_user_question 的 ToolCallStart 已在 3a
                # 统一发出，但本分支不经 execute_tools——必须补发合成
                # ToolCallEnd（结果=用户答案），否则时间线「向用户提问」行
                # 永远停在 running，且用户回答在执行过程 UI 中不可见。
                # clarification_request 的形态由 B/S 实现决定（pydantic 模型/
                # dict 桩），call_id 用属性/dict 双路提取，取不到则跳过闭合。
                _clarify_req = hitl_result.clarification_request
                _ask_call_id = (
                    getattr(_clarify_req, "tool_call_id", None)
                    or (_clarify_req.get("tool_call_id") if isinstance(_clarify_req, dict) else None)
                    or ""
                )
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
                # HITL 回合落树（persist_denied_turn 同族）：assistant（含
                # tool_calls）+ ask 合成结果（用户答案）+ 同批次其余调用的
                # 跳过结果。本分支不经 execute_tools（Step 0 从未执行），
                # 不落树则 answer 成孤儿 user 消息（模型看不到自己问过
                # 什么）、同批次调用凭空消失，树中孤立 tool_calls 会让
                # prepare_next_turn 重建上下文时被 provider 拒绝（400）。
                if config.persist_clarified_turn is not None:
                    await config.persist_clarified_turn(
                        state, response, allowed_calls, _ask_call_id, _clarify_answer,
                    )
                if _ask_call_id:
                    yield _create_clarify_tool_end(_ask_call_id, _clarify_answer)
                continue

        # 3c. execute tools
        if config.execute_tools is not None:
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
                assistant_message=response,
                tool_calls=allowed_calls,
                results=state.last_tool_results,
                context=None,
                state=state,
            )
            after_result = await config.after_tool_call(after_ctx)
            if after_result is not None and after_result.inject_messages:
                state.pending_messages = after_result.inject_messages

        # ── Step 4: should_continue (post-turn) ──
        # 覆盖：tool_loop 进度观察（record + evaluate）、budget、abort
        if config.should_continue is not None:
            _decision = await config.should_continue(
                state, response, event=LoopEvent(site="post", iteration=state.perf.llm_calls),
            )
            if not _decision.should_continue:
                if state.terminal_error is not None:
                    yield state.terminal_error
                elif state.done_event is not None:
                    yield state.done_event
                return

        # pending_messages 在循环顶部 Step 1 处理

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
        state.llm_outcome = None
        state.llm_response = None
        # HIGH-3：prepare 端口每迭代从 tree 重建 source_messages，本回合
        # 落树的 assistant/tool 消息自动进入下一轮 LLM 上下文——不存在
        # 会漂移的第二份上下文副本（doom-loop 事故根因已结构性消灭）。


# ---------------------------------------------------------------------------
# create_loop_config — 闭包组装器
# ---------------------------------------------------------------------------

def create_loop_config(
    deps: Any,
    request: Any,
    recovery: Any,
    state_machine: Any,
    tool_loop_state: Any,
    run_state: Any,
    initial_task_spec: Any,
    retry_counts: Any,
    response_policy: Any,
    *,
    pipeline: Any = None,
    tool_ctx: Any = None,
    tool_workspace_path: Any = None,
    effective_model: str | None = None,
    abort_event: Any = None,
    run_deadline_s: float = 0.0,
    run_started: float = 0.0,
    _mode: str = "standard",
    task_runtime: Any = None,
    snapshot: Any = None,
    tree: Any = None,
    _traj_scope: Any = None,
    attachment_guidance: str | None = None,
) -> LoopConfig:
    """组装 LoopConfig。

    这是**唯一知道子系统存在的地方**。run_loop() 不知道
    SafetyGate、HookDispatcher 的存在。

    将完成内核（RecoveryPolicy 注入）+ SafetyGate + HookDispatcher +
    no-tool 治理映射为 callback。
    """

    # ══════════════════════════════════════════════════════════
    # Completion control: 预算 / 完成准则 / 恢复熔断
    # ══════════════════════════════════════════════════════════
    # 控制状态只有一份：契约冻结一次；台账引用在每次决策前绑定到
    # ``state.evidence_ledger``（与 ``ctx.evidence_ledger`` 同一列表），
    # 由工具执行后的唯一写入点追加。
    completion_config = deps.config.control
    # 完成契约在首个消费点惰性构建。
    _completion_state = CompletionState(contract=None, contract_resolved=False)
    _budget_warned = False  # 预算临限提醒只注入一次
    _effort_level = getattr(request, "effort", "standard") or "standard"
    _effort_budget = resolve_effort_budget(
        effort=_effort_level,
        long_running=bool(getattr(request, "long_running", False)),
        tiers=deps.config.effort,
    )
    # 写入 loop 预算的墙钟锚点（soft/hard deadline），供 evaluate_loop_control 消费。
    run_state.loop_state.soft_at = float(run_started) + _effort_budget.soft_s
    run_state.loop_state.hard_at = float(run_started) + _effort_budget.hard_s
    _budget_guard = BudgetGuard(
        Budget(
            max_iterations=_effort_budget.steps,
            run_deadline_s=float(run_deadline_s or 0.0),
            run_started=float(run_started or 0.0),
        )
    )
    completion_kernel = CompletionKernel(
        state=_completion_state,
        reviewer=CompletionReviewer(ArtifactScanner(tool_workspace_path)),
        budget=_budget_guard,
        recovery=recovery,
    )

    _terminal_messages = {
        "budget_iterations": "任务已用尽迭代预算（硬边界）。",
        "budget_seconds": "任务已超出运行时上限（硬边界）。",
        "blocked_no_progress": "完成要求未满足且修复额度已用尽，任务终止。",
        "blocked_tools_denied": "工具调用被策略整体拒绝且重规划机会已用尽，任务终止。",
        "error": "恢复链已耗尽，完成控制无法继续。",
        "aborted": "run aborted",
    }

    # ── 三周期投影（Phase 1）：Run 级记忆组合缓存，Run 生命周期构造一次 ──
    _run_projector: Any = RunProjector(
        label=getattr(request, "task_id", "") or "run"
    )

    async def _terminate(
        state: RunLoopState,
        code: str,
        *,
        message: str | None = None,
        terminated_by: TerminalReason | None = None,
    ) -> None:
        """终态副作用的唯一实现：清计数、标记阻塞、持久化快照、产出错误事件。

        ``code`` 是 ErrorEvent 的错误码；``terminated_by`` 是遥测用的终态标签，
        缺省与 ``code`` 相同。
        """
        from prismclaw.engine.execution.loop.arbiter import mark_task_blocked, persist_snapshot_fn

        if tool_loop_state is not None:
            # run 终止时必须把所有 run 级状态清干净——下次 run 从 checkpoint
            # 恢复时 tool_loop_state 可能残留（resume 场景），跨 run 污染不可接受。
            tool_loop_state.compaction_failures = 0
            tool_loop_state.recent_tool_signatures.clear()
            tool_loop_state.consecutive_external_errors = 0
            tool_loop_state.consecutive_external_same_signature = 0
            tool_loop_state.last_external_signature = ""
            tool_loop_state.frozen_tools.clear()
            tool_loop_state.frozen_call_signatures.clear()
            tool_loop_state.tool_attempt_events.clear()
            tool_loop_state.frozen_call_defied = False
            tool_loop_state.convergence_advisory_sent = False
            state_machine.update_tool_loop_state(tool_loop_state)
        if state.ctx is not None:
            # 闭集收敛：terminated_by 显式给出时优先；否则 code 必须本身就是
            # 合法的 TerminalReason（内核/治理的终态码），否则在此刻抛错，
            # 杜绝新的魔法字符串渗入遥测通道。
            state.ctx.terminated_by = terminated_by or TerminalReason(code)
            if code != "aborted":
                await mark_task_blocked(state.ctx, code)
            await persist_snapshot_fn(state.ctx)
        state.terminal_error = ErrorEvent(
            error_code=code,
            message=message or _terminal_messages.get(code, "任务终止"),
        )

    async def should_continue(
        state: RunLoopState,
        response: Any | None = None,
        *,
        event: LoopEvent,
    ) -> Decision:
        """统一续跑/终态决策——完成控制的唯一出口。

        ``event.site`` 声明调用位置（字段而非分支选择器）：

        - ``preflight``（LLM 响应后、工具执行前）：预算 / abort / 无工具
          回合治理（完成申报）。工具循环策略不在此评估——streak 的语义
          是"同调用同结果"，结果要执行后才存在。
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
        _completion_state.ledger = state.ledger.evidence_ledger
        # loop_self 定位插桩：should_continue 是骨架未打点段的最大成员，
        # 超阈值（0.5s）才记日志，正常路径零噪音。
        _sc_t0 = _time.monotonic()
        _gov_ms = -1.0

        # ── 完成契约构建 ──
        if not _completion_state.contract_resolved:
            nonlocal initial_task_spec
            _completion_state.contract_resolved = True
            _completion_state.contract = (
                build_completion_contract(
                    initial_task_spec,
                    max_repairs=completion_config.max_repairs,
                )
                if initial_task_spec is not None
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
                declares_blocked=bool(getattr(tool_ctx, "goal_block_intent", None)),
            )
            _gov_t0 = _time.monotonic()
            gov = await evaluate_no_tool_governance(
                state.ctx,
                response=response,
                event=_stop_event,
                kernel=completion_kernel,
                retry_counts=retry_counts,
                response_policy=response_policy,
                state_machine=state_machine,
                prepared_turn=state.prepared_turn,
                total_input_tokens=state.ledger.total_input_tokens,
                total_output_tokens=state.ledger.total_output_tokens,
                total_cache_creation_tokens=state.ledger.total_cache_creation_tokens,
                total_cache_read_tokens=state.ledger.total_cache_read_tokens,
                run_state=run_state,
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
                await _terminate(
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
        decision = completion_kernel.decide(event)
        if decision.verdict == "TERMINATE":
            await _terminate(state, decision.code)
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
        _budget_dim = _budget_guard.warn(
            iteration=state.perf.llm_calls,
        )
        if _budget_dim is not None:
            nonlocal _budget_warned
            if not _budget_warned:
                _budget_warned = True
                state.pending_messages.append(
                    {
                        "role": "user",
                        "content": (
                            "[系统提示：本轮次预算已用去九成。这是收敛信号："
                            "不要再开新的探索分支，尽快补齐交付物并给出最终答复。]"
                        ),
                    },
                )

        # ── 工具循环策略（post 独有 — 先 record 再 evaluate）──
        if event.site == "post" and response is not None and response.tool_calls:
            from prismclaw.engine.execution.dispatch.call_handling import (
                evaluate_tool_loop_policies,
                record_tool_call_observation,
                parse_tool_calls,
            )

            # 观察对象：本批次首个调用 + 其执行结果。
            # 参数解析与 before_tool_call 共用同一来源（parse_tool_calls →
            # _function_args）：畸形 JSON 两处都得到 {"_raw": …}，签名必然一致
            # ——冻结才能命中（两处各写一套 json.loads 时，畸形参数算出不同
            # 签名，冻结永不生效）。
            _tool_name: str | None = None
            _tool_args: dict = {}
            _first_id = ""
            _parsed = parse_tool_calls(response.tool_calls[:1])
            if _parsed:
                _tool_name = _parsed[0].name or None
                _tool_args = _parsed[0].arguments or {}
                _first_id = _parsed[0].call_id
            _tool_sig = compute_tool_signature(_tool_name or "", _tool_args)

            # 结果按 tool_call_id 严格配对（结果列表顺序不保证与调用一致）。
            # id 非空但匹配不到 → 本调用无结果（被拦截/异常），不是"取别的
            # 调用的结果来凑数"——那会把签名与结果错配。
            _first_result = None
            if _first_id:
                for _r in state.last_tool_results or []:
                    if getattr(_r, "tool_call_id", "") == _first_id:
                        _first_result = _r
                        break
            elif state.last_tool_results:
                _first_result = state.last_tool_results[0]

            record_tool_call_observation(
                tool_loop_state,
                tool_name=_tool_name,
                tool_signature=_tool_sig,
                result_fingerprint=compute_result_fingerprint(_first_result),
                # 无配对结果（execute_tools 异常/无结果）按失败计——正常执行
                # 必有配对结果，doom-loop 签名窗口才不会漏计。
                is_error=bool(
                    _first_result is None
                    or getattr(_first_result, "is_error", False)
                ),
                # 失败归因：external（MCP 传输/业务错误、sub-agent 整体失败）
                # 不进签名窗口，只累计 external 计数供判定函数区分
                # "换源对抗"与"死磕同源"。
                error_origin=(
                    getattr(_first_result, "error_origin", None)
                    if _first_result is not None
                    else None
                ),
            )
            loop_outcome = evaluate_tool_loop_policies(
                state=tool_loop_state,
                config=getattr(deps.config, "run_loop", None) if hasattr(deps, "config") else None,
            )
            state_machine.update_tool_loop_state(tool_loop_state)

            if loop_outcome.action == "warn" and loop_outcome.guidance is not None:
                state.pending_messages.append(
                    {"role": "user", "content": loop_outcome.guidance},
                )

            if loop_outcome.action == "freeze":
                # 冻结该调用签名（初始 0），下一回合 before_tool_call 起拦截。
                # 任务不终止——模型仍有自救机会（换工具 / 交付已有结果）。
                if loop_outcome.frozen_signature:
                    tool_loop_state.frozen_call_signatures.setdefault(
                        loop_outcome.frozen_signature, 0,
                    )
                state.pending_messages.append(
                    {"role": "user", "content": loop_outcome.guidance},
                )
                state_machine.update_tool_loop_state(tool_loop_state)

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

    # ══════════════════════════════════════════════════════════
    # prepare_next_turn: 可变更新
    # ══════════════════════════════════════════════════════════

    async def prepare_next_turn(state: RunLoopState) -> TurnUpdate | None:
        """准备下一轮上下文。

        每迭代从 tree 重建 query_loop_state（投影语义：树是唯一真相源，
        无跨迭代缓存、无手工失效标志）——工具结果 / steering / 压缩摘要
        落树即自动进入下一轮上下文。

        pending_messages（预算警告、工具循环指导等）与
        pending_meta_messages（技能上下文修饰）在重建时注入，消费后
        清空 pending。返回 TurnUpdate 仅用于压缩重启（restart + 重放）。
        """
        if pipeline is None:
            return None

        from prismclaw.engine.execution.query.context_helpers import _build_source_query_loop_state

        # 每迭代纯投影：从 tree + 注入消息构建，无 override 缓存路径。
        query_loop_state = _build_source_query_loop_state(
            tree=state.tree,
            request=request,
            deps=deps,
            tool_ctx=tool_ctx,
            effective_model=effective_model or "",
            attachment_guidance=attachment_guidance,
            tool_loop_state=tool_loop_state,
            pending_meta_messages=state.pending_meta_messages,
            appended_messages_override=state.pending_messages or None,
        )
        # 清空 pending_messages — 已合并到本轮 query_loop_state 中
        state.pending_messages.clear()

        # 三周期投影（Phase 1）：注入 Run 级记忆组合缓存（幂等 attach）。
        _run_projector.attach(getattr(deps, "context_assembler", None))

        # ── loop 控制单一评估点（在 pipeline.run 之前）──
        _ctrl = evaluate_loop_control(
            run_state=run_state,
            effort_budget=_effort_budget,
            now=_time.monotonic(),
        )
        if _ctrl.should_stop:
            state.ctx.terminated_by = TerminalReason.BUDGET_ITERATIONS
            from prismclaw.engine.execution.loop.arbiter import mark_task_blocked
            await mark_task_blocked(state.ctx, "loop_budget_exhausted")
            return None

        # ── 收尾冻结：wrapping 冻结搜索类，last_turn 冻结非写入类 ──
        # frozen_tools 跨轮持续生效，仅在 _terminate 时清空；此处只增不删。
        if _ctrl.freeze_tools and tool_loop_state is not None:
            _run_loop_cfg = getattr(deps.config, "run_loop", None)
            if _ctrl.is_last_turn:
                # last_turn：冻结所有非写入类工具，保留 write 类让产物写完。
                from prismclaw.engine.toolkit.registry.tool_classification import (
                    resolve_tool_class,
                )
                for _tname in deps.tools.names():
                    if resolve_tool_class(_tname, deps.tools) != "write":
                        tool_loop_state.frozen_tools.add(_tname)
            else:
                # wrapping：冻结搜索意图工具，避免继续开探索分支。
                _search_tools = set(getattr(_run_loop_cfg, "search_intent_tools", []) or [])
                tool_loop_state.frozen_tools.update(_search_tools)
            state_machine.update_tool_loop_state(tool_loop_state)

        result = await pipeline.run(
            request=request,
            deps=deps,
            tree=state.tree,
            turn_state=state.snapshot.turn_state,
            tool_workspace_path=tool_workspace_path,
            effective_model=effective_model,
            tool_ctx=tool_ctx,
            skill_invocation=state.active_skill_invocation,
            attachment_guidance=attachment_guidance,
            query_loop_state=query_loop_state,
            compression_runtime=state.snapshot.compression_runtime,
            abort_event=abort_event,
            compression_orchestrator=deps.session_compressor,
        )

        # 更新 state — PreparedTurn 供 stream 端口与 should_continue 使用
        state.prepared_turn = result
        logger.debug("prepare_next_turn: wrote state.prepared_turn (preflight=%s)", 
                     "present" if result.preflight is not None else "None")

        compression_preparation = result.compression_preparation

        if compression_preparation is not None and compression_preparation.action == "restart_turn":
            code = recovery.consult("compression_restart")
            if code is not None:
                # 重放本轮一次性注入（hook 注入 / 预算警告 / 工具
                # guidance）。restart 检测发生在 LLM 调用之前——这些注入
                # 尚未送达模型，而骨架 restart 分支会丢弃本轮上下文并
                # 从 tree 重建；appended 通道不落树，不重放即永久丢失。
                # result.query_loop_state.query_view.source_messages
                # .appended_messages 是合并后的全量注入集（历史 pending +
                # 本轮 pending），rebuild_query_loop_state / replace_messages
                # 均不触碰该字段，提取保真。
                _replay = list(
                    result.query_loop_state.query_view.source_messages.appended_messages
                )
                # Step 3：压缩产物已落树——显式失效 Run 级树状态缓存，
                # 下一轮 assemble 重读树，新 CompactionEntry 进检索锚。
                _run_projector.invalidate(getattr(request, "session_id", None))
                return TurnUpdate(
                    restart=True,
                    pending_messages=_replay,
                )
            # 恢复链已耗尽 → 硬终止
            await _terminate(state, "error", message="恢复链已耗尽（压缩重启被熔断）")
            return TurnUpdate(restart=False)

        return None

    # ══════════════════════════════════════════════════════════
    # before_tool_call: 工具执行前拦截
    # ══════════════════════════════════════════════════════════

    async def before_tool_call(
        before_ctx: BeforeToolCallContext,
    ) -> BeforeToolCallResult | None:
        """工具执行前检查。

        1. doom-loop 冻结签名命中 → block（模型收到冻结告知）；
           反复重试被冻结的调用达升级阈值 → 置 frozen_call_defied
           嫌疑标志（只作观测，不产生终态）。
        2. evaluate_safety_gate deny → BeforeToolCallResult(block=True)
        都不 deny → None (执行)
        """
        # tool_calls_spent 按尝试计，含被拒；并行 N 个调用计 N（每调用进一次本回调）。
        run_state.loop_state.tool_calls_spent += 1
        from prismclaw.engine.execution.dispatch.tools import evaluate_safety_gate
        from prismclaw.engine.execution.dispatch.call_handling import check_frozen_call

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
            # 同一签名每回合只计一次数（模型并行发 N 个相同调用
            # 不耗 N 次 escalate 配额）
            counted_this_turn=_bstate.frozen_counted_this_turn if _bstate is not None else set(),
        )
        if frozen_outcome.action == "freeze":
            state_machine.update_tool_loop_state(tool_loop_state)
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
            return BeforeToolCallResult(block=True, reason=reason)
        return None

    # ══════════════════════════════════════════════════════════
    # after_tool_call: 工具执行后处理
    # ══════════════════════════════════════════════════════════

    async def after_tool_call(
        after_ctx: AfterToolCallContext,
    ) -> AfterToolCallResult | None:
        """工具执行后处理（只做观测与一次收尾提示，不做裁决）。

        POST_TOOL hooks 已下沉到 execute_tool_calls 逐调用派发。此处保留：

        * 收敛软提示：完成契约机械核验无缺口时注入一次"请收尾"提示。

        完成审查只在模型试图停止时发生（no-tool 链 → CompletionKernel），
        工具回合不做完成审查。
        """
        _state = after_ctx.state
        _ctx = _state.ctx if _state is not None else None

        if _state is not None and after_ctx.tool_calls:
            # 完成契约无缺口 → 注入一次收尾提示。不终止：终止权只归内核。
            if _ctx is not None and _completion_state.contract is not None:
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

    # ══════════════════════════════════════════════════════════
    # stream_llm: LLM 流式调用
    # ══════════════════════════════════════════════════════════

    async def stream_llm(state: RunLoopState) -> AsyncIterator[Any]:
        """LLM 流式调用。

        保留已有模块 call_llm_stream_with_outcome()，不重写。
        """
        # steps_spent 与 perf.llm_calls 同写入点（stream_llm 调用前）。
        run_state.loop_state.steps_spent += 1
        from prismclaw.engine.execution.dispatch.pipeline import (
            PipelineParams,
            _OutcomeHolder,
            call_llm_stream_with_outcome,
            LLMStreamResult,
        )
        from prismclaw.engine.foundation.runtime_util import remaining_run_seconds as _remaining_run_seconds

        _remaining_run = _remaining_run_seconds(run_deadline_s, run_started)

        # timeout check
        if _remaining_run is not None and _remaining_run <= 0:
            state.ctx.terminated_by = TerminalReason.TIMEOUT
            from prismclaw.engine.execution.loop.arbiter import mark_task_blocked
            await mark_task_blocked(state.ctx, "timeout")
            from prismclaw.schema.content.streaming import ErrorEvent
            from prismclaw.engine.execution.dispatch.pipeline import LLMStreamError
            _timeout_error = ErrorEvent(
                error_code="timeout",
                message="run exceeded run_seconds",
            )
            state.llm_outcome = LLMStreamError(
                error_event=_timeout_error,
            )
            return

        import time as _time
        state.frame.llm_start_monotonic = _time.monotonic()

        # 防御性检查：prepared_turn 必须由 prepare_next_turn 写入
        if state.prepared_turn is None:
            from prismclaw.engine.execution.dispatch.pipeline import LLMStreamError as _LLMStreamError
            from prismclaw.schema.content.streaming import ErrorEvent as _ErrorEvent
            logger.error("stream_llm: state.prepared_turn is None — prepare_next_turn did not write it")
            state.llm_outcome = _LLMStreamError(
                error_event=_ErrorEvent(error_code="internal", message="prepared_turn is None"),
            )
            return

        # ── 单请求墙钟硬上限 cap_s（effort 派生）──
        # prepare_next_turn 之后到 stream_llm 之间有上下文构建耗时，
        # 此处重新评估 is_last_turn 以保证 cap_s 准确。
        _now = _time.monotonic()
        _ctrl = evaluate_loop_control(
            run_state=run_state,
            effort_budget=_effort_budget,
            now=_now,
        )
        _remaining_hard = run_state.loop_state.hard_at - _now
        if _ctrl.is_last_turn:
            _cap_s = min(_effort_budget.cap_s, max(0.0, _remaining_hard))
        else:
            _cap_s = min(_effort_budget.cap_s, max(0.0, _remaining_hard - _effort_budget.reserve_s))

        _pipeline_params = PipelineParams(
            prepared_turn=state.prepared_turn,
            effective_model=effective_model or "",
            remaining_run=_remaining_run,
            run_deadline_s=run_deadline_s,
            run_started=run_started,
            traj_turn_counter=_traj_scope.turn_counter if _traj_scope is not None else 0,
            iteration_pre_advance=state.perf.llm_calls,
            abort_event=abort_event,
            max_idle_retries=1,
            cap_s=_cap_s,
            is_last_turn=_ctrl.is_last_turn,
            reasoning_effort=reasoning_effort_for(_effort_level),
        )
        holder = _OutcomeHolder()
        async for event in call_llm_stream_with_outcome(
            state.ctx, _pipeline_params, _outcome=holder,
        ):
            yield event
        state.llm_outcome = holder.outcome
        if isinstance(holder.outcome, LLMStreamResult):
            state.llm_response = holder.outcome.response
            _usage = getattr(holder.outcome.response, "usage", None)
            if _usage is not None:
                state.ledger.total_input_tokens += getattr(_usage, "input_tokens", 0) or 0
                state.ledger.total_output_tokens += getattr(_usage, "output_tokens", 0) or 0
                state.ledger.total_cache_creation_tokens += getattr(_usage, "cache_creation_input_tokens", 0) or 0
                state.ledger.total_cache_read_tokens += getattr(_usage, "cache_read_input_tokens", 0) or 0
                # G4：缓存命中率埋点（结构化 info）。
                _turn_input = int(getattr(_usage, "input_tokens", 0) or 0)
                _turn_cache_read = int(getattr(_usage, "cache_read_input_tokens", 0) or 0)
                if _turn_input > 0:
                    logger.info(
                        "turn_cache_hit cache_read=%d input=%d cache_hit_ratio=%.3f",
                        _turn_cache_read,
                        _turn_input,
                        _turn_cache_read / _turn_input,
                    )
                # last_usage 存储链路：记录本轮服务端真实 usage.input_tokens（输入侧
                # token，供下一轮压缩触发决策读取；状态持久化于 checkpoint）。
                try:
                    state.snapshot.compression_runtime.last_prompt_tokens = (
                        getattr(_usage, "input_tokens", None)
                    )
                except Exception:
                    pass
                # P0-②（每轮 token/cache 遥测接线）：log_turn_token_usage 在
                # 架构重构时定义但从未被调用（log.log 中 token_usage 计数为 0 的
                # 真因）；PreparedTurn 的 tools_wire_tokens / history_budget /
                # preflight 生产端均已就绪，此处补上缺失的消费端。
                _pt = state.prepared_turn
                if _pt is not None and _pt.preflight is not None:
                    from prismclaw.engine.execution.query._turn_helpers import (
                        log_turn_token_usage,
                    )

                    log_turn_token_usage(
                        logger_=logger,
                        iteration=state.perf.llm_calls,
                        telemetry=_pt.preflight.compute_turn_telemetry(),
                        usage=_usage,
                        history_budget=_pt.history_budget,
                        history_compaction_count=(
                            tree.get_history_compaction_count()
                            if tree is not None
                            else 0
                        ),
                        tools_tokens=_pt.tools_wire_tokens,
                    )

    # ══════════════════════════════════════════════════════════
    # persist_denied_turn: 全拒回合的树持久化
    # ══════════════════════════════════════════════════════════

    async def persist_denied_turn(
        state: RunLoopState,
        response: Any,
        blocked_events: list[Any],
    ) -> None:
        """全拒回合（路径 E restart）的树持久化。

        与 execute_tools Step 0 完全相同的形态（assistant + 被拒结果），
        模型下轮从 tree 重建上下文时能看到本回合发过什么、为何被拒。
        失败不中断全拒恢复路径，但必须闭合配对：
        assistant 已落树而 blocked 结果写入失败时，树中孤立 tool_calls
        会让下轮请求被 provider 拒绝（400）——与 execute_tools except
        路径的 _close_orphaned_tool_calls 防护形态一致。
        """
        from prismclaw.engine.execution.dispatch.response_handling import (
            append_assistant_response,
        )
        from prismclaw.engine.execution.loop.bootstrap import _append
        from prismclaw.engine.execution.dispatch.turn_utils import strip_thinking

        try:
            await append_assistant_response(
                deps=deps,
                tree=tree,
                append_entry=_append,
                text=strip_thinking(response.text or ""),
                tool_calls=response.tool_calls,
            )
            if blocked_events:
                await _persist_blocked_tool_results(
                    deps=deps,
                    tree=tree,
                    blocked_events=blocked_events,
                )
        except Exception:
            logger.warning("persist_denied_turn failed", exc_info=True)
            await _close_orphaned_tool_calls(
                deps=deps,
                tree=tree,
                tool_calls=response.tool_calls or [],
            )

    async def persist_clarified_turn(
        state: RunLoopState,
        response: Any,
        allowed_calls: list[Any],
        ask_call_id: str,
        answer: str | None,
    ) -> None:
        """HITL 回合的树持久化（assistant + ask 结果 + 同批次跳过结果 + answer）。

        实现在 _loop_helpers._persist_clarified_turn（引擎层通用 helper）；
        此处仅闭包注入 deps/tree。失败不中断骨架（尽力而为，与
        persist_denied_turn 同策略）；骨架在调用后统一走续跑分支
        （None 终止路径不调用本闭包——树中无 assistant 即无配对缺口）。
        """
        try:
            await _persist_clarified_turn(
                deps=deps,
                tree=tree,
                response=response,
                allowed_calls=allowed_calls,
                ask_call_id=ask_call_id,
                answer=answer,
            )
        except Exception:
            logger.warning("persist_clarified_turn failed", exc_info=True)
            await _close_orphaned_tool_calls(
                deps=deps,
                tree=tree,
                tool_calls=response.tool_calls or [],
            )

    # ══════════════════════════════════════════════════════════
    # execute_tools: 工具执行 (handoff dispatch, B/S 架构独有)
    # ══════════════════════════════════════════════════════════

    async def execute_tools(
        state: RunLoopState,
        response: Any,
        allowed_calls: list[Any],
    ) -> AsyncIterator[Any]:
        """工具执行。B/S 架构有 handoff dispatch + subagent，因此提取为独立 callback。"""
        from prismclaw.engine.execution.dispatch.execution import (
            execute_tool_calls_with_handoff,
            postprocess_tool_results,
            project_tool_result_entries,
            persist_projected_tool_result_entries,
            plan_handoff_dispatch,
            apply_handoff_dispatch_plan,
        )
        from prismclaw.engine.execution.skills.skill_dispatch import (
            _apply_context_modifiers_from_results,
            _execute_skill_execution_strategys_from_results,
        )
        from prismclaw.engine.execution.loop.arbiter import persist_snapshot_fn
        from prismclaw.engine.execution.control.artifacts import ArtifactScanner
        from prismclaw.engine.execution.control.evidence import declared_paths, record_tool_evidence
        from prismclaw.engine.execution.dispatch.call_handling import parse_tool_calls
        from prismclaw.engine.toolkit.subagent.subagent_progress_emitter import _emit_cap_exceeded_synthetic_progress
        from prismclaw.engine.toolkit.builtin.tool_helpers import _extract_file_paths_from_tool_calls
        from prismclaw.engine.execution.state.task_persistence import (
            _persist_execution_evidence,
            _record_latest_tool_effect,
            _record_result_compaction_state,
        )
        from prismclaw.engine.execution.loop.bootstrap import (
            publish_context_node,
            _persist_turn_runtime_metrics,
            _append,
        )
        from prismclaw.schema.agent import HeartbeatEvent
        import time as _time

        try:
            # ── 0. Persist assistant message (with tool_calls) ──
            # HIGH-5：工具回合的 assistant 消息此前从不落树。本回合的
            # tool_calls 与 tool 结果全部依赖本函数写入树后，下轮
            # prepare_next_turn 才能从 tree 重建出完整的 LLM 上下文
            # （tool_calls ↔ tool 消息配对是 provider 的硬约束）。
            # 复用 response_handling.append_assistant_response：
            # entry → event_logger.assistant_message（tool_calls 内联）
            # → redis 加载可恢复。
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
            # 被拒调用（before_tool_call block）的 tool 结果随之落树——
            # 只有部分调用被执行时，被拒的 tool_calls 没有配对 tool 消息
            # 会让下一轮请求被 provider 拒绝（400）。
            if state.blocked_tool_results:
                await _persist_blocked_tool_results(
                    deps=deps,
                    tree=tree,
                    blocked_events=state.blocked_tool_results,
                )
                state.blocked_tool_results = []

            # ── 1. Handoff dispatch ──
            # 使用 allowed_calls（已过滤掉被阻止的工具）而非 response.tool_calls
            _allowed_raw_calls = [c.call for c in allowed_calls]
            handoff_result = await plan_handoff_dispatch(
                deps=deps,
                request=request,
                tool_calls=_allowed_raw_calls,
            )
            await apply_handoff_dispatch_plan(
                tool_ctx=tool_ctx,
                handoff_plan=handoff_result,
                subagent_progress_sink=deps.resolved_subagent_progress_sink,
                emit_cap_exceeded_progress=_emit_cap_exceeded_synthetic_progress,
            )

            # ── 1.5 Artifact baseline ──
            # 只 stat 本次调用声明的路径；写入事实由前后快照差确定。
            _artifact_scanner = ArtifactScanner(tool_workspace_path)
            _parsed_calls = parse_tool_calls(handoff_result.tool_calls)
            _declared = tuple(
                path for _call in _parsed_calls for path in declared_paths(_call)
            )
            _artifacts_before = _artifact_scanner.stat_paths(_declared)

            # ── 2. Execute tools ──
            # elapsed_s 从 run_started（任务发起时刻）起算，而非 llm_start_monotonic
            # （当前 LLM 调用发起时刻），以反映用户感知的全程时间。
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
            # 用 heavyweight：它会额外做 skill 目录发现（新出现的
            # .work/skills 子树），是新目录唯一被发现的地方；
            # 已扫描目录由 SkillCatalogState.discovered_dirs 去重，
            # 重复调用的增量成本仅为 walk-up stat。
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
            # Publish context node injections
            for _inj in _context_injections:
                try:
                    publish_context_node(
                        tree,
                        entry_id=_inj.entry_id,
                        label=_inj.skill_name,
                        content=_inj.content,
                    )
                except Exception:
                    pass

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
            # On execution error: doom-loop 签名计数不在此处手写累加——
            # 唯一写入点是 record_tool_call_observation，无配对结果
            # （本路径的常态，骨架已在 Step 3 前清空 last_tool_results）
            # 会被它按失败计。
            logger.warning(
                "execute_tools error: %s", _exec_exc, exc_info=True,
            )
            # Step 0 已把 assistant（含 tool_calls）落树——中途异常时
            # 尚未执行的调用必须在树层闭合配对（合成 error 结果），
            # 否则下轮 prepare_next_turn 从 tree 重建上下文时
            # 孤立 tool_calls 会被 provider 拒绝（400）。
            await _close_orphaned_tool_calls(
                deps=deps,
                tree=tree,
                tool_calls=response.tool_calls or [],
            )
            yield ErrorEvent(
                error_code="tool_execution_error",
                message=f"工具执行失败：{_exec_exc}",
            )

    # ══════════════════════════════════════════════════════════
    # HITL: B/S 架构独有
    # ══════════════════════════════════════════════════════════

    def check_hitl(state: RunLoopState, calls: list[Any]) -> HITLCheckResult | None:
        """HITL 检测。AskUserQuestion 需要跨 yield 边界处理。

        契约：AskUserQuestion 若对模型可见（可被调用），则运行时依赖
        ``deps.session_queue`` 必须存在。二者不一致属配置错误，必须在此
        fail fast，不允许静默降级为「当普通工具执行」——那会让工具谎报
        「已提问」却不暂停等人，正是此前「提问后没暂停」的根因。
        """
        from prismclaw.engine.toolkit.builtin.tool_helpers import _is_ask_user_question
        ask_user_calls = [c for c in calls if _is_ask_user_question(c)]
        if not ask_user_calls:
            return None
        if not hasattr(deps, "session_queue") or deps.session_queue is None:
            raise RuntimeError(
                "AskUserQuestion 被调用，但 deps.session_queue 为 None："
                "该交互式工具在没有澄清通道时仍对模型可见。"
                "只要 session_queue 不可用，就必须隐藏 AskUserQuestion"
                "（HeadlessModeReducer）。"
            )
        ask_call = ask_user_calls[0]
        return HITLCheckResult(
            needs_clarification=True,
            clarification_request=_build_clarify_request(ask_call, tool_ctx, deps, request),
        )

    async def wait_for_clarification(hitl: HITLCheckResult) -> str | None:
        """等待用户回答（B/S 架构独有，纯等待不落树）。

        **无限等待**：执行在此暂停，只有用户提交作答才恢复——不再有
        「超时自动继续」。返回 None 表示非用户作答（abort / stale /
        会话结束），骨架据此终止本轮，绝不合成假反馈让执行继续。

        **不落树 answer**：落树由 persist_clarified_turn 统一负责
        （assistant → tool 结果 → user answer 的唯一正确序——此处先落
        会把 user 消息插在提问之前并隔断 tool_calls ↔ tool 配对）。
        """
        # 进入 HITL 等待：更新共享 phase 状态，由 session 层独立 broadcaster
        # 周期广播（每 15s），使前端在长阻塞期间持续显示「等待你的回答…」。
        deps.session_queue.set_phase("awaiting_clarification")
        _wait_id = (
            hitl.clarification_request.approval_id
            if hasattr(hitl.clarification_request, "approval_id")
            else ""
        )
        # HITL 阻断诊断插桩：answered=false 即非用户作答（abort/stale），
        # 骨架会终止本轮；answered=true 才是用户提交后恢复执行。
        logger.warning(
            "hitl.wait.start approval_id=%s (无限等待，仅用户作答恢复)", _wait_id,
        )
        _wait_t0 = _time.monotonic()
        try:
            # 不传 timeout → 无限等待：超时不再让执行自动继续。
            answer = await deps.session_queue.wait_for_clarification(_wait_id)
        finally:
            deps.session_queue.set_phase(None)
        logger.warning(
            "hitl.wait.done approval_id=%s elapsed=%.1fs answered=%s",
            _wait_id, _time.monotonic() - _wait_t0, answer is not None,
        )
        if answer is None:
            # 非用户作答：不落树任何假反馈（落树会让模型误以为用户说过话），
            # 交由骨架终止本轮。
            return None
        # answer 落树由 persist_clarified_turn 统一负责（顺序：assistant →
        # tool 结果 → user answer）。此处先落会把 user 消息插在 assistant
        # 提问之前，且隔断 tool_calls ↔ tool 结果配对（provider 400）。
        return answer

    async def get_steering_messages() -> list[str]:
        """steering 消费（B/S 独有）：drain 会话队列 + 落树原样 user 消息。

        返回注入文本列表（空列表 = 无 steering）。落树走 HITL 同款路径
        （_append + MessageEntry，parent 链随 tree.leaf_id 前进），
        prepare_next_turn 树重建时带入 LLM 上下文；不进 pending_messages/
        appended 通道（不落树的注入压缩重启即丢）。run 结束时未消费的
        steering 由 chat_runner 的 settle_steering_after_run 兜底转回排队。
        非 web channel（conversation_id/session_queue 缺失）恒返回空。

        用户方向调整 = 新进展信号：清 frozen_call_defied 一次性嫌疑
        （all_blocked 边角下该标志会跨回合存活，用户纠偏后不应被陈旧
        证据杀）。
        """
        cid = request.conversation_id
        if cid is None or deps.session_queue is None:
            return []
        items = deps.session_queue.drain_steering(cid)
        if not items:
            return []
        from prismclaw.schema.agent.session import MessageEntry
        from prismclaw.schema.session.types import generate_entry_id, now_iso
        from prismclaw.engine.execution.loop.bootstrap import _append

        texts: list[str] = []
        for msg, _handler in items:
            content = msg.content or ""
            if not content:
                continue
            await _append(
                deps,
                tree,
                MessageEntry(
                    id=generate_entry_id(set(tree.entries.keys())),
                    parent_id=tree.leaf_id,
                    timestamp=now_iso(),
                    role="user",
                    content=content,
                ),
            )
            texts.append(content)
        if texts and tool_loop_state.frozen_call_defied:
            tool_loop_state.frozen_call_defied = False
            state_machine.update_tool_loop_state(tool_loop_state)
        return texts

    # ══════════════════════════════════════════════════════════
    # Assemble LoopConfig (三端口 + 内核绑定 + steering 插槽)
    # ══════════════════════════════════════════════════════════

    return LoopConfig(
        prepare=PreparePort(prepare_next_turn=prepare_next_turn),
        stream=StreamPort(stream_llm=stream_llm),
        execute=ExecutePort(
            execute_tools=execute_tools,
            before_tool_call=before_tool_call,
            after_tool_call=after_tool_call,
            persist_denied_turn=persist_denied_turn,
            persist_clarified_turn=persist_clarified_turn,
            check_hitl=check_hitl,
            wait_for_clarification=wait_for_clarification,
        ),
        should_continue=should_continue,
        get_steering_messages=get_steering_messages,
    )
