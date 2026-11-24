from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from tests.engine.subagent.test_subagent_records import _store

from prismclaw.engine.subagent.core._result_types import AgentRunResult
from prismclaw.engine.subagent.core.run_context import SubagentRunContext
from prismclaw.engine.subagent.definition.manifest import AgentManifest
from prismclaw.engine.subagent.runtime.collaboration import (
    SubagentCollaborationError,
    SubagentCollaborationService,
    SubagentConflictError,
    SubagentRunLaunch,
)
from prismclaw.engine.subagent.runtime.lease import LocalSubagentLeaseStore
from prismclaw.engine.subagent.runtime.parallel import ParallelTaskRuntimeRecord
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.content.content_types import (
    SubagentEnvelopeArtifact,
    TextBlock,
    ToolResult,
)
from prismclaw.schema.content.subagent import SubagentResultEnvelope


class _ToolRegistry:
    def names(self) -> list[str]:
        return []

    def list_for_llm(self, _names: list[str]) -> list[dict[str, Any]]:
        return []

    def get(self, _name: str) -> None:
        return None


class _Executor:
    _default_model = "test-model"
    parallel_store = None

    def __init__(self, handler: Any = None) -> None:
        self.handler = handler
        self.calls = 0

    async def run(self, manifest, task, parent_ctx, **kwargs) -> AgentRunResult:
        del task
        self.calls += 1
        call_number = self.calls
        run_context: SubagentRunContext = kwargs["run_context"]
        # 与真实 SubAgentExecutor 一致：每个 Run 写一条携带
        # (agent_id, task_id) 组合键的并行任务记录。
        if self.parallel_store is not None and kwargs.get("task_id"):
            await self.parallel_store.append(
                ParallelTaskRuntimeRecord(
                    task_id=kwargs["task_id"],
                    agent_id=run_context.agent_id,
                    owner_session_id=parent_ctx.session_id,
                    kind="subagent",
                    status="running",
                    run_id=run_context.run_id,
                    agent_name=manifest.name,
                )
            )
        run_context.build_messages()
        if self.handler is not None:
            await self.handler(call_number, run_context)
        else:
            await run_context.persist_assistant("done", [])
        envelope = SubagentEnvelopeArtifact(
            agent_id=run_context.agent_id,
            run_id=run_context.run_id,
            child_session_id=run_context.child_session_id,
            lifecycle=run_context.lifecycle,
            final_text="done",
            result_envelope=SubagentResultEnvelope(summary="done"),
        )
        return AgentRunResult(
            tool_result=ToolResult(
                tool_call_id=kwargs.get("call_id", ""),
                content=[TextBlock(text="done")],
                subagent_envelope=envelope,
            ),
            outcome="success",
            turns=1,
            tokens_input=0,
            tokens_output=0,
            duration_seconds=0.0,
            model_used="test-model",
        )


async def _fixture(*, handler: Any = None):
    store = _store()
    parent = await store.create_new_session("parent", "ws", "main")
    executor = _Executor(handler)
    context = ToolContext(
        workspace_id="ws",
        workspace_path=Path("/tmp"),
        session_id=parent.header.id,
    )
    launch = SubagentRunLaunch(
        manifest=AgentManifest(name="explorer", description="explore"),
        executor=executor,
        parent_ctx=context,
        tool_registry=_ToolRegistry(),
        agent_source="builtin",
    )
    service = SubagentCollaborationService(
        session_store=store,
        worker_id="worker-test",
    )
    return store, service, executor, launch, parent.header.id


async def _wait_for_idle(service, session_id: str, agent_id: str, run_count: int) -> None:
    async def wait() -> None:
        while True:
            agent = await service.records.get_session(
                session_id=session_id,
                agent_id=agent_id,
            )
            runs = await service.records.list_runs(
                session_id=session_id,
                agent_id=agent_id,
            )
            if (
                agent is not None
                and agent.status == "idle"
                and len(runs) == run_count
                and all(run.finished_at for run in runs)
            ):
                return
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), timeout=2)


