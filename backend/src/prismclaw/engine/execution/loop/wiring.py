"""wiring — build_loop_config：把 stage 函数绑定到 LoopConfig 端口。

run_loop 不知道任何子系统的存在，只消费 LoopConfig 的三端口 + 两个插槽。
LoopRuntime 的装配在 ``runtime.build_runtime``；本文件只做端口绑定。
"""

from __future__ import annotations

import logging
from functools import partial
from typing import TYPE_CHECKING

from prismclaw.engine.execution.loop import continue_ as _continue
from prismclaw.engine.execution.loop import stream as _stream
from prismclaw.engine.execution.loop import terminal as _terminal
from prismclaw.engine.execution.loop import tools as _tools
from prismclaw.engine.execution.loop import turn_prepare as _prepare
from prismclaw.engine.execution.loop.runtime import LoopRuntime, build_runtime
from prismclaw.engine.execution.loop.types import (
    ExecutePort,
    LoopConfig,
    PreparePort,
    RunLoopState,
    StreamPort,
)
from prismclaw.schema.llm.types import LLMResponse

if TYPE_CHECKING:
    from prismclaw.engine.execution.dispatch.call_handling import ParsedToolCall
    from prismclaw.schema.content.streaming import ToolCallEnd

logger = logging.getLogger(__name__)


def build_loop_config(rt: LoopRuntime) -> LoopConfig:
    """组装 LoopConfig：原地装配 Runtime，再把 stage 函数绑定为端口。"""
    build_runtime(rt)

    # persist_turn 不消费 state；端口签名首参是 state，用薄适配丢弃。
    async def _persist_denied(
        state: RunLoopState,
        response: LLMResponse,
        blocked_events: list[ToolCallEnd],
    ) -> None:
        await _terminal.persist_turn(rt, response, "denied", blocked_events=blocked_events)

    async def _persist_clarified(
        state: RunLoopState,
        response: LLMResponse,
        allowed_calls: list[ParsedToolCall],
        ask_call_id: str,
        answer: str | None,
    ) -> None:
        await _terminal.persist_turn(
            rt,
            response,
            "clarified",
            allowed_calls=allowed_calls,
            ask_call_id=ask_call_id,
            answer=answer,
        )

    return LoopConfig(
        prepare=PreparePort(prepare_next_turn=partial(_prepare.prepare_next_turn, rt)),
        stream=StreamPort(stream_llm=partial(_stream.stream_llm, rt)),
        execute=ExecutePort(
            execute_tools=partial(_tools.execute_tools, rt),
            before_tool_call=partial(_tools.gate_tool_call, rt),
            after_tool_call=partial(_tools.after_tool_call, rt),
            persist_denied_turn=_persist_denied,
            persist_clarified_turn=_persist_clarified,
            check_hitl=partial(_terminal.check_hitl, rt),
            wait_for_clarification=partial(_terminal.wait_for_clarification, rt),
        ),
        should_continue=partial(_continue.should_continue, rt),
        get_steering_messages=partial(_terminal.get_steering_messages, rt),
    )
