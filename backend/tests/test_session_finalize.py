"""终态收尾契约：消息只经消息事件写入；锚点复用引擎已落的 assistant 消息。

对应 spec 的 I1 / I3 / I4：
- 中断补写必须是 ASSISTANT_MESSAGE 事件（投影会追加进 order）；
- 锚点取 order 中最后一条 assistant 消息，跳过其后的 tool entry。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from prismclaw.app.web.chat._chat_helpers import _persist_streamed_assistant_message
from prismclaw.app.web.chat.chat_runner import _last_assistant_message_id
from prismclaw.schema.agent.session import MessageEntry, SessionHeader
from prismclaw.schema.session import SessionEventType
from prismclaw.schema.session.types import SessionTree


class _MemStore:
    def __init__(self, tree: SessionTree | None) -> None:
        self.tree = tree
        self.committed: list = []

    async def load(self, _session_id: str) -> SessionTree | None:
        return self.tree

    async def commit_events(self, events: list) -> None:
        self.committed.extend(events)


def _tree(order: list[str], entries: dict[str, MessageEntry]) -> SessionTree:
    return SessionTree(
        header=SessionHeader(id="s1", workspace_id="w1", agent_id="a1"),
        entries=dict(entries),
        order=list(order),
        leaf_id=order[-1] if order else None,
    )


def _deps(store: _MemStore) -> SimpleNamespace:
    return SimpleNamespace(session_store=store)


def test_last_assistant_message_id_skips_trailing_tool_entry() -> None:
    entries = {
        "u1": MessageEntry(id="u1", parent_id=None, role="user", content="帮我看下"),
        "a1": MessageEntry(id="a1", parent_id="u1", role="assistant", content="好的"),
        "t1": MessageEntry(id="t1", parent_id="a1", role="tool", content="{}"),
    }
    deps = _deps(_MemStore(_tree(["u1", "a1", "t1"], entries)))
    assert asyncio.run(_last_assistant_message_id(deps, "s1")) == "a1"


def test_last_assistant_message_id_none_without_assistant() -> None:
    entries = {"u1": MessageEntry(id="u1", parent_id=None, role="user", content="hi")}
    deps = _deps(_MemStore(_tree(["u1"], entries)))
    assert asyncio.run(_last_assistant_message_id(deps, "s1")) is None


def test_streamed_message_is_written_as_message_event() -> None:
    """中断补写走 message 事件（进 order），并带 metadata.interrupted。"""
    store = _MemStore(_tree([], {}))
    new_id = asyncio.run(
        _persist_streamed_assistant_message(_deps(store), session_id="s1", text="已生成清单")
    )
    assert new_id
    message_events = [
        event for event in store.committed if event.type == SessionEventType.ASSISTANT_MESSAGE
    ]
    assert len(message_events) == 1
    data = message_events[0].data
    assert data["entry_id"] == new_id
    assert data["content"] == "已生成清单"
    assert data["metadata"]["interrupted"] is True


def test_streamed_message_skips_empty_text() -> None:
    store = _MemStore(_tree([], {}))
    assert (
        asyncio.run(
            _persist_streamed_assistant_message(_deps(store), session_id="s1", text="   ")
        )
        is None
    )
    assert store.committed == []