@pytest.mark.asyncio
async def test_one_shot_spawn_persists_identity_and_closes() -> None:
    _store_value, service, executor, launch, session_id = await _fixture()

    outcome = await service.spawn(
        launch,
        task="inspect the code",
        lifecycle="one_shot",
        request_id="spawn-one",
    )

    assert executor.calls == 1
    assert outcome.result is not None
    assert outcome.agent.agent_id.startswith("ag:")
    assert outcome.run is not None
    assert outcome.run.run_id.startswith("ar:")
    assert outcome.run.status == "succeeded"
    assert outcome.agent.status == "closed"
    assert outcome.agent.child_session_id == outcome.run.child_session_id

    replay = await service.spawn(
        launch,
        task="inspect the code",
        lifecycle="one_shot",
        request_id="spawn-one",
    )
    assert replay.agent.agent_id == outcome.agent.agent_id
    assert replay.run is not None and replay.run.run_id == outcome.run.run_id
    assert executor.calls == 1

    with pytest.raises(SubagentCollaborationError, match="one_shot"):
        await service.send(
            session_id=session_id,
            agent_id=outcome.agent.agent_id,
            content="follow up",
            launch=launch,
        )


@pytest.mark.asyncio
async def test_finalize_and_send_race_starts_exactly_one_follow_up() -> None:
    first_acked = asyncio.Event()
    finish_first = asyncio.Event()
    second_seen = asyncio.Event()

    async def handler(call_number: int, run_context: SubagentRunContext) -> None:
        if call_number == 1:
            await run_context.persist_assistant("first done", [])
            first_acked.set()
            await finish_first.wait()
        else:
            assert any(
                "new instruction" in str(message.get("content"))
                for message in run_context.build_messages()
            )
            await run_context.persist_assistant("follow-up done", [])
            second_seen.set()

    _store_value, service, executor, launch, session_id = await _fixture(handler=handler)
    spawned = await service.spawn(
        launch,
        task="initial task",
        lifecycle="persistent",
        request_id="spawn-persistent",
        background=True,
    )
    await asyncio.wait_for(first_acked.wait(), timeout=1)

    _agent, message_id = await service.send(
        session_id=session_id,
        agent_id=spawned.agent.agent_id,
        content="new instruction",
        request_id="send-one",
        launch=launch,
    )
    _agent, replay_message_id = await service.send(
        session_id=session_id,
        agent_id=spawned.agent.agent_id,
        content="new instruction",
        request_id="send-one",
        launch=launch,
    )
    assert replay_message_id == message_id

    with pytest.raises(SubagentConflictError, match="different content"):
        await service.send(
            session_id=session_id,
            agent_id=spawned.agent.agent_id,
            content="conflicting instruction",
            request_id="send-one",
            launch=launch,
        )

    finish_first.set()
    await asyncio.wait_for(second_seen.wait(), timeout=1)
    await _wait_for_idle(service, session_id, spawned.agent.agent_id, run_count=2)
    runs = await service.records.list_runs(
        session_id=session_id,
        agent_id=spawned.agent.agent_id,
    )
    assert len({run.run_id for run in runs}) == 2
    assert executor.calls == 2

    closed = await service.close(
        session_id=session_id,
        agent_id=spawned.agent.agent_id,
    )
    assert closed.status == "closed"
    with pytest.raises(SubagentCollaborationError, match="closed"):
        await service.send(
            session_id=session_id,
            agent_id=spawned.agent.agent_id,
            content="after close",
            launch=launch,
        )


async def _wait_for_agent_status(service, session_id: str, agent_id: str, status: str):
    async def wait():
        while True:
            agent = await service.records.get_session(
                session_id=session_id,
                agent_id=agent_id,
            )
            if agent is not None and agent.status == status:
                return agent
            await asyncio.sleep(0)

    return await asyncio.wait_for(wait(), timeout=2)


@pytest.mark.asyncio
async def test_concurrent_sync_spawn_replay_executes_once() -> None:
    release = asyncio.Event()

    async def handler(_call_number: int, run_context: SubagentRunContext) -> None:
        await release.wait()
        await run_context.persist_assistant("done", [])

    _store_value, service, executor, launch, session_id = await _fixture(handler=handler)
    first = asyncio.create_task(
        service.spawn(
            launch,
            task="inspect once",
            lifecycle="one_shot",
            request_id="same-request",
        )
    )
    await asyncio.sleep(0)
    second = asyncio.create_task(
        service.spawn(
            launch,
            task="inspect once",
            lifecycle="one_shot",
            request_id="same-request",
        )
    )
    await asyncio.sleep(0)
    assert executor.calls == 1

    release.set()
    outcomes = await asyncio.gather(first, second)
    assert executor.calls == 1
    assert {item.agent.agent_id for item in outcomes} == {outcomes[0].agent.agent_id}
    assert {item.run.run_id for item in outcomes if item.run} == {outcomes[0].run.run_id}
    assert all(item.result is not None for item in outcomes)
    replay = await service.spawn(
        launch,
        task="inspect once",
        lifecycle="one_shot",
        request_id="same-request",
    )
    assert replay.agent.owner_session_id == session_id
    assert replay.result is None
    assert replay.run is not None and replay.run.result_envelope is not None


