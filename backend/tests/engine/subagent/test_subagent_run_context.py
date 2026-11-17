from __future__ import annotations

import pytest

from prismclaw.engine.execution.checkpoint.models import CommitState
from prismclaw.engine.subagent.core.persistence import SubagentPersistence
from prismclaw.engine.subagent.core.run_context import (
    SubagentCommitUnknownError,
    SubagentRunContext,
)
from prismclaw.schema.content.content_types import TextBlock, ToolResult
from prismclaw.schema.session.types import CustomEntry, MessageEntry
from tests.engine.subagent.test_loop_characterization import (
    _run,
    _tc,
    LoopHarness,
    MockLLM,
    MockResponse,
    MockTool,
    MockToolRegistry,
)
from tests.engine.subagent.test_subagent_records import _store


async def _persistence(store, *, task: str = "initial task") -> SubagentPersistence:
    parent = await store.create_new_session("parent", "ws", "main")
    return await SubagentPersistence.create(
        store,
        parent_session_id=parent.header.id,
        agent_id="ag:run-context",
        workspace_id="ws",
        task=task,
        request_id="req:initial",
        run_id="ar:run-context",
    )


def _run_context(persistence: SubagentPersistence) -> SubagentRunContext:
    return SubagentRunContext(
        agent_id="ag:run-context",
        run_id="ar:run-context",
        persistence=persistence,
        lifecycle="persistent",
    )


def _harness(llm: MockLLM, tools: MockToolRegistry, run_context: SubagentRunContext) -> LoopHarness:
    harness = LoopHarness(llm, tools)
    harness._run_context = run_context
    harness._run_id = run_context.run_id
    return harness


@pytest.mark.asyncio
async def test_steering_is_injected_once_and_acknowledged() -> None:
    store = _store()
    persistence = await _persistence(store)
    run_context = _run_context(persistence)

    class SteeringTool(MockTool):
        async def execute(self, args, context) -> ToolResult:
            loaded = await SubagentPersistence.load(store, persistence.child_session_id)
            assert loaded is not None
            await loaded.append_user_message(
                "new constraint",
                message_id="msg:steering",
                request_id="req:steering",
            )
            return ToolResult(
                tool_call_id="tc1",
                content=[TextBlock(text="tool done")],
            )

    llm = MockLLM([
        MockResponse(tool_calls=[_tc("tc1")]),
        MockResponse(text="done"),
    ])
    harness = _harness(
        llm,
        MockToolRegistry([SteeringTool("noop")]),
        run_context,
    )

    outcome, _turns, _usage = await _run(
        harness,
        messages=run_context.build_messages(),
        llm=llm,
        tools=harness._tools,
    )

    assert outcome == "success"
    second_call_contents = [
        str(message.get("content")) for message in llm.calls[1]["messages"]
    ]
    assert sum("new constraint" in content for content in second_call_contents) == 1
    assert all(
        sum("new constraint" in str(message.get("content")) for message in call["messages"]) <= 1
        for call in llm.calls
    )

    cold = await SubagentPersistence.load(store, persistence.child_session_id)
    assert cold is not None
    assistants = [
        entry
        for entry in cold.tree.get_branch()
        if isinstance(entry, MessageEntry) and entry.role == "assistant"
    ]
    assert len(assistants) == 2
    assert assistants[0].metadata["ack_message_ids"]
    assert assistants[1].metadata["ack_message_ids"] == ["msg:steering"]
    assert cold.get_unacked_user_messages() == []


@pytest.mark.asyncio
async def test_tool_result_and_committed_effect_share_a_batch() -> None:
    store = _store()
    persistence = await _persistence(store)
    run_context = _run_context(persistence)
    llm = MockLLM([
        MockResponse(tool_calls=[_tc("tc1")]),
        MockResponse(text="done"),
    ])
    harness = _harness(
        llm,
        MockToolRegistry([MockTool("noop")]),
        run_context,
    )

    outcome, _turns, _usage = await _run(
        harness,
        messages=run_context.build_messages(),
        llm=llm,
        tools=harness._tools,
    )

    assert outcome == "success"
    cold = await SubagentPersistence.load(store, persistence.child_session_id)
    assert cold is not None
    effects = cold.get_tool_effects()
    assert [effect["commit_state"] for effect in effects] == [
        CommitState.IN_PROGRESS.value,
        CommitState.COMMITTED.value,
    ]
    assert effects[-1]["result_persisted"] is True
    assert effects[-1]["run_id"] == run_context.run_id
    tool_messages = [
        entry
        for entry in cold.tree.get_branch()
        if isinstance(entry, MessageEntry) and entry.role == "tool"
    ]
    assert len(tool_messages) == 1
    assert tool_messages[0].metadata["tool_call_id"] == "tc1"
    assert [message["role"] for message in cold.rebuild_model_messages()] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]


@pytest.mark.asyncio
async def test_tool_result_commit_failure_records_commit_unknown(monkeypatch) -> None:
    store = _store()
    persistence = await _persistence(store)
    run_context = _run_context(persistence)
    original_commit = persistence.commit_entries

    async def fail_result_batch(entries):
        if any(
            isinstance(entry, MessageEntry) and entry.role == "tool"
            for entry in entries
        ) and any(isinstance(entry, CustomEntry) for entry in entries):
            raise RuntimeError("simulated lost commit response")
        return await original_commit(entries)

    monkeypatch.setattr(persistence, "commit_entries", fail_result_batch)
    llm = MockLLM([MockResponse(tool_calls=[_tc("tc1")])])
    harness = _harness(
        llm,
        MockToolRegistry([MockTool("noop")]),
        run_context,
    )

    with pytest.raises(SubagentCommitUnknownError):
        await _run(
            harness,
            messages=run_context.build_messages(),
            llm=llm,
            tools=harness._tools,
        )

    assert run_context.recovery_blocked_reason is not None
    cold = await SubagentPersistence.load(store, persistence.child_session_id)
    assert cold is not None
    assert cold.get_tool_effects()[-1]["commit_state"] == CommitState.COMMIT_UNKNOWN.value
