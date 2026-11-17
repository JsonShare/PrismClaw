from __future__ import annotations

from typing import Any

import pytest
from tests.engine.session_store import FakeRedisSessionStore
from tests.engine.subagent.test_subagent_records import _store

from prismclaw.engine.execution.checkpoint.models import CommitState
from prismclaw.engine.subagent.core.persistence import SubagentPersistence
from prismclaw.engine.subagent.runtime.recovery import assess_and_repair_recovery
from prismclaw.schema.agent.capability import EffectClass
from prismclaw.schema.session.types import CustomEntry, MessageEntry

_AGENT_ID = "ag:recovery"
_RUN_ID = "ar:recovery"


async def _persistence(store: FakeRedisSessionStore) -> SubagentPersistence:
    return await SubagentPersistence.create(
        store,
        parent_session_id="parent-external",
        agent_id=_AGENT_ID,
        workspace_id="ws",
        task="recover this run",
        request_id="req:recovery",
        run_id=_RUN_ID,
    )


def _call(call_id: str, tool_name: str = "read") -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": tool_name, "arguments": "{}"},
    }


async def _assistant(
    persistence: SubagentPersistence,
    *call_ids: str,
    tool_name: str = "read",
) -> MessageEntry:
    return await persistence.append_assistant_message(
        "working",
        agent_id=_AGENT_ID,
        run_id=_RUN_ID,
        attempt=1,
        ack_message_ids=[],
        tool_calls=[_call(call_id, tool_name) for call_id in call_ids],
    )


async def _effect(
    persistence: SubagentPersistence,
    call_id: str,
    *,
    commit_state: CommitState,
    effect_class: EffectClass,
    fence: int = 1,
    tool_name: str = "read",
) -> CustomEntry:
    return await persistence.append_tool_effect(
        {
            "tool_call_id": call_id,
            "tool_name": tool_name,
            "effect_class": effect_class.value,
            "idempotency_key": call_id,
            "approval_id": None,
            "commit_state": commit_state.value,
            "result_persisted": False,
            "started_at_epoch_ms": 100,
            "finished_at_epoch_ms": None,
            "effect_id": f"fx:{call_id}",
            "agent_id": _AGENT_ID,
            "run_id": _RUN_ID,
            "attempt": 1,
            "fence": fence,
            "arguments_summary": "{}",
            "tool_is_error": False,
        }
    )


def _tool_messages(persistence: SubagentPersistence) -> list[MessageEntry]:
    return [
        entry
        for entry in persistence.tree.get_branch()
        if isinstance(entry, MessageEntry) and entry.role == "tool"
    ]


def _latest_effect(persistence: SubagentPersistence, call_id: str) -> dict[str, Any]:
    matches = [
        effect
        for effect in persistence.get_tool_effects()
        if effect.get("tool_call_id") == call_id
    ]
    assert matches
    return matches[-1]


@pytest.mark.asyncio
async def test_before_dispatch_repairs_all_calls_in_one_batch(monkeypatch) -> None:
    store = _store()
    persistence = await _persistence(store)
    await _assistant(persistence, "call:one", "call:two")
    original_commit = persistence.commit_entries
    batches: list[list[MessageEntry | CustomEntry]] = []

    async def track_commit(
        entries: list[MessageEntry | CustomEntry],
    ) -> list[MessageEntry | CustomEntry]:
        batches.append(list(entries))
        return await original_commit(entries)

    monkeypatch.setattr(persistence, "commit_entries", track_commit)
    assessment = await assess_and_repair_recovery(persistence)

    assert assessment.blocked is False
    assert assessment.reasons == []
    assert assessment.repaired_tool_call_ids == ["call:one", "call:two"]
    assert len(batches) == 1
    assert len(batches[0]) == 4

    tool_messages = _tool_messages(persistence)
    assert [message.tool_call_id for message in tool_messages] == ["call:one", "call:two"]
    assert all(message.is_error for message in tool_messages)
    assert all((message.metadata or {}).get("synthetic") is True for message in tool_messages)
    for message in tool_messages:
        content = str(message.content)
        assert "interrupted before dispatch" in content
        assert "not executed" in content
        assert "not be replayed" in content

    for call_id in ("call:one", "call:two"):
        effect = _latest_effect(persistence, call_id)
        assert effect["commit_state"] == CommitState.ROLLED_BACK.value
        assert effect["result_persisted"] is True
        assert effect["original_call_executed"] is False

    branch = persistence.tree.get_branch()
    assert persistence.tree.leaf_id == branch[-1].id
    assert all(
        child.parent_id == parent.id
        for parent, child in zip(branch, branch[1:], strict=False)
    )
    assert [message["role"] for message in persistence.rebuild_model_messages()] == [
        "user",
        "assistant",
        "tool",
        "tool",
    ]