@pytest.mark.asyncio
async def test_interrupt_and_resume_create_a_new_attempt_even_after_ack() -> None:
    started = asyncio.Event()

    async def handler(call_number: int, run_context: SubagentRunContext) -> None:
        if call_number == 1:
            started.set()
            await run_context.interrupt_event.wait()
        await run_context.persist_assistant(f"done-{call_number}", [])

    _store_value, service, executor, launch, session_id = await _fixture(handler=handler)
    spawned = await service.spawn(
        launch,
        task="interrupt me",
        lifecycle="persistent",
        request_id="interrupt-resume",
        background=True,
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    await service.interrupt(
        session_id=session_id,
        agent_id=spawned.agent.agent_id,
    )
    interrupted = await _wait_for_agent_status(
        service,
        session_id,
        spawned.agent.agent_id,
        "interrupted",
    )
    first_run = await service.records.get_run(
        session_id=session_id,
        run_id=spawned.run.run_id,
    )
    assert first_run is not None and first_run.status == "interrupted"

    resumed = await service.resume(
        session_id=session_id,
        agent_id=interrupted.agent_id,
        launch=launch,
    )
    assert resumed.active_run_id is not None
    assert resumed.active_run_id != first_run.run_id
    await _wait_for_idle(service, session_id, spawned.agent.agent_id, run_count=2)
    runs = await service.records.list_runs(
        session_id=session_id,
        agent_id=spawned.agent.agent_id,
    )
    latest = runs[-1]
    assert latest.attempt == first_run.attempt + 1
    assert latest.fence > first_run.fence
    assert latest.resumed_from_run_id == first_run.run_id
    assert executor.calls == 2


@pytest.mark.asyncio
async def test_close_running_agent_cancels_run_and_rejects_resume() -> None:
    started = asyncio.Event()

    async def handler(_call_number: int, run_context: SubagentRunContext) -> None:
        started.set()
        await run_context.interrupt_event.wait()
        await run_context.persist_assistant("stopped", [])

    _store_value, service, _executor, launch, session_id = await _fixture(handler=handler)
    spawned = await service.spawn(
        launch,
        task="close me",
        lifecycle="persistent",
        request_id="close-running",
        background=True,
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    closed = await service.close(
        session_id=session_id,
        agent_id=spawned.agent.agent_id,
    )
    assert closed.status == "closed"
    run = await service.records.get_run(
        session_id=session_id,
        run_id=spawned.run.run_id,
    )

    async def wait_for_terminal():
        nonlocal run
        while run is None or run.finished_at is None:
            await asyncio.sleep(0)
            run = await service.records.get_run(
                session_id=session_id,
                run_id=spawned.run.run_id,
            )
        return run

    run = await asyncio.wait_for(wait_for_terminal(), timeout=1)
    assert run.status == "cancelled"
    with pytest.raises(SubagentCollaborationError, match="closed"):
        await service.resume(
            session_id=session_id,
            agent_id=spawned.agent.agent_id,
            launch=launch,
        )


@pytest.mark.asyncio
async def test_resume_blocks_definition_drift() -> None:
    _store_value, service, _executor, launch, session_id = await _fixture()
    spawned = await service.spawn(
        launch,
        task="initial",
        lifecycle="persistent",
        request_id="definition-drift",
    )
    interrupted = await service.interrupt(
        session_id=session_id,
        agent_id=spawned.agent.agent_id,
    )
    assert interrupted.status == "interrupted"
    changed = replace(
        launch,
        manifest=launch.manifest.model_copy(update={"description": "changed"}),
    )
    blocked = await service.resume(
        session_id=session_id,
        agent_id=spawned.agent.agent_id,
        launch=changed,
    )
    assert blocked.status == "recovery_blocked"
    latest = await service.records.get_run(
        session_id=session_id,
        run_id=spawned.run.run_id,
    )
    assert latest is not None
    assert latest.safe_boundary == "definition_changed"
    assert "close this agent" in (latest.recovery_reason or "")


class _FailingTaskManager:
    def spawn(self, *_args, **_kwargs) -> None:
        raise RuntimeError("schedule failed")


@pytest.mark.asyncio
async def test_schedule_failure_finalizes_run_and_releases_lease() -> None:
    _store, service, executor, launch, session_id = await _fixture()
    launch.parent_ctx.task_manager = _FailingTaskManager()

    with pytest.raises(RuntimeError, match="schedule failed"):
        await service.spawn(
            launch,
            task="cannot schedule",
            lifecycle="persistent",
            request_id="schedule-failure",
            background=True,
        )

    assert executor.calls == 0
    agents = await service.records.list_sessions(session_id=session_id)
    assert len(agents) == 1
    assert agents[0].status == "idle"
    assert agents[0].active_run_id is None
    runs = await service.records.list_runs(
        session_id=session_id,
        agent_id=agents[0].agent_id,
    )
    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert runs[0].finished_at is not None
    assert "schedule failed" in (runs[0].error_summary or "")
    assert await service._leases.list_active() == []
    assert service._handles == {}


@pytest.mark.asyncio
async def test_reconcile_orphans_marks_expired_remote_run_interrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _unused_service, executor, launch, session_id = await _fixture()
    leases = LocalSubagentLeaseStore(ttl_seconds=0.001)
    owner = SubagentCollaborationService(
        session_store=store,
        lease_store=leases,
        worker_id="worker-owner",
    )
    recovery = SubagentCollaborationService(
        session_store=store,
        lease_store=leases,
        worker_id="worker-recovery",
    )

    async def skip_schedule(_handle) -> None:
        return None

    monkeypatch.setattr(owner, "_schedule", skip_schedule)
    spawned = await owner.spawn(
        launch,
        task="orphan",
        lifecycle="persistent",
        request_id="orphan-run",
        background=True,
    )
    assert spawned.run is not None
    await asyncio.sleep(0.01)

    await recovery.reconcile_orphans()

    assert executor.calls == 0
    agent = await recovery.records.get_session(
        session_id=session_id,
        agent_id=spawned.agent.agent_id,
    )
    assert agent is not None and agent.status == "interrupted"
    assert agent.active_run_id is None
    run = await recovery.records.get_run(
        session_id=session_id,
        run_id=spawned.run.run_id,
    )
    assert run is not None and run.status == "interrupted"
    assert run.safe_boundary == "recovery_assessed"
    assert run.finished_at is not None
    assert await leases.list_active() == []


@pytest.mark.asyncio
async def test_resume_requires_confirmation_for_unknown_tool_effect() -> None:
    started = asyncio.Event()

    async def handler(call_number: int, run_context: SubagentRunContext) -> None:
        if call_number == 1:
            call = {
                "id": "dangerous-call",
                "type": "function",
                "function": {"name": "unknown-write", "arguments": "{}"},
            }
            await run_context.persist_assistant("working", [call])
            await run_context.before_tool_dispatch([call], _ToolRegistry())
            started.set()
            await run_context.interrupt_event.wait()
        else:
            await run_context.persist_assistant("recovered", [])

    _store, service, executor, launch, session_id = await _fixture(handler=handler)
    spawned = await service.spawn(
        launch,
        task="uncertain effect",
        lifecycle="persistent",
        request_id="dangerous-recovery",
        background=True,
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    await service.interrupt(session_id=session_id, agent_id=spawned.agent.agent_id)
    await _wait_for_agent_status(
        service,
        session_id,
        spawned.agent.agent_id,
        "interrupted",
    )

    blocked = await service.resume(
        session_id=session_id,
        agent_id=spawned.agent.agent_id,
        launch=launch,
    )
    assert blocked.status == "recovery_blocked"
    assert executor.calls == 1

    resumed = await service.resume(
        session_id=session_id,
        agent_id=spawned.agent.agent_id,
        launch=launch,
        confirm_recovery=True,
    )
    assert resumed.status == "running"
    await _wait_for_idle(service, session_id, spawned.agent.agent_id, run_count=2)
    assert executor.calls == 2


@pytest.mark.asyncio
async def test_follow_up_handle_survives_previous_run_finalize() -> None:
    """run 结束时的清理不能删掉 finalize 阶段新建的续跑 handle。"""
    release_first = asyncio.Event()
    second_started = asyncio.Event()
    release_second = asyncio.Event()

    async def handler(call_number: int, run_context: SubagentRunContext) -> None:
        if call_number == 1:
            await release_first.wait()
        else:
            second_started.set()
            await release_second.wait()
            await run_context.persist_assistant("done", [])

    _store, service, _executor, launch, session_id = await _fixture(handler=handler)
    spawned = await service.spawn(
        launch,
        task="first",
        lifecycle="persistent",
        request_id="handle-identity",
        background=True,
    )
    agent_id = spawned.agent.agent_id
    first_run_id = spawned.agent.active_run_id

    await service.send(
        session_id=session_id,
        agent_id=agent_id,
        content="follow up",
        launch=launch,
        request_id="handle-follow-up",
    )
    release_first.set()
    await asyncio.wait_for(second_started.wait(), timeout=2)

    handle = service._handles.get(agent_id)
    assert handle is not None
    assert handle.run.run_id != first_run_id

    release_second.set()
    await _wait_for_idle(service, session_id, agent_id, run_count=2)


@pytest.mark.asyncio
async def test_run_fence_stays_monotonic_after_local_lease_reset() -> None:
    """Local lease store 重启归零后，新记录仍须高于已持久化的 fence。"""
    release = asyncio.Event()

    async def handler(_call_number: int, run_context: SubagentRunContext) -> None:
        await release.wait()
        await run_context.persist_assistant("done", [])

    store, service, _executor, launch, session_id = await _fixture(handler=handler)
    spawned = await service.spawn(
        launch,
        task="first",
        lifecycle="persistent",
        request_id="fence-reset",
        background=True,
    )
    agent_id = spawned.agent.agent_id
    release.set()
    await _wait_for_idle(service, session_id, agent_id, run_count=1)

    await service.send(
        session_id=session_id,
        agent_id=agent_id,
        content="second",
        launch=launch,
        request_id="fence-second",
    )
    await _wait_for_idle(service, session_id, agent_id, run_count=2)
    before = await service.records.get_session(session_id=session_id, agent_id=agent_id)
    assert before is not None
    assert before.current_fence >= 2

    restarted = SubagentCollaborationService(
        session_store=store,
        worker_id="worker-restart",
    )
    release.clear()
    await restarted.send(
        session_id=session_id,
        agent_id=agent_id,
        content="after restart",
        launch=launch,
        request_id="fence-after-restart",
    )
    current = await restarted.records.get_session(session_id=session_id, agent_id=agent_id)
    assert current is not None
    assert current.status == "running"
    assert current.active_run_id is not None
    runs = await restarted.records.list_runs(session_id=session_id, agent_id=agent_id)
    active = next(item for item in runs if item.run_id == current.active_run_id)
    assert active.fence > before.current_fence

    release.set()
    await _wait_for_idle(restarted, session_id, agent_id, run_count=3)


@pytest.mark.asyncio
async def test_spawn_requires_resume_when_pending_effects_exist() -> None:
    """有未决工具副作用时 spawn 不得绕过恢复评估直接重启。"""
    started = asyncio.Event()

    async def handler(call_number: int, run_context: SubagentRunContext) -> None:
        if call_number == 1:
            call = {
                "id": "pending-write",
                "type": "function",
                "function": {"name": "unknown-write", "arguments": "{}"},
            }
            await run_context.persist_assistant("working", [call])
            await run_context.before_tool_dispatch([call], _ToolRegistry())
            started.set()
            await run_context.interrupt_event.wait()

    _store, service, executor, launch, session_id = await _fixture(handler=handler)
    spawned = await service.spawn(
        launch,
        task="uncertain effect",
        lifecycle="persistent",
        request_id="spawn-guard",
        background=True,
    )
    agent_id = spawned.agent.agent_id
    await asyncio.wait_for(started.wait(), timeout=1)
    await service.interrupt(session_id=session_id, agent_id=agent_id)
    await _wait_for_agent_status(service, session_id, agent_id, "interrupted")

    await service.send(
        session_id=session_id,
        agent_id=agent_id,
        content="follow up after interrupt",
        launch=launch,
        request_id="spawn-guard-pending",
    )

    with pytest.raises(SubagentCollaborationError, match="unresolved tool effects"):
        await service.spawn(
            launch,
            task="uncertain effect",
            lifecycle="persistent",
            request_id="spawn-guard",
        )
    assert executor.calls == 1
