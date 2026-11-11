"""run_loop 骨架级集成测试 — doom-loop 防护的 phase 路由契约。

用最小 stub（无 deps 子系统）驱动真实 run_loop，锁定重构的核心风险区：

  - 无工具回合 → 路径 C 在 preflight 运行并正常 done（回归 A1）
  - 工具回合 → record+evaluate 只在 post 发生，每回合恰好一次
  - execute_tools 异常 → 无配对结果按失败计（consecutive_errors 复活）
  - last_tool_results 每回合清空（无跨回合残留错配）
  - 全拒回合 → persist_denied_turn 落树后续跑（冻结 escalate 只置
    嫌疑标志，不产生终态）
"""
from __future__ import annotations

from typing import Any

from prismclaw.engine.execution.control.kernel import Decision, LoopEvent
from prismclaw.engine.execution.loop.agent_loop import run_loop
from prismclaw.engine.execution.loop.types import (
    BeforeToolCallContext,
    BeforeToolCallResult,
    ExecutePort,
    LoopConfig,
    PreparePort,
    RunLoopState,
    StreamPort,
)
from prismclaw.schema.content.streaming import Done


async def _noop_prepare(state: RunLoopState):
    """无装配 Prepare 端口桩。"""
    return None


class _FakeResponse:
    """最小 LLMResponse 替身：只带 tool_calls 与文本内容。"""

    def __init__(self, tool_calls: list[dict] | None = None, text: str = "ok"):
        self.tool_calls = tool_calls or []
        self.finish_reason = "stop"
        self.text = text


class _ScriptedLoop:
    """按脚本推进的 stream_llm + 全套回调记录器。

    script: 每回合返回 _FakeResponse；None 表示终止流。
    """

    def __init__(self, script: list[_FakeResponse | None]):
        self.script = list(script)
        self.phases: list[tuple[str, int]] = []          # (phase, turn)
        self.records: list[dict[str, Any]] = []          # record_tool_call_observation 快照
        self.state = RunLoopState()
        self.turn = 0

    def build_config(self) -> LoopConfig:
        async def stream_llm(state: RunLoopState):
            if not self.script:
                return
            resp = self.script.pop(0)
            if resp is None:
                return
            state.llm_response = resp
            yield  # stream_llm 是 async generator（LoopConfig 契约）

        async def should_continue(
            state: RunLoopState,
            response: Any | None = None,
            *,
            event: LoopEvent,
        ) -> Decision:
            self.phases.append((event.site, self.turn))
            if event.site == "preflight" and response is not None and not response.tool_calls:
                # 模拟路径 C：无工具回合 → done
                state.done_event = Done(final_message="done")
                return Decision("COMPLETE", "test_done")
            return Decision("PROCEED", "test_proceed")

        async def before_tool_call(
            before_ctx: BeforeToolCallContext,
        ) -> BeforeToolCallResult | None:
            return None

        async def execute_tools(
            state: RunLoopState, response: Any, allowed_calls: list[Any],
        ):
            # 记录本回合的观察快照（模拟路径 D record 的输入条件）
            self.records.append({
                "turn": self.turn,
                "results_len": len(state.last_tool_results),
                "first_result_error": (
                    getattr(state.last_tool_results[0], "is_error", None)
                    if state.last_tool_results else None
                ),
            })
            yield  # execute_tools 是 async generator

        cfg = LoopConfig(
            prepare=PreparePort(prepare_next_turn=_noop_prepare),
            stream=StreamPort(stream_llm=stream_llm),
            execute=ExecutePort(
                execute_tools=execute_tools,
                before_tool_call=before_tool_call,
            ),
            should_continue=should_continue,
        )
        return cfg

    async def run(self) -> list[Any]:
        events = []
        cfg = self.build_config()
        async for ev in run_loop(self.state, cfg):
            events.append(ev)
            # 每个 LLM 回合结束（llm_end boundary）推进 turn 计数
            if type(ev).__name__ == "IterationBoundary" and getattr(ev, "kind", "") == "llm_end":
                self.turn += 1
        return events


async def _drive(script: list[_FakeResponse | None]) -> _ScriptedLoop:
    loop = _ScriptedLoop(script)
    await loop.run()
    return loop


# ── A1 回归：无工具回合在 preflight done ──


async def test_no_tool_turn_completes_via_preflight():
    loop = await _drive([_FakeResponse(tool_calls=None)])
    # 路径 C 在 preflight 触发并终止（而非无限重询 LLM）
    assert ("preflight", 0) in loop.phases
    assert ("post", 0) not in loop.phases
    assert isinstance(loop.state.done_event, Done)
    assert loop.state.terminal_error is None


# ── phase 路由：工具回合 preflight + post 各一次 ──


