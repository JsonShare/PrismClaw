from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tests.engine.subagent.test_subagent_collaboration import (
    _fixture,
    _wait_for_agent_status,
    _wait_for_idle,
)
from tests.engine.subagent.test_subagent_records import _store

from prismclaw.engine.security.gate.types import (
    ParallelTaskExtras,
    TaskRuntimeContext,
)
from prismclaw.engine.subagent.control_tool import SubagentControlTool
from prismclaw.engine.subagent.core.run_context import SubagentRunContext
from prismclaw.engine.subagent.runtime.parallel import (
    ParallelTaskRuntimeRecord,
    SessionParallelTaskStore,
)
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext


class _Catalog:
    def __init__(self, launch) -> None:
        self.manifest = launch.manifest
        self.tool_registry = launch.tool_registry

    def get_agent_entry(self, name: str):
        if name != self.manifest.name:
            return None
        return SimpleNamespace(manifest=self.manifest)


async def _control_fixture(*, handler: Any = None):
    store, service, executor, launch, session_id = await _fixture(handler=handler)
    parallel_store = SessionParallelTaskStore(store)
    launch.parent_ctx.task_runtime = ParallelTaskExtras(
        task_runtime=TaskRuntimeContext(),
        parallel_task_store=parallel_store,
    )
    executor.parallel_store = parallel_store
    tool = SubagentControlTool(
        collaboration=service,
        capability_catalog=_Catalog(launch),
        executor=executor,
    )
    return store, service, executor, launch, tool, session_id


@pytest.mark.asyncio
async def test_list_status_send_replay_and_close() -> None:
    _store_, service, executor, launch, tool, session_id = await _control_fixture()
    spawned = await service.spawn(
        launch,
        task="initial",
        lifecycle="persistent",
        request_id="control-basic",
    )
    agent_id = spawned.agent.agent_id
    task_id = spawned.run.task_id
    assert task_id is not None

    listed = await tool.execute(
        {
            "_call_id": "list",
            "action": "list",
            "lifecycle": "persistent",
            "status": "idle",
        },
        launch.parent_ctx,
    )
    assert not listed.is_error
    assert [item["agent_id"] for item in listed.metadata["agents"]] == [agent_id]

    filtered = await tool.execute(
        {
            "_call_id": "list-filtered",
            "action": "list",
            "agent_id": agent_id,
        },
        launch.parent_ctx,
    )
    assert not filtered.is_error
    assert [item["agent_id"] for item in filtered.metadata["agents"]] == [agent_id]

    status = await tool.execute(
        {
            "_call_id": "status",
            "action": "status",
            "agent_id": agent_id,
            "task_id": task_id,
        },
        launch.parent_ctx,
    )
    assert not status.is_error
    assert status.metadata["agent"]["agent_id"] == agent_id
    assert status.metadata["run"]["run_id"] == spawned.run.run_id
    assert status.metadata["unacked_message_count"] == 0

    sent = await tool.execute(
        {
            "_call_id": "send",
            "action": "send",
            "agent_id": agent_id,
            "task_id": task_id,
            "content": "follow up",
            "request_id": "send-request",
        },
        launch.parent_ctx,
    )
    assert not sent.is_error
    message_id = sent.metadata["message_id"]
    await _wait_for_idle(service, session_id, agent_id, run_count=2)

    replay = await tool.execute(
        {
            "_call_id": "send-replay",
            "action": "send",
            "agent_id": agent_id,
            "task_id": task_id,
            "content": "follow up",
            "request_id": "send-request",
        },
        launch.parent_ctx,
    )
    assert not replay.is_error
    assert replay.metadata["message_id"] == message_id
    assert executor.calls == 2

    conflict = await tool.execute(
        {
            "_call_id": "send-conflict",
            "action": "send",
            "agent_id": agent_id,
            "task_id": task_id,
            "content": "different content",
            "request_id": "send-request",
        },
        launch.parent_ctx,
    )
    assert conflict.is_error
    assert "different content" in conflict.content[0].text

    closed = await tool.execute(
        {
            "_call_id": "close",
            "action": "close",
            "agent_id": agent_id,
            "task_id": task_id,
        },
        launch.parent_ctx,
    )
    assert not closed.is_error
    assert closed.metadata["agent"]["status"] == "closed"


