"""run_loop 骨架最小可运行性证明。

run_loop 的消费方是主会话（runner.py）。本文件用最小 stub（空装配
Prepare 端口 + B/S 插槽缺省）+ 最小 RunLoopState，证明骨架可以完整
运行、按 ``Decision`` 正常终止、回合级计时成立。

骨架语义（适配器依赖）：

1. preflight 的 should_continue 在 LLM 调用之后、iteration 递增之前运行：
   谓词 `iteration < N` 产生 N+1 次 LLM 调用 → max_turns 终止判定必须放
   post 相位（且让末轮工具执行完）。
"""
import asyncio
from types import SimpleNamespace

import pytest

from prismclaw.engine.execution.control.kernel import Decision, LoopEvent
from prismclaw.engine.execution.loop.agent_loop import run_loop
from prismclaw.engine.execution.loop.types import (
    ExecutePort,
    LoopConfig,
    PreparePort,
    RunLoopState,
    StreamPort,
)


async def _noop_prepare(s: RunLoopState):
    """空装配 Prepare 端口桩。"""
    return None


def _fake_response(tool_calls: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        text="ok",
        tool_calls=tool_calls or [],
        finish_reason="stop",
        usage=None,
    )


@pytest.mark.asyncio
async def test_run_loop_minimal_config_pure_text_loop() -> None:
    """最小配置（B/S 插槽缺省）：无树、无 HITL、无落树，纯文本循环正常终止。

    谓词 `iteration < 2` 的实证语义：preflight 在调用后看到 pre-increment
    值 0,1,2 → 恰好 3 次 LLM 调用、iteration 终值 2。
    """
    state = RunLoopState()
    llm_calls = 0

    async def stream_llm(s: RunLoopState):
        nonlocal llm_calls
        llm_calls += 1
        s.llm_response = _fake_response()
        return
        yield  # pragma: no cover — 使本函数成为 async generator

    async def should_continue(
        s: RunLoopState, response=None, *, event: LoopEvent,
    ) -> Decision:
        return Decision("PROCEED" if s.iteration < 2 else "COMPLETE", "test")

    async def noop_execute(s: RunLoopState, response, allowed_calls):
        return
        yield  # pragma: no cover

    config = LoopConfig(
        prepare=PreparePort(prepare_next_turn=_noop_prepare),
        stream=StreamPort(stream_llm=stream_llm),
        execute=ExecutePort(execute_tools=noop_execute),
        should_continue=should_continue,
    )

    events: list = []
    async for event in run_loop(state, config):
        events.append(event)

    assert llm_calls == 3
    assert state.iteration == 2
    # 最小配置下不应产生任何工具事件
    assert not state.last_tool_results
    assert not state.blocked_tool_results


@pytest.mark.asyncio
async def test_run_loop_post_phase_terminates_after_last_turn_tools() -> None:
    """post 相位 max_turns 语义：末轮工具执行完毕后才终止（对齐 subagent 语义）。"""
    state = RunLoopState()
    llm_calls = 0
    executed_tools = 0

    async def stream_llm(s: RunLoopState):
        nonlocal llm_calls
        llm_calls += 1
        s.llm_response = _fake_response(
            tool_calls=[{"id": f"c{llm_calls}", "type": "function",
                         "function": {"name": "noop", "arguments": "{}"}}]
        )
        return
        yield  # pragma: no cover

    async def should_continue(
        s: RunLoopState, response=None, *, event: LoopEvent,
    ) -> Decision:
        if event.site == "post":
            # max_turns=2：calls_made = s.iteration + 1
            return Decision(
                "PROCEED" if s.iteration + 1 < 2 else "COMPLETE", "test",
            )
        return Decision("PROCEED", "test")  # preflight 放行，让工具执行

    async def execute_tools(s: RunLoopState, response, allowed_calls):
        nonlocal executed_tools
        executed_tools += len(allowed_calls)
        s.last_tool_results = []
        return
        yield  # pragma: no cover

    config = LoopConfig(
        prepare=PreparePort(prepare_next_turn=_noop_prepare),
        stream=StreamPort(stream_llm=stream_llm),
        execute=ExecutePort(execute_tools=execute_tools),
        should_continue=should_continue,
    )

    async for _ in run_loop(state, config):
        pass

    assert llm_calls == 2
    assert executed_tools == 2  # 末轮工具被执行后才终止


