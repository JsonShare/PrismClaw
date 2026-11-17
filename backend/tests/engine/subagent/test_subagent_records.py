from __future__ import annotations

from typing import Any

import pytest
from tests.engine.session_store import FakeRedisSessionStore, _FakeRedisClient

from prismclaw.engine.subagent.core.persistence import SubagentPersistence
from prismclaw.engine.subagent.runtime.records import (
    AgentRunRecord,
    AgentSessionRecord,
    SessionSubagentRecordStore,
)
from prismclaw.schema.session.event_logger import SessionEventLogger
from prismclaw.schema.session.types import (
    SUBAGENT_TOOL_EFFECT_CUSTOM_TYPE,
    CustomEntry,
    MessageEntry,
    SessionEntry,
    SessionEvent,
    SessionEventType,
    SessionTree,
)


class _RedisClient(_FakeRedisClient):
    async def incr(self, key: str) -> int:
        value = int(self._kv.get(key, 0)) + 1
        self._kv[key] = value
        return value

    async def hset(
        self,
        key: str,
        field: str | None = None,
        value: Any = None,
        *,
        mapping: dict[str, Any] | None = None,
    ) -> int:
        if mapping is None:
            assert field is not None
            return await super().hset(key, field, value)
        bucket = self._hashes.setdefault(key, {})
        bucket.update(mapping)
        return len(mapping)

    async def rpush(self, key: str, *values: Any) -> int:
        bucket = self._lists.setdefault(key, [])
        bucket.extend(values)
        return len(bucket)

    async def zrem(self, key: str, *members: str) -> int:
        bucket = self._zsets.setdefault(key, {})
        removed = 0
        for member in members:
            removed += int(bucket.pop(member, None) is not None)
        return removed


def _store() -> FakeRedisSessionStore:
    store = FakeRedisSessionStore()
    store._client = _RedisClient()
    return store


async def _append_message(
    store: FakeRedisSessionStore,
    session_id: str,
    entry: MessageEntry,
) -> MessageEntry:
    tree = await store.load(session_id)
    assert tree is not None
    entry.parent_id = tree.leaf_id
    event = SessionEventLogger().message_entry(session_id, entry, entry.id)[0]
    committed = await store.commit_events([event])
    entry.parent_id = committed[0].data.get("parent_id")
    entry.seq = committed[0].seq
    return entry


def _agent_session(**updates: Any) -> AgentSessionRecord:
    record = AgentSessionRecord(
        agent_id="ag:one",
        owner_session_id="",
        child_session_id="child-one",
        agent_name="explorer",
        agent_source="builtin",
        lifecycle="persistent",
    )
    return record.model_copy(update=updates)


def _agent_run(run_id: str, **updates: Any) -> AgentRunRecord:
    record = AgentRunRecord(
        run_id=run_id,
        agent_id="ag:one",
        owner_session_id="",
        child_session_id="child-one",
        owner_worker_id="worker-one",
    )
    return record.model_copy(update=updates)


