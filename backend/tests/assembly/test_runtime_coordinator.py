from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest
from redis.asyncio import Redis

from prismclaw.app.gateway.worker_registry import WorkerRegistry
from prismclaw.assembly.experts.expert_lifecycle import ExpertLifecycleManager, ExpertRuntimeBundle
from prismclaw.assembly.runtime_coordinator import (
    ModuleRuntimeCoordinator,
    collect_local_package_roots,
)
from prismclaw.engine.execution.loop.deps import AgentRunnerDeps
from prismclaw.engine.experts.registry.expert_registry import ExpertRegistry
from prismclaw.infra.config.capability_policy import CapabilityPolicyRegistry
from prismclaw.infra.config.module_release import (
    ModuleReleaseStore,
    build_local_release_document,
    create_release_document,
)
from prismclaw.infra.settings import Settings
from prismclaw.schema.shared import ProjectCapabilityLayout


class _Catalog:
    def __init__(self, *expert_ids: str) -> None:
        self._expert_ids = frozenset(expert_ids)

    def known_ids(self, kind: str) -> frozenset[str]:
        return self._expert_ids if kind == "expert" else frozenset()


class _MCPManager:
    def __init__(self) -> None:
        self.shutdown_calls = 0

    async def shutdown(self) -> None:
        self.shutdown_calls += 1


def test_collect_local_package_roots_uses_canonical_deployed_expert_ids(tmp_path: Path) -> None:
    expert = SimpleNamespace(expert_id="expert-a@experts", root_path=tmp_path / "expert-a")
    second = SimpleNamespace(expert_id="expert-b@experts", root_path=tmp_path / "expert-b")
    registry = SimpleNamespace(list_experts=lambda: (expert, second))

    assert collect_local_package_roots(cast(ExpertRegistry, registry)) == {
        "expert-a@experts": tmp_path / "expert-a",
        "expert-b@experts": tmp_path / "expert-b",
    }


class _Store:
    def __init__(self, document: object) -> None:
        self.document = document
        self.acks: list[tuple[int, str]] = []

    async def fetch_current(self) -> object:
        return self.document

    async def ack(self, version: int, worker_id: str, *, ttl_seconds: int = 90) -> None:
        self.acks.append((version, worker_id))


async def test_hash_mismatch_is_not_ready_and_does_not_build_or_ack(tmp_path: Path) -> None:
    package = tmp_path / "expert-a"
    package.mkdir()
    (package / "SKILL.md").write_text("# old\n", encoding="utf-8")
    document = build_local_release_document(
        {"expert-a": package}, version=1, capability_policy={}, metadata={}
    )
    (package / "SKILL.md").write_text("# drifted\n", encoding="utf-8")
    store = _Store(document)
    build_calls = 0

    async def build_candidate() -> object:
        nonlocal build_calls
        build_calls += 1
        raise AssertionError("must not build for hash mismatch")

    registry = CapabilityPolicyRegistry(tmp_path / "missing.yaml", catalog=_Catalog())
    deps = SimpleNamespace(
        expert=SimpleNamespace(
            environment_catalog=MagicMock(),
            lifecycle_manager=SimpleNamespace(build_candidate=build_candidate),
            capability_policy_registry=registry,
        )
    )
    coordinator = ModuleRuntimeCoordinator(
        store=cast(ModuleReleaseStore, store),
        worker_registry=cast(WorkerRegistry, SimpleNamespace(worker_id="worker-a")),
        runner_deps=cast(AgentRunnerDeps, deps),
        package_roots={"expert-a": package},
    )

    assert await coordinator.sync_once() is False
    assert coordinator.ready is False
    assert coordinator.health["target_version"] == 1
    assert "RELEASE_PACKAGE_HASH_MISMATCH" in coordinator.health["error"]
    assert build_calls == 0
    assert store.acks == []


