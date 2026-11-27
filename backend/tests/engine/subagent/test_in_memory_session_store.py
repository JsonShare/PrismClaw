"""``InMemorySessionStore`` 必须能承载 ``SubagentPersistence`` 的完整凭证语义。"""

from __future__ import annotations

import pytest

from prismclaw.engine.subagent.core.persistence import SubagentPersistence
from prismclaw.persistence.session.in_memory import InMemorySessionStore
from prismclaw.schema.session.types import MessageEntry


@pytest.mark.asyncio
async def test_create_then_commit_messages_rebuilds_paired_conversation() -> None:
    store = InMemorySessionStore()
    persistence = await SubagentPersistence.create(
        store,
        parent_session_id="parent-1",
        agent_id="core-agent",
        workspace_id="ws-1",
        task="总结这份文档",
    )

    await persistence.append_user_message("补充：只要结论")
    await persistence.append_assistant_message(
        "正在读取",
        agent_id="core-agent",
        run_id="run-1",
        attempt=0,
        tool_calls=[{"id": "call-1", "type": "function", "function": {"name": "read", "arguments": "{}"}}],
    )
    await persistence.append_message(
        MessageEntry(
            id="tool-1",
            parent_id=persistence.tree.leaf_id,
            role="tool",
            content="ok",
            tool_call_id="call-1",
            tool_name="read",
        )
    )

    messages = persistence.rebuild_model_messages()

    assert [message["role"] for message in messages] == ["user", "user", "assistant", "tool"]
    assert messages[-1]["tool_call_id"] == "call-1"
    # seq 单调，且每提交一次 = 一条事件
    event_count = await store.get_event_count(persistence.child_session_id)
    assert event_count >= 4
    assert (await store.load(persistence.child_session_id)) is not None


@pytest.mark.asyncio
async def test_inherit_mode_requires_parent_tree_in_the_same_store() -> None:
    store = InMemorySessionStore()
    with pytest.raises(ValueError, match="parent session"):
        await SubagentPersistence.create(
            store,
            parent_session_id="missing-parent",
            agent_id="core-agent",
            workspace_id="ws-1",
            task="t",
            context_mode="inherit",
        )