@pytest.mark.asyncio
async def test_interrupt_and_resume_through_control() -> None:
    started = asyncio.Event()

    async def handler(call_number: int, run_context: SubagentRunContext) -> None:
        if call_number == 1:
            started.set()
            await run_context.interrupt_event.wait()
        await run_context.persist_assistant(f"done-{call_number}", [])

    _store_, service, executor, launch, tool, session_id = await _control_fixture(
        handler=handler
    )
    spawned = await service.spawn(
        launch,
        task="interrupt",
        lifecycle="persistent",
        request_id="control-interrupt",
        background=True,
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    agent_id = spawned.agent.agent_id
    task_id = spawned.run.task_id

    interrupted = await tool.execute(
        {
            "_call_id": "interrupt",
            "action": "interrupt",
            "agent_id": agent_id,
            "task_id": task_id,
        },
        launch.parent_ctx,
    )
    assert not interrupted.is_error
    await _wait_for_agent_status(service, session_id, agent_id, "interrupted")

    resumed = await tool.execute(
        {
            "_call_id": "resume",
            "action": "resume",
            "agent_id": agent_id,
            "task_id": task_id,
        },
        launch.parent_ctx,
    )
    assert not resumed.is_error
    assert resumed.metadata["agent"]["active_run_id"] != spawned.run.run_id
    await _wait_for_idle(service, session_id, agent_id, run_count=2)
    assert executor.calls == 2


@pytest.mark.asyncio
async def test_resume_definition_drift_surfaces_recovery_blocked() -> None:
    _store_, service, _executor, launch, tool, session_id = await _control_fixture()
    spawned = await service.spawn(
        launch,
        task="initial",
        lifecycle="persistent",
        request_id="control-drift",
    )
    agent_id = spawned.agent.agent_id
    task_id = spawned.run.task_id
    await tool.execute(
        {
            "_call_id": "interrupt",
            "action": "interrupt",
            "agent_id": agent_id,
            "task_id": task_id,
        },
        launch.parent_ctx,
    )
    tool._capability_catalog.manifest = launch.manifest.model_copy(
        update={"description": "changed"}
    )

    blocked = await tool.execute(
        {
            "_call_id": "resume",
            "action": "resume",
            "agent_id": agent_id,
            "task_id": task_id,
        },
        launch.parent_ctx,
    )
    assert blocked.is_error
    assert blocked.metadata["agent"]["status"] == "recovery_blocked"
    run = await service.records.get_run(session_id=session_id, run_id=spawned.run.run_id)
    assert run is not None and run.safe_boundary == "definition_changed"


@pytest.mark.asyncio
async def test_control_is_isolated_by_parent_session() -> None:
    store, service, _executor, launch, tool, _session_id = await _control_fixture()
    spawned = await service.spawn(
        launch,
        task="private",
        lifecycle="persistent",
        request_id="control-isolation",
    )
    other = await store.create_new_session("other", "ws", "main")
    other_context = ToolContext(
        workspace_id="ws",
        workspace_path=Path("/tmp"),
        session_id=other.header.id,
        task_runtime=ParallelTaskExtras(
            task_runtime=TaskRuntimeContext(),
            parallel_task_store=SessionParallelTaskStore(store),
        ),
    )

    status = await tool.execute(
        {
            "_call_id": "foreign-status",
            "action": "status",
            "agent_id": spawned.agent.agent_id,
            "task_id": spawned.run.task_id,
        },
        other_context,
    )
    assert status.is_error
    assert not status.metadata or "agent" not in status.metadata

    listed = await tool.execute(
        {"_call_id": "foreign-list", "action": "list"}, other_context
    )
    assert not listed.is_error
    assert listed.metadata["agents"] == []
    original = await service.records.get_session(
        session_id=_session_id,
        agent_id=spawned.agent.agent_id,
    )
    assert original is not None and original.status == "idle"


@pytest.mark.asyncio
async def test_composite_addressing_requires_both_keys() -> None:
    _store_, service, _executor, launch, tool, _session_id = await _control_fixture()
    spawned = await service.spawn(
        launch,
        task="initial",
        lifecycle="persistent",
        request_id="control-composite",
    )
    agent_id = spawned.agent.agent_id
    task_id = spawned.run.task_id
    assert task_id is not None

    # ① 只给 agent_id 调 status → 报错
    only_agent = await tool.execute(
        {"_call_id": "only-agent", "action": "status", "agent_id": agent_id},
        launch.parent_ctx,
    )
    assert only_agent.is_error
    assert "'task_id'" in only_agent.content[0].text

    # ② 只给 task_id 调 status → 报错
    only_task = await tool.execute(
        {"_call_id": "only-task", "action": "status", "task_id": task_id},
        launch.parent_ctx,
    )
    assert only_task.is_error
    assert "'agent_id'" in only_task.content[0].text

    # ③ 双键齐全 → 正常
    both = await tool.execute(
        {
            "_call_id": "both",
            "action": "status",
            "agent_id": agent_id,
            "task_id": task_id,
        },
        launch.parent_ctx,
    )
    assert not both.is_error
    assert both.metadata["agent"]["agent_id"] == agent_id

    # ④ 组合不存在 → 报错
    missing = await tool.execute(
        {
            "_call_id": "combo-missing",
            "action": "status",
            "agent_id": agent_id,
            "task_id": "pt:missing",
        },
        launch.parent_ctx,
    )
    assert missing.is_error
    assert "未找到" in missing.content[0].text

    # ⑤ task 属于其他 agent → 组合不匹配报错
    other = await service.spawn(
        launch,
        task="other",
        lifecycle="persistent",
        request_id="control-composite-other",
    )
    mismatch = await tool.execute(
        {
            "_call_id": "combo-mismatch",
            "action": "status",
            "agent_id": other.agent.agent_id,
            "task_id": task_id,
        },
        launch.parent_ctx,
    )
    assert mismatch.is_error
    assert "不匹配" in mismatch.content[0].text
    assert agent_id in mismatch.content[0].text


@pytest.mark.asyncio
async def test_list_rejects_task_id() -> None:
    _store_, service, _executor, launch, tool, _session_id = await _control_fixture()
    spawned = await service.spawn(
        launch,
        task="initial",
        lifecycle="persistent",
        request_id="control-list-task",
    )

    rejected = await tool.execute(
        {
            "_call_id": "list-with-task",
            "action": "list",
            "task_id": spawned.run.task_id,
        },
        launch.parent_ctx,
    )
    assert rejected.is_error
    assert "'task_id'" in rejected.content[0].text


@pytest.mark.asyncio
async def test_parallel_task_store_rejects_task_reassignment() -> None:
    store = _store()
    session = await store.create_new_session("tasks", "ws", "main")
    parallel_store = SessionParallelTaskStore(store)
    session_id = session.header.id

    scheduled = ParallelTaskRuntimeRecord(
        task_id="pt:1",
        agent_id="ag:1",
        owner_session_id=session_id,
        kind="subagent",
        status="scheduled",
    )
    await parallel_store.append(scheduled)
    # 同组合键重复写入覆盖
    await parallel_store.append(scheduled.model_copy(update={"status": "running"}))
    current = await parallel_store.get(
        session_id=session_id,
        agent_id="ag:1",
        task_id="pt:1",
    )
    assert current is not None and current.status == "running"

    # 同 task_id 换 agent_id 违反组合唯一
    with pytest.raises(ValueError):
        await parallel_store.append(
            scheduled.model_copy(update={"agent_id": "ag:2"})
        )


@pytest.mark.asyncio
async def test_one_shot_rejects_send_and_resume() -> None:
    _store_, service, _executor, launch, tool, _session_id = await _control_fixture()
    spawned = await service.spawn(
        launch,
        task="once",
        lifecycle="one_shot",
        request_id="control-one-shot",
    )
    agent_id = spawned.agent.agent_id
    task_id = spawned.run.task_id

    sent = await tool.execute(
        {
            "_call_id": "one-shot-send",
            "action": "send",
            "agent_id": agent_id,
            "task_id": task_id,
            "content": "follow up",
        },
        launch.parent_ctx,
    )
    assert sent.is_error and "one_shot" in sent.content[0].text

    resumed = await tool.execute(
        {
            "_call_id": "one-shot-resume",
            "action": "resume",
            "agent_id": agent_id,
            "task_id": task_id,
        },
        launch.parent_ctx,
    )
    assert resumed.is_error and "one_shot" in resumed.content[0].text