async def test_tool_turn_runs_preflight_then_post():
    call = {"id": "c1", "function": {"name": "read", "arguments": "{}"}}
    loop = await _drive([
        _FakeResponse(tool_calls=[call]),
        _FakeResponse(tool_calls=None),  # 下一回合 done
    ])
    phases_turn0 = [p for (p, t) in loop.phases if t == 0]
    assert phases_turn0 == ["preflight", "post"]
    # all_blocked 不应出现（无工具被拒）
    assert "all_blocked" not in [p for (p, _) in loop.phases]


# ── last_tool_results 每回合清空（无跨回合残留）──


async def test_last_tool_results_cleared_each_turn():
    call = {"id": "c1", "function": {"name": "read", "arguments": "{}"}}

    class _R:
        tool_call_id = "c1"
        is_error = False

    loop = _ScriptedLoop([
        _FakeResponse(tool_calls=[call]),
        _FakeResponse(tool_calls=[call]),
        _FakeResponse(tool_calls=None),  # done 收束
    ])

    async def execute_tools(state, response, allowed_calls):
        # 回合 0 正常产出；回合 1 模拟异常（无产出）——若骨架未清空
        # 残留，回合 1 会看到回合 0 的结果
        if loop.turn == 0:
            state.last_tool_results = [_R()]
        yield

    async def stream_llm(state: RunLoopState):
        if not loop.script:
            return
        resp = loop.script.pop(0)
        if resp is None:
            return
        state.llm_response = resp
        yield

    async def should_continue(state, response=None, *, event: LoopEvent) -> Decision:
        if event.site == "preflight" and response is not None and not response.tool_calls:
            state.done_event = Done(final_message="done")
            return Decision("COMPLETE", "test_done")
        return Decision("PROCEED", "test_proceed")

    async def before_tool_call(ctx):
        return None

    cfg = LoopConfig(
        prepare=PreparePort(prepare_next_turn=_noop_prepare),
        stream=StreamPort(stream_llm=stream_llm),
        execute=ExecutePort(
            execute_tools=execute_tools,
            before_tool_call=before_tool_call,
        ),
        should_continue=should_continue,
    )
    events = []
    async for ev in run_loop(loop.state, cfg):
        events.append(ev)
        if type(ev).__name__ == "IterationBoundary" and getattr(ev, "kind", "") == "llm_end":
            loop.turn += 1

    # 回合 1 无产出 → 骨架清空使 last_tool_results 为空
    # （done 回合已把 done_event 挂上；残留检查在终止后仍有效）
    assert loop.state.last_tool_results == []


# ── 全拒回合：persist_denied_turn 被调用 + blocked 收集 ──


async def test_all_denied_persists_turn():
    call = {"id": "c1", "function": {"name": "read", "arguments": "{}"}}
    loop = _ScriptedLoop([
        _FakeResponse(tool_calls=[call]),
        _FakeResponse(tool_calls=None),  # done 收束
    ])

    async def before_tool_call(ctx) -> BeforeToolCallResult | None:
        return BeforeToolCallResult(block=True, reason="denied by gate")

    denied_calls: list[tuple[Any, list]] = []

    async def persist_denied_turn(state, response, blocked_events):
        denied_calls.append((response, list(blocked_events)))

    async def stream_llm(state: RunLoopState):
        if not loop.script:
            return
        resp = loop.script.pop(0)
        if resp is None:
            return
        state.llm_response = resp
        yield

    async def should_continue(state, response=None, *, event: LoopEvent) -> Decision:
        if event.site == "preflight" and response is not None and not response.tool_calls:
            state.done_event = Done(final_message="done")
            return Decision("COMPLETE", "test_done")
        return Decision("PROCEED", "test_proceed")

    async def execute_tools(state, response, allowed_calls):
        yield

    cfg = LoopConfig(
        prepare=PreparePort(prepare_next_turn=_noop_prepare),
        stream=StreamPort(stream_llm=stream_llm),
        execute=ExecutePort(
            execute_tools=execute_tools,
            before_tool_call=before_tool_call,
            persist_denied_turn=persist_denied_turn,
        ),
        should_continue=should_continue,
    )
    events = []
    async for ev in run_loop(loop.state, cfg):
        events.append(ev)

    # persist_denied_turn 恰好一次，携带本回合 response 与 blocked 事件
    assert len(denied_calls) == 1
    assert denied_calls[0][0] is loop.script or denied_calls[0][0].tool_calls == [call]
    assert len(denied_calls[0][1]) == 1
    # blocked 事件为 ToolCallEnd 形态（含 is_error result）
    _evt = denied_calls[0][1][0]
    assert type(_evt).__name__ == "ToolCallEnd"
    assert _evt.result.is_error is True
    assert "denied by gate" in _evt.result.content[0].text
    # 消费后清空（不泄漏到下一回合）
    assert loop.state.blocked_tool_results == []
    # execute_tools 未被调用（全拒不执行）
    assert loop.state.last_tool_results == []


