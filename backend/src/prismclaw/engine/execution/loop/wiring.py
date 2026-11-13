"""wiring — build_loop_config：把 stage 函数绑定到 LoopConfig 端口。

run_loop 不知道任何子系统的存在，只消费 LoopConfig 的三端口 + 两个插槽。
LoopRuntime 的装配在 ``runtime.build_runtime``；本文件只做端口绑定。
"""
from __future__ import annotations

import logging
from functools import partial
from typing import Any

from prismclaw.engine.execution.loop import continue_ as _continue
from prismclaw.engine.execution.loop import prepare as _prepare
from prismclaw.engine.execution.loop import stream as _stream
from prismclaw.engine.execution.loop import terminal as _terminal
from prismclaw.engine.execution.loop import tools as _tools
from prismclaw.engine.execution.loop.runtime import build_runtime
from prismclaw.engine.execution.loop.types import (
    ExecutePort,
    LoopConfig,
    PreparePort,
    RunLoopState,
    StreamPort,
)

logger = logging.getLogger(__name__)


def build_loop_config(
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
    mode: str = "standard",
    task_runtime: Any = None,
    snapshot: Any = None,
    tree: Any = None,
    traj_scope: Any = None,
    attachment_guidance: str | None = None,
) -> LoopConfig:
    """组装 LoopConfig：装配 Runtime，再把 stage 函数绑定为端口。"""
    rt = build_runtime(
        deps=deps,
        request=request,
        recovery=recovery,
        state_machine=state_machine,
        tool_loop_state=tool_loop_state,
        run_state=run_state,
        initial_task_spec=initial_task_spec,
        retry_counts=retry_counts,
        response_policy=response_policy,
        pipeline=pipeline,
        tool_ctx=tool_ctx,
        tool_workspace_path=tool_workspace_path,
        effective_model=effective_model,
        abort_event=abort_event,
        run_deadline_s=run_deadline_s,
        run_started=run_started,
        mode=mode,
        task_runtime=task_runtime,
        snapshot=snapshot,
        tree=tree,
        traj_scope=traj_scope,
        attachment_guidance=attachment_guidance,
    )

    # persist_turn 不消费 state；端口签名首参是 state，用薄适配丢弃。
    async def _persist_denied(
        state: RunLoopState,
        response: Any,
        blocked_events: list[Any],
    ) -> None:
        await _terminal.persist_turn(rt, response, "denied", blocked_events=blocked_events)

    async def _persist_clarified(
        state: RunLoopState,
        response: Any,
        allowed_calls: list[Any],
        ask_call_id: str,
        answer: str | None,
    ) -> None:
        await _terminal.persist_turn(
            rt, response, "clarified",
            allowed_calls=allowed_calls, ask_call_id=ask_call_id, answer=answer,
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
