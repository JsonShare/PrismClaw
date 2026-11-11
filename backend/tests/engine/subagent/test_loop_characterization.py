"""步 1 characterization：钉住现 LoopMixin._loop 的黄金行为（重构 parity 基准）。

驱动面：裸 LoopMixin 子类（_dispatch_tool_calls/_emit_progress/_emit_terminal/
_resolve_progress_emitter 全部定义于 _loop_mixin.py 自身），只需注入
self._llm / self._tools / self._tool_timeout_s 三个属性。

覆盖 7 条黄金路径（SDD §P3b 迁移步骤 1）：
1. 自然完成（max_turns=2 与 max_turns=0 无界两形态）——outcome/turns/usage/messages
2. max_turns_exceeded——末轮工具执行完、无第 3 次 LLM 调用
3. abort——轮顶检查，0 次 LLM 调用，终态 aborted
4. deadline 超时——wait_for 打断 complete，turns=0，终态 timeout→failed
5. 工具执行抛异常——错误结果落 tool 消息，循环继续至自然完成
6. 未注册工具名——错误结果落 tool 消息，循环继续
7. should_break_after_results——首批工具结果后立即成功退出
8. 进度事件序列——tool_calling/tool_running/thinking/completed 时序

本文件在重构前后必须逐字不变地全绿（parity 契约）。
"""
import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from prismclaw.engine.subagent.core._loop_mixin import LoopMixin
from prismclaw.engine.subagent.core._result_types import SettledSubagentToolSurface
from prismclaw.engine.subagent.core._subagent_loop_runner import run_subagent_turn_loop
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.content.content_types import TextBlock, ToolResult

MANIFEST = SimpleNamespace(name="charter")


# ── mocks（对齐 test_medical_integration_full.py 的既有模式）────────────────


class MockResponse:
    def __init__(self, text: str = "", tool_calls: list | None = None,
                 input_tokens: int = 10, output_tokens: int = 5):
        self.text = text
        self.tool_calls = tool_calls or []
        self.usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)


class MockLLM:
    def __init__(self, responses: list, delay_s: float = 0.0):
        # responses 项可为 MockResponse 或 Exception 实例（直接抛出，含 CancelledError）
        self._responses = list(responses)
        self._idx = 0
        self.calls: list[dict[str, Any]] = []
        self._delay_s = delay_s

    async def complete(self, *, request, model, tools, abort_event, **kw):
        self.calls.append({
            "messages": [dict(m) for m in request.messages],
            "system": request.system,
            "model": model,
            "n_tools": len(tools or []),
        })
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        if self._idx >= len(self._responses):
            return MockResponse(text="(exhausted)")
        resp = self._responses[self._idx]
        self._idx += 1
        if isinstance(resp, BaseException):
            raise resp
        return resp


class MockTool:
    def __init__(self, name: str, execute_fn=None, raise_exc: Exception | None = None):
        self.name = name
        self.description = f"Mock {name} tool"
        self.parameters = {"type": "object", "properties": {}}
        self.side_effect = False
        self.tool_class = "read"
        self.effect_class = "local_mutation"
        self.visibility_scope = "main_only"
        self.read_only_allowed = True
        self._execute_fn = execute_fn
        self._raise_exc = raise_exc

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        if self._raise_exc is not None:
            raise self._raise_exc
        text = self._execute_fn(args) if self._execute_fn else f"[mock {self.name}]"
        return ToolResult(
            tool_call_id="mock_tc",
            content=[TextBlock(text=text)],
            is_error=False,
        )


class MockToolRegistry:
    def __init__(self, tools: list[MockTool]):
        self._tools = {t.name: t for t in tools}

    def get(self, name: str):
        return self._tools.get(name)

    def names(self) -> set[str]:
        return set(self._tools.keys())

    def list_for_llm(self, allowed_names=None) -> list[dict]:
        return [
            {"type": "function", "function": {"name": t.name,
             "description": t.description, "parameters": t.parameters}}
            for t in self._tools.values()
            if allowed_names is None or t.name in allowed_names
        ]

    def list_tools(self, allowed_names=None, subagent_depth=0):
        return list(self._tools.values())


