"""Phase 3 端口化契约测试。

锁定三周期状态模型与三端口 LoopConfig 的结构契约：

- 三端口构造期必填（P4 fast fail：缺端口即 TypeError）
- 骨架扁平名委托到端口与 frame/ledger 载体（SDD §5.1 冻结契约的
  唯一兼容层），闭包直写 frame/ledger 的单一真相
- IterationFrame / RunLedger 的默认独立实例（跨 RunLoopState 不共享）
"""
from __future__ import annotations

import pytest

from prismclaw.engine.execution.control.kernel import Decision, LoopEvent
from prismclaw.engine.execution.loop.types import (
    ExecutePort,
    LoopConfig,
    PreparePort,
    RunLedger,
    RunLoopState,
    StreamPort,
)


async def _noop_prepare(state):
    return None


async def _noop_stream(state):
    return
    yield  # pragma: no cover — async generator 契约


async def _noop_execute(state, response, allowed_calls):
    return
    yield  # pragma: no cover


async def _noop_should_continue(state, response=None, *, event):
    return Decision("COMPLETE", "test")


def _full_config() -> LoopConfig:
    return LoopConfig(
        prepare=PreparePort(prepare_next_turn=_noop_prepare),
        stream=StreamPort(stream_llm=_noop_stream),
        execute=ExecutePort(execute_tools=_noop_execute),
        should_continue=_noop_should_continue,
    )


# ── P4：三端口构造期必填 ──


def test_loop_config_requires_all_three_ports():
    with pytest.raises(TypeError):
        LoopConfig(
            stream=StreamPort(stream_llm=_noop_stream),
            execute=ExecutePort(execute_tools=_noop_execute),
            should_continue=_noop_should_continue,
        )
    with pytest.raises(TypeError):
        LoopConfig(
            prepare=PreparePort(prepare_next_turn=_noop_prepare),
            execute=ExecutePort(execute_tools=_noop_execute),
            should_continue=_noop_should_continue,
        )
    with pytest.raises(TypeError):
        LoopConfig(
            prepare=PreparePort(prepare_next_turn=_noop_prepare),
            stream=StreamPort(stream_llm=_noop_stream),
            should_continue=_noop_should_continue,
        )
    with pytest.raises(TypeError):
        LoopConfig(
            prepare=PreparePort(prepare_next_turn=_noop_prepare),
            stream=StreamPort(stream_llm=_noop_stream),
            execute=ExecutePort(execute_tools=_noop_execute),
        )


def test_loop_config_flat_names_delegate_to_ports():
    async def prepare(state):
        return None

    async def execute(state, response, calls):
        return
        yield  # pragma: no cover

    cfg = LoopConfig(
        prepare=PreparePort(prepare_next_turn=prepare),
        stream=StreamPort(stream_llm=_noop_stream),
        execute=ExecutePort(execute_tools=execute),
        should_continue=_noop_should_continue,
    )
    # 骨架扁平名是端口成员的同一闭包（冻结契约兼容层）
    assert cfg.prepare_next_turn is prepare
    assert cfg.stream_llm is _noop_stream
    assert cfg.execute_tools is execute
    # 可选插槽缺省 None
    assert cfg.before_tool_call is None
    assert cfg.after_tool_call is None
    assert cfg.persist_denied_turn is None
    assert cfg.persist_clarified_turn is None
    assert cfg.check_hitl is None
    assert cfg.wait_for_clarification is None
    assert cfg.get_steering_messages is None


# ── 状态三分：frame / ledger 载体 ──


def test_run_loop_state_flat_names_delegate_to_frame_and_ledger():
    state = RunLoopState()
    # 骨架扁平名读写 frame 单一真相
    state.llm_response = object()
    assert state.frame.llm_response is state.llm_response
    state.last_tool_results = [1, 2]
    assert state.frame.last_tool_results == [1, 2]
    state.pending_messages = [{"role": "user", "content": "x"}]
    assert state.frame.pending_messages[0]["content"] == "x"
    state.max_tokens_hit_in_step = True
    assert state.frame.max_tokens_hit_in_step is True
    # 账本字段只存在于 ledger（闭包直写，无扁平别名）
    state.ledger.total_input_tokens += 7
    assert state.ledger.total_input_tokens == 7
    state.ledger.evidence_ledger.append(object())
    assert len(state.ledger.evidence_ledger) == 1
    # 迭代单元与账本均为独立实例（默认工厂，不跨状态共享）
    other = RunLoopState()
    assert state.frame is not other.frame
    assert state.ledger is not other.ledger
    assert other.ledger.total_input_tokens == 0
    assert other.frame.last_tool_results == []


def test_run_ledger_is_run_scoped_append_only_carrier():
    ledger = RunLedger(evidence_ledger=[], total_input_tokens=10)
    assert ledger.total_input_tokens == 10
    assert ledger.total_output_tokens == 0
    assert ledger.total_cache_creation_tokens == 0
    assert ledger.total_cache_read_tokens == 0