@pytest.mark.asyncio
async def test_turn_perf_counts_pure_text_turn() -> None:
    """纯文本回合（咨询形态）的回合级计时必须成立。

    回归目标：纯文本回合在骨架「无工具调用」分支直接 continue，跳过底部
    bookkeeping —— LoopTiming.total_iteration_ms 对它永远是 0，历史埋点
    对咨询类回合等于不存在。TurnPerf 在计时当处累加，必须拿到读数。
    """
    state = RunLoopState()

    async def stream_llm(s: RunLoopState):
        await asyncio.sleep(0.01)  # 造出可测量的 LLM 耗时
        s.llm_response = _fake_response()
        return
        yield  # pragma: no cover

    async def should_continue(
        s: RunLoopState, response=None, *, event: LoopEvent,
    ) -> Decision:
        return Decision("COMPLETE", "test")  # 一轮即终止

    async def noop_execute(s: RunLoopState, response, allowed_calls):
        return
        yield  # pragma: no cover

    config = LoopConfig(
        prepare=PreparePort(prepare_next_turn=_noop_prepare),
        stream=StreamPort(stream_llm=stream_llm),
        execute=ExecutePort(execute_tools=noop_execute),
        should_continue=should_continue,
    )

    async for _ in run_loop(state, config):
        pass

    perf = state.perf
    assert perf.llm_calls == 1
    assert perf.tool_calls == 0
    assert perf.llm_ms >= 10.0, f"纯文本回合未计入 LLM 耗时: {perf.llm_ms}"
    assert perf.wall_ms() >= perf.llm_ms
    # 空装配 prepare 端口（必填契约）→ 准备段仅含空实现调用开销
    assert 0.0 <= perf.prep_ms < 5.0
    # 自身开销 = 墙钟 - 三段，恒非负
    assert perf.loop_self_ms() >= 0.0
    # 历史埋点对纯文本回合无读数（盲区证据，防止回归时误删 TurnPerf）
    assert state.timing.total_iteration_ms == 0.0


@pytest.mark.asyncio
async def test_turn_perf_counts_tool_calls_across_iterations() -> None:
    """多轮工具回合：iterations / llm_calls / tool_calls 跨迭代累加。"""
    state = RunLoopState()
    llm_calls = 0

    async def stream_llm(s: RunLoopState):
        nonlocal llm_calls
        llm_calls += 1
        s.llm_response = _fake_response(
            tool_calls=[{"id": f"c{llm_calls}", "type": "function",
                         "function": {"name": "noop", "arguments": "{}"}}]
        )
        return
        yield  # pragma: no cover

    async def should_continue(
        s: RunLoopState, response=None, *, event: LoopEvent,
    ) -> Decision:
        if event.site == "post":
            return Decision(
                "PROCEED" if s.iteration + 1 < 2 else "COMPLETE", "test",
            )
        return Decision("PROCEED", "test")

    async def execute_tools(s: RunLoopState, response, allowed_calls):
        s.last_tool_results = []
        return
        yield  # pragma: no cover

    config = LoopConfig(
        prepare=PreparePort(prepare_next_turn=_noop_prepare),
        stream=StreamPort(stream_llm=stream_llm),
        execute=ExecutePort(execute_tools=execute_tools),
        should_continue=should_continue,
    )

    async for _ in run_loop(state, config):
        pass

    perf = state.perf
    assert perf.llm_calls == 2
    assert perf.tool_calls == 2
    assert perf.tools_ms >= 0.0