class ProgressRecorder:
    def __init__(self):
        self.items: list[Any] = []

    def update(self, item) -> None:
        self.items.append(item)


# ── harness ──────────────────────────────────────────────────────────────────


class LoopHarness(LoopMixin):
    """裸 LoopMixin：只提供 _loop 依赖的三个属性。"""

    def __init__(self, llm: MockLLM, tools: MockToolRegistry, tool_timeout_s: float = 30.0):
        self._llm = llm
        self._tools = tools
        self._tool_timeout_s = tool_timeout_s


def _sub_ctx(**kw) -> ToolContext:
    return ToolContext(
        workspace_id="w1",
        workspace_path=Path("/tmp"),
        session_id="sess-charter",
        **kw,
    )


def _surface(names: tuple[str, ...] = ("noop",)) -> SettledSubagentToolSurface:
    return SettledSubagentToolSurface(
        visible_tool_names=names,
        callable_tool_names=names,
    )


def _tc(call_id: str, name: str = "noop", args: dict | None = None) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {})},
    }


async def _run(harness: LoopHarness, *, messages: list | None = None,
               max_turns: int = 8, timeout_s: float = 30.0,
               sub_ctx: ToolContext | None = None,
               llm: MockLLM | None = None,
               tools: MockToolRegistry | None = None):
    llm = llm or harness._llm
    tools = tools or harness._tools
    messages = messages if messages is not None else [{"role": "user", "content": "go"}]
    return await run_subagent_turn_loop(
        harness,
        messages=messages,
        system_prompt="sys",
        model="test-model",
        max_turns=max_turns,
        timeout_s=timeout_s,
        tool_surface=_surface(tuple(t.name for t in tools._tools.values())),
        tool_schemas=tools.list_for_llm(),
        sub_ctx=sub_ctx or _sub_ctx(),
        parent_ctx=_sub_ctx(),
        manifest=MANIFEST,
    )


# ── 1. 自然完成 ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("max_turns", [8, 0])  # 0 = 无迭代上限（fork 语义）
async def test_natural_completion(max_turns: int) -> None:
    llm = MockLLM([
        MockResponse(tool_calls=[_tc("tc1")]),
        MockResponse(text="all done", input_tokens=20, output_tokens=7),
    ])
    harness = LoopHarness(llm, MockToolRegistry([MockTool("noop")]))
    messages = [{"role": "user", "content": "go"}]

    outcome, turns, usage = await _run(harness, messages=messages, max_turns=max_turns)

    assert outcome == "success"
    assert turns == 2
    assert usage == {"input_tokens": 30, "output_tokens": 12}
    # 消息序列：user → assistant(tool_calls) → tool → assistant(text)
    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "assistant"]
    assert messages[1]["tool_calls"] == [_tc("tc1")]
    assert messages[2]["tool_call_id"] == "tc1"
    assert "[mock noop]" in str(messages[2]["content"])
    assert messages[3]["content"] == "all done"
    # 工具 schema 全程传入
    assert all(c["n_tools"] == 1 for c in llm.calls)


# ── 2. max_turns_exceeded ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_max_turns_exceeded() -> None:
    recorder = ProgressRecorder()
    llm = MockLLM([MockResponse(tool_calls=[_tc(f"tc{i}")]) for i in range(4)])
    harness = LoopHarness(llm, MockToolRegistry([MockTool("noop")]))
    messages = [{"role": "user", "content": "go"}]
    sub_ctx = _sub_ctx(subagent_progress_emitter=recorder)

    outcome, turns, usage = await _run(
        harness, messages=messages, max_turns=2, sub_ctx=sub_ctx, llm=llm
    )

    assert outcome == "max_turns_exceeded"
    assert turns == 2
    assert len(llm.calls) == 2  # 无第 3 次 LLM 调用
    # 第 2 次调用时只见第 1 轮的工具结果（第 2 轮结果尚未产生）
    last_call_tools = [m for m in _last_messages(llm) if m["role"] == "tool"]
    assert len(last_call_tools) == 1
    assert last_call_tools[0]["tool_call_id"] == "tc0"
    # 末轮 dispatch 后工具结果完整回填（assistant tool_calls 不留孤儿）
    assert [m["role"] for m in messages] == [
        "user", "assistant", "tool", "assistant", "tool",
    ]
    assert messages[4]["tool_call_id"] == "tc1"
    # 终端进度映射：max_turns_exceeded → failed
    assert recorder.items[-1].partial_status == "failed"


