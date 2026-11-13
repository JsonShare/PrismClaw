"""坏 JSON 参数修复契约（SDD §9.2）。

覆盖三类形态：截断补全、尾逗号、markdown fence。
并锁定两条不变量：
- ``to_openai_dict`` 永不产出非法 JSON（否则下一轮 provider 400）；
- ``_function_args`` 是执行层唯一解析点，失败只落到空 dict 一条路径。
"""
from __future__ import annotations

import json

from prismclaw.engine.execution.dispatch.execution_entry import (
    _function_args,
    repair_json_args,
)
from prismclaw.schema.llm.tool_call_types import ToolCallSlot


def _call(arguments: str) -> dict:
    return {"function": {"name": "write", "arguments": arguments}}


def test_repair_truncated_json() -> None:
    """流式截断：缺少收尾的 `"` 与 `}`。"""
    args = _function_args(_call('{"path": "a.txt", "content": "hello'))
    assert args == {"path": "a.txt", "content": "hello"}


def test_repair_trailing_comma() -> None:
    args = _function_args(_call('{"path": "a.txt", "content": "hi",}'))
    assert args == {"path": "a.txt", "content": "hi"}


def test_repair_markdown_fence() -> None:
    fenced = '```json\n{"path": "a.txt"}\n```'
    assert repair_json_args(fenced) == {"path": "a.txt"}
    assert _function_args(_call(fenced)) == {"path": "a.txt"}


def test_unrecoverable_returns_empty_dict() -> None:
    """失败路径只有一条：空 dict，交给工具自身校验。"""
    assert _function_args(_call("not json at all")) == {}
    assert _function_args(_call("")) == {}


def test_to_openai_dict_always_emits_valid_json() -> None:
    """非法负载不得原样写入 assistant.tool_calls（回放会 400）。"""
    slot = ToolCallSlot(index=0, name="write", arguments_str="not json at all")
    out = slot.to_openai_dict()
    parsed = json.loads(out["function"]["arguments"])
    assert parsed == {}


def test_to_openai_dict_uses_repaired_args() -> None:
    """历史消息与执行层看到同一份修复后的参数。"""
    slot = ToolCallSlot(index=0, name="write", arguments_str='{"path": "a.txt",}')
    out = slot.to_openai_dict()
    assert json.loads(out["function"]["arguments"]) == {"path": "a.txt"}