# ── HITL：wait_for_clarification 被调用（answer 落树由闭包负责）──


async def test_hitl_waits_and_closes_timeline():
    ask_call = {"id": "c1", "function": {"name": "ask_user_question", "arguments": "{}"}}
    loop = _ScriptedLoop([
        _FakeResponse(tool_calls=[ask_call]),
        _FakeResponse(tool_calls=None),  # done 收束
    ])

    async def before_tool_call(ctx) -> BeforeToolCallResult | None:
        return None

    clarification_calls: list[Any] = []

    class _FakeClarifyResult:
        needs_clarification = True
        clarification_request = {"question": "which file?", "tool_call_id": "c1"}

    def check_hitl(state, calls):
        return _FakeClarifyResult()

    async def wait_for_clarification(hitl_result):
        clarification_calls.append(hitl_result)
        return "方案 B"

    async def stream_llm(state: RunLoopState):
        if not loop.script:
            return
        resp = loop.script.pop(0)
        if resp is None:
            return
        state.llm_response = resp
        yield

    async def should_continue(state, response=None, *, event: LoopEvent) -> Decision:
        if event.site == "preflight" and response is not None and not response.tool_calls:
            state.done_event = Done(final_message="done")
            return Decision("COMPLETE", "test_done")
        return Decision("PROCEED", "test_proceed")

    async def execute_tools(state, response, allowed_calls):
        yield

    cfg = LoopConfig(
        prepare=PreparePort(prepare_next_turn=_noop_prepare),
        stream=StreamPort(stream_llm=stream_llm),
        execute=ExecutePort(
            execute_tools=execute_tools,
            before_tool_call=before_tool_call,
            check_hitl=check_hitl,
            wait_for_clarification=wait_for_clarification,
        ),
        should_continue=should_continue,
    )
    events = [ev async for ev in run_loop(loop.state, cfg)]

    assert len(clarification_calls) == 1
    # answer 不注入 pending_messages（落树由闭包负责，双通道会重复）
    assert loop.state.pending_messages == []
    # HITL 时间线闭合：wait 返回的答案经合成 ToolCallEnd 下发，
    # 前端「向用户提问」行据此落定并展示用户回答
    clarify_ends = [
        ev for ev in events
        if type(ev).__name__ == "ToolCallEnd" and ev.tool_call_id == "c1"
    ]
    assert len(clarify_ends) == 1
    assert clarify_ends[0].result.is_error is False
    assert clarify_ends[0].result.content[0].text == "方案 B"


# ── 压缩重启：TurnUpdate.pending_messages（重放注入）被应用到 state ──


async def test_compression_restart_replays_pending_messages():
    """restart 检测发生在 LLM 调用之前，被合并进 query_loop_state 的一次性
    注入尚未送达模型；骨架 restart 分支丢弃本轮上下文后必须重放 pending，
    否则注入永久丢失（appended 通道不落树，树重建无法恢复）。"""
    from prismclaw.engine.execution.loop.types import TurnUpdate

    loop = _ScriptedLoop([
        _FakeResponse(tool_calls=None),  # 重启后正常 done
    ])

    replay_injections = [{"role": "user", "content": "budget warning"}]
    restart_seen: list[TurnUpdate] = []

    async def prepare_next_turn(state: RunLoopState) -> TurnUpdate | None:
        # 首次调用返回 restart=True + 重放 pending；后续返回 None
        if not restart_seen:
            restart_seen.append(TurnUpdate(restart=True, pending_messages=list(replay_injections)))
            return restart_seen[0]
        return None

    async def stream_llm(state: RunLoopState):
        if not loop.script:
            return
        resp = loop.script.pop(0)
        if resp is None:
            return
        state.llm_response = resp
        yield

    async def should_continue(state, response=None, *, event: LoopEvent) -> Decision:
        if event.site == "preflight" and response is not None and not response.tool_calls:
            state.done_event = Done(final_message="done")
            return Decision("COMPLETE", "test_done")
        return Decision("PROCEED", "test_proceed")

    async def before_tool_call(ctx):
        return None

    async def execute_tools(state, response, allowed_calls):
        yield

    cfg = LoopConfig(
        prepare=PreparePort(prepare_next_turn=prepare_next_turn),
        stream=StreamPort(stream_llm=stream_llm),
        execute=ExecutePort(
            execute_tools=execute_tools,
            before_tool_call=before_tool_call,
        ),
        should_continue=should_continue,
    )
    async for ev in run_loop(loop.state, cfg):
        pass

    assert len(restart_seen) == 1
    # 重放注入被应用到 state.pending_messages（prepare 端口下次调用时
    # 合并进重建的 query_loop_state——LLM 上下文恢复注入）
    assert loop.state.pending_messages == replay_injections