def _last_messages(llm: MockLLM) -> list[dict]:
    return llm.calls[-1]["messages"]


# ── 3. abort ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_abort_before_llm() -> None:
    llm = MockLLM([MockResponse(text="never")])
    harness = LoopHarness(llm, MockToolRegistry([MockTool("noop")]))
    sub_ctx = _sub_ctx()
    sub_ctx.abort.set()

    outcome, turns, usage = await _run(harness, sub_ctx=sub_ctx)

    assert outcome == "aborted"
    assert turns == 0
    assert len(llm.calls) == 0


# ── 4. deadline 超时 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deadline_timeout() -> None:
    llm = MockLLM([MockResponse(text="slow")], delay_s=0.5)
    harness = LoopHarness(llm, MockToolRegistry([MockTool("noop")]))

    outcome, turns, usage = await _run(harness, timeout_s=0.05)

    assert outcome == "timeout"
    assert turns == 0
    assert usage == {"input_tokens": 0, "output_tokens": 0}


@pytest.mark.asyncio
async def test_loop_top_deadline_exhausted_second_iteration() -> None:
    """轮顶墙钟检查（LLM 调用前）：第 1 轮工具执行耗尽预算，第 2 轮轮顶终止。"""
    llm = MockLLM([
        MockResponse(tool_calls=[_tc("tc1")]),
        MockResponse(text="never"),
    ])
    harness = LoopHarness(
        llm,
        MockToolRegistry([
            MockTool("noop", execute_fn=lambda args: _sleep_then_text(0.25)),
        ]),
    )

    outcome, turns, usage = await _run(harness, timeout_s=0.15)

    assert outcome == "timeout"
    assert turns == 1
    assert len(llm.calls) == 1  # 第 2 轮 LLM 调用前终止


def _sleep_then_text(_args: dict) -> str:
    time.sleep(0.25)
    return "[slow tool]"


# ── 5. 工具执行抛异常 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_exception_becomes_error_result() -> None:
    llm = MockLLM([
        MockResponse(tool_calls=[_tc("tc1")]),
        MockResponse(text="recovered"),
    ])
    harness = LoopHarness(
        llm, MockToolRegistry([MockTool("noop", raise_exc=RuntimeError("boom"))])
    )
    messages = [{"role": "user", "content": "go"}]

    outcome, turns, usage = await _run(harness, messages=messages, llm=llm)

    assert outcome == "success"
    assert turns == 2
    tool_msg = messages[2]
    assert tool_msg["role"] == "tool"
    # 异常被 dispatch 层转为错误结果回填（不中断循环）
    assert "boom" in str(tool_msg["content"]) or "RuntimeError" in str(tool_msg["content"])


# ── 6. 未注册工具名 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unregistered_tool_becomes_error_result() -> None:
    llm = MockLLM([
        MockResponse(tool_calls=[_tc("tc1", name="ghost")]),
        MockResponse(text="ok then"),
    ])
    harness = LoopHarness(llm, MockToolRegistry([MockTool("noop")]))
    messages = [{"role": "user", "content": "go"}]

    outcome, turns, usage = await _run(harness, messages=messages, llm=llm)

    assert outcome == "success"
    assert turns == 2
    tool_msg = messages[2]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "tc1"


# ── 7. should_break_after_results ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_should_break_after_results() -> None:
    llm = MockLLM([
        MockResponse(tool_calls=[_tc("tc1")]),
        MockResponse(text="never reached"),
    ])
    harness = LoopHarness(llm, MockToolRegistry([MockTool("noop")]))
    sub_ctx = _sub_ctx(should_break_after_results=True)

    outcome, turns, usage = await _run(harness, sub_ctx=sub_ctx)

    assert outcome == "success"
    assert turns == 1
    assert len(llm.calls) == 1  # 首批结果后立即退出