@pytest.mark.asyncio
async def test_in_progress_pure_read_is_closed_without_replay() -> None:
    store = _store()
    persistence = await _persistence(store)
    await _assistant(persistence, "call:read")
    await _effect(
        persistence,
        "call:read",
        commit_state=CommitState.IN_PROGRESS,
        effect_class=EffectClass.PURE_READ,
        fence=9,
    )

    assessment = await assess_and_repair_recovery(persistence)

    assert assessment.blocked is False
    assert assessment.repaired_tool_call_ids == ["call:read"]
    message = _tool_messages(persistence)[0]
    assert message.is_error is True
    assert "read-only" in str(message.content)
    assert "not replayed" in str(message.content)
    effect = _latest_effect(persistence, "call:read")
    assert effect["effect_id"] == "fx:call:read"
    assert effect["effect_class"] == EffectClass.PURE_READ.value
    assert effect["fence"] == 9
    assert effect["commit_state"] == CommitState.ROLLED_BACK.value
    assert effect["result_persisted"] is True
    assert effect["original_call_replayed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "effect_class",
    [
        EffectClass.LOCAL_MUTATION,
        EffectClass.EXTERNAL_MUTATION,
        EffectClass.UNKNOWN_EFFECT,
    ],
)
async def test_in_progress_dangerous_calls_block_without_rewrite(
    effect_class: EffectClass,
) -> None:
    store = _store()
    persistence = await _persistence(store)
    await _assistant(persistence, "call:danger", tool_name="mutate")
    await _effect(
        persistence,
        "call:danger",
        commit_state=CommitState.IN_PROGRESS,
        effect_class=effect_class,
        fence=2,
        tool_name="mutate",
    )
    before_ids = list(persistence.tree.entries)

    assessment = await assess_and_repair_recovery(persistence)

    assert assessment.blocked is True
    assert assessment.repaired_tool_call_ids == []
    assert "call:danger" in assessment.reasons[0]
    assert effect_class.value in assessment.reasons[0]
    assert list(persistence.tree.entries) == before_ids
    assert _tool_messages(persistence) == []
    assert _latest_effect(persistence, "call:danger")["commit_state"] == (
        CommitState.IN_PROGRESS.value
    )


@pytest.mark.asyncio
async def test_commit_unknown_blocks_even_for_pure_read() -> None:
    store = _store()
    persistence = await _persistence(store)
    await _assistant(persistence, "call:unknown")
    await _effect(
        persistence,
        "call:unknown",
        commit_state=CommitState.COMMIT_UNKNOWN,
        effect_class=EffectClass.PURE_READ,
    )
    before_ids = list(persistence.tree.entries)

    assessment = await assess_and_repair_recovery(persistence)

    assert assessment.blocked is True
    assert "unknown commit outcome" in assessment.reasons[0]
    assert list(persistence.tree.entries) == before_ids
    assert _tool_messages(persistence) == []


@pytest.mark.asyncio
async def test_committed_result_is_rebuilt_without_writes() -> None:
    store = _store()
    persistence = await _persistence(store)
    await _assistant(persistence, "call:done")
    await persistence.append_tool_message(
        "actual result",
        agent_id=_AGENT_ID,
        run_id=_RUN_ID,
        attempt=1,
        tool_call_id="call:done",
        tool_name="read",
    )
    await _effect(
        persistence,
        "call:done",
        commit_state=CommitState.COMMITTED,
        effect_class=EffectClass.EXTERNAL_MUTATION,
    )
    before_ids = list(persistence.tree.entries)

    assessment = await assess_and_repair_recovery(persistence)

    assert assessment.blocked is False
    assert assessment.reasons == []
    assert assessment.repaired_tool_call_ids == []
    assert list(persistence.tree.entries) == before_ids
    assert [message.content for message in _tool_messages(persistence)] == ["actual result"]
    assert [message["content"] for message in persistence.rebuild_model_messages()][-1] == [
        {"type": "text", "text": "actual result"}
    ]


