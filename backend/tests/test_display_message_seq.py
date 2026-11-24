"""展示消息 seq 的单测。

前端做增量对账时，一条展示消息可能由多条 entry 聚合而成（assistant 的
多轮迭代 + 其工具结果），所以展示 seq 必须是组内最大值——取错会让
"后到的工具结果/更新"被客户端 last_seq 永久跳过。
"""

from __future__ import annotations

from prismclaw.app.web.routers._message_helpers import _build_display_messages
from prismclaw.schema.agent.session import SessionHeader
from prismclaw.schema.session.types import MessageEntry, SessionTree


def _tree(entries: list[MessageEntry]) -> SessionTree:
    return SessionTree(
        header=SessionHeader(id="s1", workspace_id="w1", agent_id="a1"),
        entries={entry.id: entry for entry in entries},
        order=[entry.id for entry in entries],
        leaf_id=entries[-1].id if entries else None,
    )


def _user(entry_id: str, seq: int, content: str = "hi") -> MessageEntry:
    return MessageEntry(id=entry_id, parent_id=None, role="user", content=content, seq=seq)


def _assistant(
    entry_id: str, seq: int, content: str, tool_calls: list[dict] | None = None
) -> MessageEntry:
    return MessageEntry(
        id=entry_id,
        parent_id=None,
        role="assistant",
        content=content,
        seq=seq,
        tool_calls=tool_calls,
    )


def _tool(entry_id: str, seq: int, tool_call_id: str = "tc1") -> MessageEntry:
    return MessageEntry(
        id=entry_id,
        parent_id=None,
        role="tool",
        content="ok",
        seq=seq,
        tool_call_id=tool_call_id,
        tool_name="read_file",
    )


def test_assistant_group_takes_max_seq() -> None:
    tree = _tree(
        [
            _user("u1", 1),
            _assistant("a1", 2, "第一步"),
            _assistant("a2", 5, "完成"),
        ]
    )
    messages = _build_display_messages(tree)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["seq"] == 5


def test_tool_entry_seq_participates_in_group_max() -> None:
    tree = _tree(
        [
            _user("u1", 1),
            _assistant("a1", 3, "调用工具", [{"id": "tc1", "function": {"name": "read_file"}}]),
            _tool("t1", 9),
        ]
    )
    messages = _build_display_messages(tree)
    assert messages[1]["seq"] == 9


def test_user_message_uses_own_seq() -> None:
    tree = _tree([_user("u1", 7), _assistant("a1", 8, "答")])
    messages = _build_display_messages(tree)
    assert messages[0]["seq"] == 7


def test_group_seq_resets_between_turns() -> None:
    tree = _tree(
        [
            _user("u1", 1),
            _assistant("a1", 5, "第一轮"),
            _user("u2", 8),
            _assistant("a2", 9, "第二轮"),
        ]
    )
    messages = _build_display_messages(tree)
    assert [m["seq"] for m in messages] == [1, 5, 8, 9]