@pytest.mark.asyncio
async def test_message_metadata_round_trip_for_all_roles() -> None:
    store = _store()
    tree = await store.create_new_session("metadata", "ws", "main")
    session_id = tree.header.id
    metadata = [
        {"message_id": "msg:1", "request_id": "req:1", "source": "parent"},
        {"agent_id": "ag:one", "run_id": "ar:one", "attempt": 1, "ack_message_ids": ["msg:1"]},
        {"agent_id": "ag:one", "run_id": "ar:one", "attempt": 1, "tool_call_id": "call:1"},
    ]
    entries = [
        MessageEntry(id="user", parent_id=None, role="user", content="task", metadata=metadata[0]),
        MessageEntry(
            id="assistant",
            parent_id="user",
            role="assistant",
            content="working",
            tool_calls=[{"id": "call:1", "type": "function", "function": {"name": "read", "arguments": "{}"}}],
            metadata=metadata[1],
        ),
        MessageEntry(
            id="tool",
            parent_id="assistant",
            role="tool",
            content="result",
            tool_call_id="call:1",
            metadata=metadata[2],
        ),
    ]
    logger = SessionEventLogger()
    events = [
        logger.message_entry(session_id, entry, entries[-1].id)[0]
        for entry in entries
    ]
    await store.commit_events(events)

    loaded = await store.load(session_id)
    assert loaded is not None
    assert [loaded.entries[entry.id].metadata for entry in entries] == metadata

    rebuilt = SessionTree.from_events(await store.get_events(session_id))
    assert [rebuilt.entries[entry.id].metadata for entry in entries] == metadata
    assert rebuilt.entries["assistant"].tool_calls == entries[1].tool_calls

    legacy_cases = [
        (SessionEventType.USER_MESSAGE, "user/message", "user"),
        (SessionEventType.ASSISTANT_MESSAGE, "assistant/message", "assistant"),
        (SessionEventType.TOOL_RESULT, "tool/result", "tool"),
    ]
    for event_type, data_type, role in legacy_cases:
        legacy = SessionTree.from_events(
            [
                SessionEvent(
                    seq=1,
                    type=event_type,
                    session_id="legacy",
                    data={"type": data_type, "entry_id": role, "content": "old"},
                )
            ]
        )
        assert legacy.entries[role].metadata is None


@pytest.mark.asyncio
async def test_agent_and_run_records_fold_by_fence_then_event_seq() -> None:
    store = _store()
    tree = await store.create_new_session("records", "ws", "main")
    owner_session_id = tree.header.id
    records = SessionSubagentRecordStore(store)

    await records.append(
        _agent_session(
            owner_session_id=owner_session_id,
            status="running",
            active_run_id="ar:old",
            current_fence=1,
        )
    )
    await records.append(
        _agent_session(
            owner_session_id=owner_session_id,
            status="running",
            active_run_id="ar:new",
            current_fence=3,
        )
    )
    await records.append(
        _agent_session(
            owner_session_id=owner_session_id,
            status="closed",
            current_fence=2,
        )
    )
    current = await records.get_session(session_id=owner_session_id, agent_id="ag:one")
    assert current is not None
    assert (current.current_fence, current.active_run_id) == (3, "ar:new")

    await records.append(
        _agent_session(
            owner_session_id=owner_session_id,
            status="idle",
            active_run_id=None,
            current_fence=3,
        )
    )
    current = await records.get_session(session_id=owner_session_id, agent_id="ag:one")
    assert current is not None
    assert (current.current_fence, current.status) == (3, "idle")

    await records.append(_agent_run("ar:old", owner_session_id=owner_session_id, fence=1))
    await records.append(
        _agent_run("ar:new", owner_session_id=owner_session_id, fence=2, status="running")
    )
    await records.append(
        _agent_run("ar:old", owner_session_id=owner_session_id, fence=4, status="running")
    )
    await records.append(
        _agent_run("ar:old", owner_session_id=owner_session_id, fence=3, status="succeeded")
    )
    old_run = await records.get_run(session_id=owner_session_id, run_id="ar:old")
    assert old_run is not None
    assert (old_run.fence, old_run.status) == (4, "running")

    await records.append(
        _agent_run("ar:new", owner_session_id=owner_session_id, fence=2, status="succeeded")
    )
    runs = await records.list_runs(session_id=owner_session_id)
    assert {record.run_id for record in runs} == {"ar:old", "ar:new"}
    assert {record.run_id: record.status for record in runs} == {
        "ar:old": "running",
        "ar:new": "succeeded",
    }


