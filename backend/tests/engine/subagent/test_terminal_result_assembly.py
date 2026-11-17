"""子代理非自然终止时的工具结果装配单元测试。

覆盖纯函数：
  - _synthesize_tool_results: 顺序保持、总量预算、单条超长截断、空历史
  - _build_tool_result_map: tool_call_id 与工具名关联
"""

from __future__ import annotations

from prismclaw.engine.subagent.core._lifecycle_mixin import (
    _SYNTHESIZED_RESULT_BUDGET,
    _build_tool_result_map,
    _synthesize_tool_results,
)


def test_synthesize_preserves_tool_call_order() -> None:
    """工具调用顺序保持：search → read → bash，输出标记顺序一致。"""
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "tc-1", "function": {"name": "search", "arguments": "{}"}},
                {"id": "tc-2", "function": {"name": "read", "arguments": "{}"}},
                {"id": "tc-3", "function": {"name": "bash", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "tc-1", "content": "搜索结果"},
        {"role": "tool", "tool_call_id": "tc-2", "content": "文件内容"},
        {"role": "tool", "tool_call_id": "tc-3", "content": "命令输出"},
    ]

    text = _synthesize_tool_results(messages)

    idx_search = text.index("[工具: search")
    idx_read = text.index("[工具: read")
    idx_bash = text.index("[工具: bash")
    assert idx_search < idx_read < idx_bash


def test_synthesize_enforces_total_budget() -> None:
    """总量预算：20 条 1000 字符结果被截断并追加省略标注。"""
    tool_calls: list[dict] = []
    tool_messages: list[dict] = []
    for i in range(20):
        tc_id = f"tc-{i}"
        tool_calls.append({"id": tc_id, "function": {"name": "bash", "arguments": "{}"}})
        tool_messages.append({"role": "tool", "tool_call_id": tc_id, "content": "x" * 1000})
    messages = [
        {"role": "assistant", "tool_calls": tool_calls},
        *tool_messages,
    ]

    text = _synthesize_tool_results(messages)

    assert len(text) <= _SYNTHESIZED_RESULT_BUDGET + 50
    assert "[已省略" in text


def test_synthesize_truncates_single_long_result() -> None:
    """单条超长截断：2000 字符结果保留头尾并插入截断标记。"""
    messages = [
        {
            "role": "assistant",
            "tool_calls": [{"id": "tc-1", "function": {"name": "read", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "tc-1", "content": "a" * 2000},
    ]

    text = _synthesize_tool_results(messages)

    assert "...[截断]..." in text


def test_synthesize_empty_history_returns_empty_string() -> None:
    """空历史：无工具消息时返回空字符串。"""
    assert _synthesize_tool_results([]) == ""


def test_build_tool_result_map_links_call_id_to_name() -> None:
    """结果映射：tool_call_id 正确关联到 read / bash 及其内容。"""
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "tc-1", "function": {"name": "read", "arguments": "{}"}},
                {"id": "tc-2", "function": {"name": "bash", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "tc-1", "content": "读到的内容"},
        {"role": "tool", "tool_call_id": "tc-2", "content": "命令输出"},
    ]

    result_map = _build_tool_result_map(messages)

    assert result_map["tc-1"] == ("read", "读到的内容")
    assert result_map["tc-2"] == ("bash", "命令输出")
