"""终态收尾契约：锚点取 order 中最后一条 assistant 消息，跳过其后的 tool entry。

正文只来自引擎落库的终态轮（无工具调用的收尾轮），Web 层不补写
（见 ``specs/body-terminal-turn-only/spec.md``）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from prismclaw.app.web.chat.chat_runner import _last_assistant_message_id
from prismclaw.schema.agent.session import MessageEntry, SessionHeader
from prismclaw.schema.session.types import SessionTree


class _MemStore:
    def __init__(self, tree: SessionTree | None) -> None:
        self.tree = tree

    async def load(self, _session_id: str) -> SessionTree | None:
        return self.tree


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
