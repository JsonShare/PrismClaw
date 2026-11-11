"""POST_TOOL hook 公共汇聚层单测。

验证 dispatch/execution_entry._apply_post_tool_hooks 的三段语义：
1. matcher 命中且 hook 返回 updated_mcp_tool_output → ToolResult 文本被改写
   （主循环与子代理两条执行路径都要生效）；
2. matcher 未命中 → 结果原样通过；
3. hook 抛异常/超时 → 不阻断不改写（non_blocking）。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from prismclaw.engine.execution.dispatch.execution_entry import (
    execute_tool_calls,
    execute_preflighted_tool_calls,
)
from prismclaw.engine.hooks import HookDispatcher, HookEvent
from prismclaw.engine.plugins.hooks.handlers import (
    HookHandler,
    MatcherFilteredHandler,
)
from prismclaw.engine.plugins.hooks.schema import HookContext, HookOutcome, HookResult
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.agent.data_types import TextBlock
from prismclaw.schema.content.content_types import ToolResult


# ── 测试桩 ────────────────────────────────────────────────────────────────


@dataclass
class _RecordingHandler(HookHandler):
    """记录收到的 context 并返回预设结果。"""

    seen: list[HookContext] = field(default_factory=list)
    result: HookResult | None = None
    raise_exc: Exception | None = None

    async def execute(self, context: HookContext) -> HookResult:
        self.seen.append(context)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.result or HookResult.success()


class _StubTool:
    """返回固定文本的只读工具（concurrency-safe，走并行桶）。"""

    def __init__(self, name: str, text: str) -> None:
        self.name = name
        self.description = "stub"
        self.parameters: dict[str, Any] = {"type": "object", "properties": {}}
        self.side_effect = False
        self.tool_class = "read"
        self.effect_class = "pure_read"
        self.is_concurrency_safe = True
        self._text = text

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(
            tool_call_id="",
            content=[TextBlock(text=self._text)],
            is_error=False,
        )


class _StubRegistry:
    def __init__(self, tools: list[_StubTool]) -> None:
        self._tools = {t.name: t for t in tools}

    def get(self, name: str | None):
        if not name:
            return None
        tool = self._tools.get(name)
        if tool is not None:
            return tool
        # 对齐真实 ToolRegistry：LLM 名（__ 形式）回退到 canonical（: 形式）
        if "__" in name:
            return self._tools.get(name.replace("__", ":", 1))
        return None

    def names(self):
        return list(self._tools.keys())


def _make_ctx(dispatcher: HookDispatcher | None) -> ToolContext:
    return ToolContext(
        workspace_id="ws",
        workspace_path=Path("/tmp"),
        session_id="sess-test",
        hook_dispatcher=dispatcher,
    )


def _call(name: str, call_id: str) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps({"q": "x"})},
    }


def _text(result: ToolResult) -> str:
    return "".join(b.text for b in result.content if hasattr(b, "text"))


LONG_OUTPUT = json.dumps({
    "query": "test",
    "results": [
        {"title": "paper-1", "abstract": "a" * 500},
        {"title": "paper-2", "abstract": "b" * 300},
    ],
})

SLIMMED_OUTPUT = json.dumps({
    "query": "test",
    "results": [
        {"title": "paper-1", "abstract": "a" * 200 + "…"},
        {"title": "paper-2", "abstract": "b" * 200 + "…"},
    ],
})


# ── 用例 ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_matcher_hit_rewrites_result_on_main_loop_path():
    """matcher 命中 + hook 返回改写 → execute_tool_calls 结果文本被替换。"""
    recorder = _RecordingHandler(
        result=HookResult(
            outcome="success",
            updated_mcp_tool_output=SLIMMED_OUTPUT,
        )
    )
    dispatcher = HookDispatcher()
    dispatcher.register(
        HookEvent.POST_TOOL,
        MatcherFilteredHandler(recorder, r"tech-mining\.science-mining__fanout_search"),
    )

    registry = _StubRegistry([
        _StubTool("tech-mining.science-mining:fanout_search", LONG_OUTPUT),
    ])
    ctx = _make_ctx(dispatcher)

    results = await execute_tool_calls(
        registry, [_call("tech-mining.science-mining__fanout_search", "c1")], ctx,
    )

    assert len(results) == 1
    assert _text(results[0]) == SLIMMED_OUTPUT
    assert results[0].tool_call_id == "c1"
    assert not results[0].is_error
    # hook 收到完整载荷：tool_name 是 LLM 调用名（__ 形式），tool_output 是结果文本
    assert len(recorder.seen) == 1
    assert recorder.seen[0].tool_name == "tech-mining.science-mining__fanout_search"
    assert recorder.seen[0].tool_output == LONG_OUTPUT


@pytest.mark.asyncio
async def test_matcher_miss_passes_through():
    """matcher 不匹配的工具 → hook 不触发，结果原样。"""
    recorder = _RecordingHandler(
        result=HookResult(updated_mcp_tool_output="SHOULD_NOT_APPLY")
    )
    dispatcher = HookDispatcher()
    dispatcher.register(HookEvent.POST_TOOL, MatcherFilteredHandler(recorder, r"^Bash$"))

    registry = _StubRegistry([_StubTool("read_file", "original")])
    ctx = _make_ctx(dispatcher)

    results = await execute_tool_calls(registry, [_call("read_file", "c1")], ctx)

    assert _text(results[0]) == "original"
    assert recorder.seen == []  # matcher 过滤在 dispatch 前短路


@pytest.mark.asyncio
async def test_hook_failure_is_non_blocking():
    """hook 抛异常 → 结果不改写、不抛出。"""
    recorder = _RecordingHandler(raise_exc=RuntimeError("hook crashed"))
    dispatcher = HookDispatcher()
    dispatcher.register(HookEvent.POST_TOOL, recorder)

    registry = _StubRegistry([_StubTool("read_file", "original")])
    ctx = _make_ctx(dispatcher)

    results = await execute_tool_calls(registry, [_call("read_file", "c1")], ctx)

    assert _text(results[0]) == "original"


@pytest.mark.asyncio
async def test_no_dispatcher_is_zero_overhead():
    """无 hook_dispatcher → 正常执行（子代理默认路径）。"""
    registry = _StubRegistry([_StubTool("read_file", "original")])
    ctx = _make_ctx(None)

    results = await execute_tool_calls(registry, [_call("read_file", "c1")], ctx)
    assert _text(results[0]) == "original"


@pytest.mark.asyncio
async def test_subagent_preflighted_path_also_rewrites():
    """子代理路径（execute_preflighted_tool_calls）同样消费 hook 改写。"""
    recorder = _RecordingHandler(
        result=HookResult(updated_mcp_tool_output=SLIMMED_OUTPUT)
    )
    dispatcher = HookDispatcher()
    dispatcher.register(
        HookEvent.POST_TOOL,
        MatcherFilteredHandler(recorder, r"science-mining__fanout_search"),
    )

    registry = _StubRegistry([
        _StubTool("tech-mining.science-mining:fanout_search", LONG_OUTPUT),
    ])
    ctx = _make_ctx(dispatcher)

    results = await execute_preflighted_tool_calls(
        registry, [_call("tech-mining.science-mining__fanout_search", "c1")], ctx,
    )

    assert _text(results[0]) == SLIMMED_OUTPUT


@pytest.mark.asyncio
async def test_hook_dispatcher_survives_deepcopy_via_reference_refill():
    """sub_agent deepcopy：hook_dispatcher 字段被排除后由 tool.py 按引用回填。

    这里只验证契约的一半：ToolContext.__deepcopy__ 排除 hook_dispatcher
    （运行时引用不深拷贝），回填逻辑在 subagent/tool.py._run_single_agent。
    """
    dispatcher = HookDispatcher()
    ctx = _make_ctx(dispatcher)
    child = asyncio.get_event_loop().run_in_executor(
        None, lambda: __import__("copy").deepcopy(ctx)
    )
    copied = await child
    assert copied.hook_dispatcher is None  # deepcopy 排除
    copied.hook_dispatcher = ctx.hook_dispatcher  # tool.py 回填模式
    assert copied.hook_dispatcher is dispatcher


# ── 停机取消 vs 运行期取消 ────────────────────────────────────────────────


class _HangingHandler(HookHandler):
    """永不返回的 handler，用于验证取消语义。"""

    async def execute(self, context: HookContext) -> HookResult:
        await asyncio.sleep(60)
        return HookResult.success()


def _hook_context() -> HookContext:
    return HookContext(event=HookEvent.POST_TOOL.value, session_id="s1")


@pytest.mark.asyncio
async def test_hook_cancel_during_shutdown_is_absorbed():
    """停机取消是预期行为：记 CANCELLED，不上抛、不落 error 日志。"""
    from prismclaw.infra.lifecycle_state import mark_shutting_down, reset_for_tests

    dispatcher = HookDispatcher()
    dispatcher.register(HookEvent.POST_TOOL, _HangingHandler())
    mark_shutting_down()
    try:
        task = asyncio.create_task(
            dispatcher.dispatch(HookEvent.POST_TOOL, _hook_context())
        )
        await asyncio.sleep(0.05)  # 让 dispatch 真正进入 wait_for 的挂起态
        task.cancel()
        result = await task
    finally:
        reset_for_tests()

    assert result.outcome is HookOutcome.CANCELLED


@pytest.mark.asyncio
async def test_hook_cancel_outside_shutdown_propagates():
    """运行期取消（abort / 上层超时）必须穿透 hook 层，不能被吞成结果。"""
    from prismclaw.infra.lifecycle_state import reset_for_tests

    reset_for_tests()
    dispatcher = HookDispatcher()
    dispatcher.register(HookEvent.POST_TOOL, _HangingHandler())

    task = asyncio.create_task(
        dispatcher.dispatch(HookEvent.POST_TOOL, _hook_context())
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task