@pytest.mark.asyncio
async def test_child_current_history_isolation_and_inherit_only_once() -> None:
    store = _store()
    old_parent = await store.create_new_session("parent", "ws", "main")
    old_entry = await _append_message(
        store,
        old_parent.header.id,
        MessageEntry(id="old-user", parent_id=None, role="user", content="old parent"),
    )
    current_parent = await store.create_new_session("parent", "ws", "main")
    await _append_message(
        store,
        current_parent.header.id,
        MessageEntry(id="new-user", parent_id=None, role="user", content="new parent"),
    )

    child = await SubagentPersistence.create(
        store,
        parent_session_id=old_parent.header.id,
        agent_id="ag:inherit",
        workspace_id="ws",
        task="child task",
        context_mode="inherit",
        request_id="req:spawn",
        run_id="ar:spawn",
    )
    child_key = f"subagent:{old_parent.header.id}:ag:inherit"
    assert await store.get_current_session_id("parent") == current_parent.header.id
    assert await store.get_current_session_id(child_key) == child.child_session_id
    assert child.tree.header.parent_session == old_parent.header.id
    parent_history = await store.list_session_history("parent")
    assert {item.session_id for item in parent_history} == {
        old_parent.header.id,
        current_parent.header.id,
    }
    assert child.child_session_id not in {item.session_id for item in parent_history}

    contents = [str(message["content"]) for message in child.rebuild_model_messages()]
    assert "old parent" in contents
    assert all("new parent" not in content for content in contents)
    inherited = [
        entry
        for entry in child.tree.get_branch()
        if isinstance(entry, MessageEntry) and (entry.metadata or {}).get("kind") == "inherited"
    ]
    assert len(inherited) == 1
    assert inherited[0].metadata == {"kind": "inherited", "source_entry_id": old_entry.id}

    await _append_message(
        store,
        old_parent.header.id,
        MessageEntry(id="late-user", parent_id=None, role="user", content="late parent"),
    )
    cold = await SubagentPersistence.load(store, child.child_session_id)
    assert cold is not None
    cold_contents = [str(message["content"]) for message in cold.rebuild_model_messages()]
    assert all("late parent" not in content for content in cold_contents)
    assert len(cold_contents) == len(contents)
    assert len(cold.get_unacked_user_messages()) == 1

    isolated = await SubagentPersistence.create(
        store,
        parent_session_id=current_parent.header.id,
        agent_id="ag:isolated",
        workspace_id="ws",
        task="isolated task",
        context_mode="isolated",
        context_snapshot={"task_goal": "structured goal", "constraints": ["keep isolated"]},
    )
    isolated_contents = [str(message["content"]) for message in isolated.rebuild_model_messages()]
    assert len(isolated_contents) == 1
    assert "structured goal" in isolated_contents[0]
    assert all("new parent" not in content for content in isolated_contents)