async def test_valid_candidate_commits_runtime_and_snapshot_then_acks_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "expert-a"
    package.mkdir()
    (package / "SKILL.md").write_text("# A\n", encoding="utf-8")
    document = build_local_release_document(
        {"expert-a": package},
        version=1,
        capability_policy={"overrides": {"expert": {"expert-a": False}}},
        metadata={},
    )
    store = _Store(document)
    lifecycle = object.__new__(ExpertLifecycleManager)
    lifecycle._settings = cast(Settings, SimpleNamespace())
    lifecycle._base_mcp_manager = None
    lifecycle._generation = 0
    lifecycle._bundle = cast(ExpertRuntimeBundle | None, SimpleNamespace())
    lifecycle._workspace_default = tmp_path
    lifecycle._injected_layout = cast(ProjectCapabilityLayout | None, SimpleNamespace())
    lifecycle._cached_layout = None
    old_mcp = _MCPManager()
    new_mcp = _MCPManager()
    new_extension = object()
    new_bundle = ExpertRuntimeBundle(
        extension_manager=new_extension,
        skill_provider=object(),
        runtime_mcp_manager=new_mcp,
        expert_registry=cast(ExpertRegistry, _Catalog("expert-a")),
    )
    build_calls = 0

    async def build() -> ExpertRuntimeBundle:
        nonlocal build_calls
        build_calls += 1
        return new_bundle

    monkeypatch.setattr(lifecycle, "_build_bundle", build)
    monkeypatch.setattr(
        "prismclaw.runtime.capability_refresh.refresh_runner_capabilities",
        lambda *args, **kwargs: None,
    )
    registry = CapabilityPolicyRegistry(tmp_path / "missing.yaml", catalog=_Catalog())
    deps = SimpleNamespace(
        expert=SimpleNamespace(
            environment_catalog=MagicMock(),
            lifecycle_manager=lifecycle,
            capability_policy_registry=registry,
            runtime_mcp_manager=old_mcp,
            extension_manager=object(),
            expert_registry=object(),
        ),
        skill_provider=None,
        hook_dispatcher=None,
    )
    coordinator = ModuleRuntimeCoordinator(
        store=cast(ModuleReleaseStore, store),
        worker_registry=cast(WorkerRegistry, SimpleNamespace(worker_id="worker-a")),
        runner_deps=cast(AgentRunnerDeps, deps),
        package_roots={"expert-a": package},
    )

    assert await coordinator.sync_once() is True
    assert await coordinator.sync_once() is True
    assert coordinator.ready is True
    assert coordinator.health["applied_version"] == 1
    assert registry.snapshot().version == 1
    assert registry.is_enabled("expert", "expert-a") is False
    assert deps.expert.extension_manager is new_extension
    assert deps.expert.environment_catalog is not None
    assert old_mcp.shutdown_calls == 1
    assert new_mcp.shutdown_calls == 0
    assert build_calls == 1
    assert store.acks == [(1, "worker-a")]


async def test_poll_retries_after_failure_and_stop_cancels_background_tasks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    coordinator = ModuleRuntimeCoordinator(
        store=cast(ModuleReleaseStore, _Store(None)),
        worker_registry=cast(WorkerRegistry, SimpleNamespace(worker_id="worker-a")),
        runner_deps=cast(AgentRunnerDeps, SimpleNamespace()),
        package_roots={},
        poll_interval_seconds=0.01,
    )
    calls = 0

    async def sync_once() -> bool:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("temporary Redis failure")
        return True

    async def subscribe_loop() -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(coordinator, "sync_once", sync_once)
    monkeypatch.setattr(coordinator, "_subscribe_loop", subscribe_loop)

    await coordinator.start()
    for _ in range(100):
        if calls >= 3:
            break
        await asyncio.sleep(0.01)
    await coordinator.stop()

    assert calls >= 3
    assert coordinator.running is False


@pytest.mark.integration
async def test_real_redis_pubsub_triggers_sync_without_waiting_for_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = os.environ.get("PRISMCLAW_TEST_REDIS_URL")
    if not url:
        pytest.skip("PRISMCLAW_TEST_REDIS_URL is required for real Redis integration")
    client = Redis.from_url(url, decode_responses=True)
    await client.ping()
    prefix = f"prismclaw:test:coordinator:{uuid.uuid4().hex}"
    store = ModuleReleaseStore(client, key_prefix=prefix)
    document = create_release_document(
        version=0,
        packages={"expert-a": {"manifest_hash": "a" * 64, "shipped": True}},
        capability_policy={},
        metadata={},
    )
    coordinator = ModuleRuntimeCoordinator(
        store=store,
        worker_registry=cast(WorkerRegistry, SimpleNamespace(worker_id="worker-a")),
        runner_deps=cast(AgentRunnerDeps, SimpleNamespace()),
        package_roots={},
        poll_interval_seconds=60,
    )
    calls = 0

    async def sync_once() -> bool:
        nonlocal calls
        calls += 1
        return True

    monkeypatch.setattr(coordinator, "sync_once", sync_once)
    try:
        await store.publish(document)
        await coordinator.start()
        for _ in range(50):
            subscribers = await client.pubsub_numsub(store.channel)
            if subscribers and int(subscribers[0][1]) > 0:
                break
            await asyncio.sleep(0.01)
        await store.publish(document)
        for _ in range(50):
            if calls >= 2:
                break
            await asyncio.sleep(0.01)
        assert calls >= 2
    finally:
        await coordinator.stop()
        keys = await client.keys(f"{prefix}:*")
        if keys:
            await client.delete(*keys)
        await client.aclose()
