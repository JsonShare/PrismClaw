from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from tests.engine.subagent.test_subagent_collaboration import _fixture, _wait_for_idle

from prismclaw.engine.subagent.tool import SubAgentTool
from prismclaw.schema.config.config import SubAgentConfig


class _Catalog:
    def __init__(self, launch) -> None:
        self.manifest = launch.manifest
        self.tool_registry = launch.tool_registry

    def count_agent_entries(self) -> int:
        return 1

    def get_agent_entry(self, name: str):
        if name != self.manifest.name:
            return None
        return SimpleNamespace(name=name, manifest=self.manifest, expert_name=None)

    def list_agent_entries(self) -> list[Any]:
        return [self.get_agent_entry(self.manifest.name)]


def _tool(service, executor, launch, *, allow_background: bool = True) -> SubAgentTool:
    return SubAgentTool(
        executor=executor,
        registry=SimpleNamespace(),
        settings=SubAgentConfig(),
        capability_catalog=_Catalog(launch),
        collaboration=service,
        allow_background=allow_background,
    )


@pytest.mark.asyncio
async def test_sync_result_and_persistent_replay_share_identity() -> None:
    _store, service, executor, launch, _session_id = await _fixture()
    tool = _tool(service, executor, launch)
    args = {
        "_call_id": "sync-call",
        "agents": [
            {
                "agent_name": "explorer",
                "task": "inspect once",
                "request_id": "tool-sync-replay",
            }
        ],
    }

    live = await tool.execute(args, launch.parent_ctx)
    replay = await tool.execute(args, launch.parent_ctx)

    assert not live.is_error
    assert not replay.is_error
    assert executor.calls == 1
    assert live.subagent_envelope is not None
    assert replay.subagent_envelope is not None
    assert live.subagent_envelope.agent_id == replay.subagent_envelope.agent_id
    assert live.subagent_envelope.run_id == replay.subagent_envelope.run_id
    assert live.subagent_envelope.child_session_id == (replay.subagent_envelope.child_session_id)
    assert replay.metadata["results"][0]["status"] == "success"
    assert "done" in replay.content[0].text


@pytest.mark.asyncio
async def test_multi_replica_rejects_one_shot_background_but_allows_persistent() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(_call_number: int, run_context) -> None:
        started.set()
        await release.wait()
        await run_context.persist_assistant("done", [])

    _store, service, executor, launch, session_id = await _fixture(handler=handler)
    tool = _tool(service, executor, launch, allow_background=False)
    assert "dispatch_mode" in tool.parameters["properties"]

    rejected = await tool.execute(
        {
            "_call_id": "one-shot-background",
            "dispatch_mode": "background",
            "agents": [{"agent_name": "explorer", "task": "reject"}],
        },
        launch.parent_ctx,
    )
    assert rejected.is_error
    assert "one_shot 后台派发" in rejected.content[0].text
    assert executor.calls == 0

    accepted = await tool.execute(
        {
            "_call_id": "persistent-background",
            "dispatch_mode": "background",
            "agents": [
                {
                    "agent_name": "explorer",
                    "task": "accept",
                    "lifecycle": "persistent",
                    "request_id": "persistent-multi-replica",
                }
            ],
        },
        launch.parent_ctx,
    )
    assert not accepted.is_error
    text = accepted.content[0].text
    assert "agent_id=ag:" in text
    assert "run_id=ar:" in text
    assert "lifecycle=persistent" in text
    assert "subagent_control" in text
    await asyncio.wait_for(started.wait(), timeout=1)
    release.set()
    agents = await service.list(session_id=session_id)
    assert len(agents) == 1
    await _wait_for_idle(service, session_id, agents[0].agent_id, run_count=1)


@pytest.mark.asyncio
async def test_persistent_spawn_rejects_fan_out() -> None:
    _store, service, executor, launch, _session_id = await _fixture()
    tool = _tool(service, executor, launch)

    result = await tool.execute(
        {
            "_call_id": "persistent-fan-out",
            "agents": [
                {
                    "agent_name": "explorer",
                    "task": "first",
                    "lifecycle": "persistent",
                },
                {
                    "agent_name": "explorer",
                    "task": "second",
                    "lifecycle": "persistent",
                },
            ],
        },
        launch.parent_ctx,
    )

    assert result.is_error
    assert "persistent 每次 spawn 只能包含一个 Agent" in result.content[0].text
    assert executor.calls == 0