@pytest.mark.asyncio
async def test_unacked_messages_tool_effects_and_cold_rebuild() -> None:
    store = _store()
    child = await SubagentPersistence.create(
        store,
        parent_session_id="parent-external",
        agent_id="ag:one",
        workspace_id="ws",
        task="initial task",
        request_id="req:initial",
        run_id="ar:one",
    )
    initial = child.get_unacked_user_messages()[0]
    initial_message_id = initial.metadata["message_id"]
    follow_up = await child.append_user_message(
        "follow up",
        message_id="msg:follow",
        request_id="req:follow",
        run_id="ar:one",
    )
    await child.append_assistant_message(
        "first answer",
        agent_id="ag:one",
        run_id="ar:one",
        attempt=1,
        ack_message_ids=[initial_message_id],
    )
    assert [entry.id for entry in child.get_unacked_user_messages()] == [follow_up.id]

    await child.append_tool_effect(
        {
            "tool_call_id": "call:one",
            "run_id": "ar:one",
            "fence": 1,
            "effect_class": "pure_read",
            "commit_state": "in_progress",
        }
    )
    await child.append_tool_message(
        "tool result",
        agent_id="ag:one",
        run_id="ar:one",
        attempt=1,
        tool_call_id="call:one",
        tool_name="read",
    )
    await child.append_tool_effect(
        {
            "tool_call_id": "call:one",
            "run_id": "ar:one",
            "fence": 1,
            "effect_class": "pure_read",
            "commit_state": "committed",
            "result_persisted": True,
        }
    )
    await child.append_assistant_message(
        "done",
        agent_id="ag:one",
        run_id="ar:one",
        attempt=1,
        ack_message_ids=["msg:follow"],
    )

    cold = await SubagentPersistence.load(store, child.child_session_id)
    assert cold is not None
    assert cold.get_unacked_user_messages() == []
    assert [effect["commit_state"] for effect in cold.get_tool_effects()] == [
        "in_progress",
        "committed",
    ]
    assert [message["role"] for message in cold.rebuild_model_messages()] == [
        "user",
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    branch = cold.tree.get_branch()
    assert cold.tree.leaf_id == branch[-1].id
    assert all(
        child_entry.parent_id == parent.id
        for parent, child_entry in zip(branch, branch[1:], strict=False)
    )
    effect_entries = [
        entry
        for entry in branch
        if isinstance(entry, CustomEntry) and entry.custom_type == SUBAGENT_TOOL_EFFECT_CUSTOM_TYPE
    ]
    assert len(effect_entries) == 2
    assert effect_entries[-1].get_typed_data()["result_persisted"] is True


@pytest.mark.asyncio
async def test_custom_subagent_payloads_accept_generic_dicts() -> None:
    entries: list[SessionEntry] = [
        CustomEntry(id="session", parent_id=None, custom_type="subagent_session", data={"agent_id": "ag:one"}),
        CustomEntry(id="run", parent_id="session", custom_type="subagent_run", data={"run_id": "ar:one"}),
        CustomEntry(id="effect", parent_id="run", custom_type="subagent_tool_effect", data={"state": "committed"}),
    ]
    assert [entry.get_typed_data() for entry in entries] == [
        {"agent_id": "ag:one"},
        {"run_id": "ar:one"},
        {"state": "committed"},
    ]


@pytest.mark.asyncio
async def test_inherited_context_drops_parent_tool_calls() -> None:
    """继承父会话上下文时不能保留孤立的 tool_calls。"""
    store = _store()
    parent = await store.create_new_session("parent-inherit", "ws", "main")
    await _append_message(
        store,
        parent.header.id,
        MessageEntry(
            id="parent-assistant",
            parent_id=None,
            role="assistant",
            content="parent did work",
            tool_calls=[
                {
                    "id": "call:p",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                }
            ],
            metadata={"agent_id": "ag:parent", "run_id": "ar:parent"},
        ),
    )

    child = await SubagentPersistence.create(
        store,
        parent_session_id=parent.header.id,
        agent_id="ag:inherit",
        workspace_id="ws",
        task="inherit",
        context_mode="inherit",
    )

    inherited = [
        entry
        for entry in child.tree.get_branch()
        if isinstance(entry, MessageEntry)
        and (entry.metadata or {}).get("kind") == "inherited"
    ]
    assert len(inherited) == 1
    assert inherited[0].content == "parent did work"
    assert inherited[0].tool_calls is None
    assert inherited[0].tool_call_id is None


def test_allowed_tools_expand_server_prefix_wildcard():
    """``server:*`` 展开为具体 MCP 工具名，其余条目原样保留。"""
    from prismclaw.engine.subagent.prompting import expand_allowed_tool_names

    available = {"read", "write", "yuandian:search", "yuandian:get", "Slack:post"}
    assert expand_allowed_tool_names(
        ["read", "yuandian:*"], available
    ) == {"read", "yuandian:search", "yuandian:get"}
    # 未匹配到任何工具的前缀不应退化成「全部工具」
    assert expand_allowed_tool_names(["nope:*"], available) == set()
    assert expand_allowed_tool_names(["read"], available) == {"read"}


def test_allowed_tools_wildcard_keeps_unrestricted_semantics_separate():
    """``None`` = 不限工具；``[]`` = 无工具。两者不能混为一谈。"""
    from prismclaw.engine.subagent.prompting import expand_allowed_tool_names

    available = {"read", "bash"}
    assert expand_allowed_tool_names(None, available) is None
    assert expand_allowed_tool_names([], available) == set()
