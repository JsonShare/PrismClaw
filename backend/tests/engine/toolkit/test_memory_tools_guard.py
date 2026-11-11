"""T1-4 验收测试（SDD D8）：记忆写入守卫只认可「成功」的工具执行。"""

from __future__ import annotations

from types import SimpleNamespace

from prismclaw.engine.toolkit.memory.memory_tools import MemoryTool
from prismclaw.schema.agent.session import MessageEntry


def _tree(**entries: MessageEntry) -> SimpleNamespace:
    return SimpleNamespace(entries={entry.id: entry for entry in entries.values()})


def _tool(entry_id: str, *, is_error: bool) -> MessageEntry:
    return MessageEntry(
        id=entry_id,
        parent_id=None,
        type="message",
        role="tool",
        content="tool result",
        is_error=is_error,
    )


def test_error_only_tool_use_is_rejected() -> None:
    tree = _tree(t=_tool("t", is_error=True))
    assert MemoryTool._has_non_error_tool_use(tree) is False


def test_success_tool_use_is_accepted() -> None:
    tree = _tree(t=_tool("t", is_error=False))
    assert MemoryTool._has_non_error_tool_use(tree) is True


def test_mixed_tool_uses_require_at_least_one_success() -> None:
    tree = _tree(
        ok=_tool("ok", is_error=False),
        bad=_tool("bad", is_error=True),
    )
    assert MemoryTool._has_non_error_tool_use(tree) is True