# ── 8. 进度事件序列 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_progress_event_sequence() -> None:
    recorder = ProgressRecorder()
    llm = MockLLM([
        MockResponse(tool_calls=[_tc("tc1", name="med__read")]),
        MockResponse(text="done"),
    ])
    tools = MockToolRegistry([MockTool("med__read")])
    harness = LoopHarness(llm, tools)
    sub_ctx = _sub_ctx(subagent_progress_emitter=recorder)

    await _run(harness, sub_ctx=sub_ctx, llm=llm)

    statuses = [item.partial_status for item in recorder.items]
    assert statuses == [
        "tool_calling",   # turn 1：LLM 返回工具调用
        "tool_running",   # turn 1：工具执行中（首个工具名，__→: 归一化）
        "tool_calling",   # turn 1：工具执行完毕
        "thinking",       # turn 2：无工具调用
        "completed",      # 终态
    ]
    tool_running = recorder.items[1]
    assert tool_running.current_tool == "med:read"
    assert tool_running.current_tool is not None
    assert all(item.current_tool is None for item in recorder.items[2:])
    assert all(item.subagent_name == "charter" for item in recorder.items)
    assert all(item.sub_session_id == "sess-charter" for item in recorder.items)


# ── 9. 复核补充：轮中 abort / 无 usage / 异常终态 ─────────────────────────────


@pytest.mark.asyncio
async def test_abort_mid_loop_after_completed_turn() -> None:
    """abort 在工具执行期触发：第 2 轮 LLM 调用前终止，已完成的轮次保留。"""
    llm = MockLLM([
        MockResponse(tool_calls=[_tc("tc1")]),
        MockResponse(text="never"),
    ])
    sub_ctx = _sub_ctx()

    def _set_abort(_args: dict) -> str:
        sub_ctx.abort.set()
        return "[abort trigger]"

    # dispatch 走 harness 注册表（self._tools），abort 副作用工具必须放这里
    harness = LoopHarness(llm, MockToolRegistry([MockTool("noop", execute_fn=_set_abort)]))

    outcome, turns, usage = await _run(harness, sub_ctx=sub_ctx, llm=llm)

    assert outcome == "aborted"
    assert turns == 1  # 第 1 轮完整保留
    assert len(llm.calls) == 1
    assert usage["input_tokens"] == 10  # 第 1 轮 usage 保留


@pytest.mark.asyncio
async def test_no_usage_accumulates_zero() -> None:
    """response.usage 为 None 时 usage_acc 保持 0。"""
    llm = MockLLM([
        MockResponse(tool_calls=[_tc("tc1")], input_tokens=10, output_tokens=5),
        MockResponse(text="done", input_tokens=0, output_tokens=0),
    ])
    llm._responses[1].usage = None
    harness = LoopHarness(llm, MockToolRegistry([MockTool("noop")]))

    outcome, turns, usage = await _run(harness, llm=llm)

    assert outcome == "success"
    assert usage == {"input_tokens": 10, "output_tokens": 5}


@pytest.mark.asyncio
async def test_llm_exception_emits_failed_terminal() -> None:
    """LLM 调用抛裸异常：终态 failed 后原样上抛。"""
    recorder = ProgressRecorder()
    llm = MockLLM([ValueError("llm exploded")])
    harness = LoopHarness(llm, MockToolRegistry([MockTool("noop")]))
    sub_ctx = _sub_ctx(subagent_progress_emitter=recorder)

    with pytest.raises(ValueError, match="llm exploded"):
        await _run(harness, sub_ctx=sub_ctx, llm=llm)

    assert recorder.items[-1].partial_status == "failed"


@pytest.mark.asyncio
async def test_cancelled_error_emits_aborted_terminal() -> None:
    """LLM 调用被取消：终态 aborted 后上抛 CancelledError。"""
    recorder = ProgressRecorder()
    llm = MockLLM([asyncio.CancelledError()])
    harness = LoopHarness(llm, MockToolRegistry([MockTool("noop")]))
    sub_ctx = _sub_ctx(subagent_progress_emitter=recorder)

    with pytest.raises(asyncio.CancelledError):
        await _run(harness, sub_ctx=sub_ctx, llm=llm)

    assert recorder.items[-1].partial_status == "aborted"