@pytest.mark.asyncio
async def test_committed_without_result_is_inconsistent_and_blocked() -> None:
    store = _store()
    persistence = await _persistence(store)
    await _assistant(persistence, "call:missing")
    await _effect(
        persistence,
        "call:missing",
        commit_state=CommitState.COMMITTED,
        effect_class=EffectClass.PURE_READ,
    )
    before_ids = list(persistence.tree.entries)

    assessment = await assess_and_repair_recovery(persistence)

    assert assessment.blocked is True
    assert "committed but has no tool result" in assessment.reasons[0]
    assert assessment.repaired_tool_call_ids == []
    assert list(persistence.tree.entries) == before_ids


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("commit_state", "effect_class"),
    [
        (CommitState.IN_PROGRESS, EffectClass.EXTERNAL_MUTATION),
        (CommitState.COMMIT_UNKNOWN, EffectClass.PURE_READ),
        (CommitState.COMMITTED, EffectClass.LOCAL_MUTATION),
    ],
)
async def test_confirmed_recovery_preserves_commit_state_and_is_cold_idempotent(
    commit_state: CommitState,
    effect_class: EffectClass,
) -> None:
    store = _store()
    persistence = await _persistence(store)
    tool_name = "mutate" if effect_class != EffectClass.PURE_READ else "read"
    await _assistant(persistence, "call:confirmed", tool_name=tool_name)
    await _effect(
        persistence,
        "call:confirmed",
        commit_state=commit_state,
        effect_class=effect_class,
        fence=4,
        tool_name=tool_name,
    )

    blocked = await assess_and_repair_recovery(persistence)
    confirmed = await assess_and_repair_recovery(persistence, confirm_recovery=True)

    assert blocked.blocked is True
    assert confirmed.blocked is False
    assert confirmed.reasons == blocked.reasons
    assert confirmed.repaired_tool_call_ids == ["call:confirmed"]
    message = _tool_messages(persistence)[0]
    content = str(message.content)
    assert "uncertainty accepted" in content
    assert "not replayed" in content
    assert "rolled back" not in content
    effect = _latest_effect(persistence, "call:confirmed")
    assert effect["commit_state"] == commit_state.value
    assert effect["recovery_confirmed"] is True
    assert effect["uncertainty_accepted"] is True
    assert effect["original_call_replayed"] is False
    assert effect["fence"] == 4

    cold = await SubagentPersistence.load(store, persistence.child_session_id)
    assert cold is not None
    before_ids = list(cold.tree.entries)
    cold_assessment = await assess_and_repair_recovery(cold)
    assert cold_assessment.blocked is False
    assert cold_assessment.reasons == blocked.reasons
    assert cold_assessment.repaired_tool_call_ids == []
    assert list(cold.tree.entries) == before_ids
    assert len(_tool_messages(cold)) == 1
    assert _latest_effect(cold, "call:confirmed")["commit_state"] == commit_state.value


@pytest.mark.asyncio
async def test_safe_repair_is_idempotent_after_cold_load() -> None:
    store = _store()
    persistence = await _persistence(store)
    await _assistant(persistence, "call:cold")
    first = await assess_and_repair_recovery(persistence)
    assert first.blocked is False

    cold = await SubagentPersistence.load(store, persistence.child_session_id)
    assert cold is not None
    before_ids = list(cold.tree.entries)
    second = await assess_and_repair_recovery(cold)

    assert second.blocked is False
    assert second.reasons == []
    assert second.repaired_tool_call_ids == []
    assert list(cold.tree.entries) == before_ids
    assert len(_tool_messages(cold)) == 1
    assert len(cold.get_tool_effects()) == 1
    assert _latest_effect(cold, "call:cold")["commit_state"] == (
        CommitState.ROLLED_BACK.value
    )
