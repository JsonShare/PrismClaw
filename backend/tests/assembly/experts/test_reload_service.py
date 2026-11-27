from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest

from prismclaw.assembly.experts.expert_lifecycle import ExpertLifecycleManager, ExpertRuntimeBundle
from prismclaw.assembly.experts.reload_service import reload_expert_runtime
from prismclaw.engine.execution.loop.deps import AgentRunnerDeps
from prismclaw.engine.experts.registry.expert_registry import ExpertRegistry
from prismclaw.infra.settings import Settings
from prismclaw.schema.shared import ProjectCapabilityLayout


class _MCPManager:
    def __init__(self, events: list[str] | None = None) -> None:
        self.shutdown_calls = 0
        self._events = events

    async def shutdown(self) -> None:
        self.shutdown_calls += 1
        if self._events is not None:
            self._events.append("shutdown-old-mcp")


class _ExtensionManager:
    def iter_expert_descriptors(self) -> list[object]:
        return []

    def iter_expert_command_descriptors(self) -> list[object]:
        return []

    def iter_skill_descriptors(self) -> list[object]:
        return []

    def iter_agent_descriptors(self) -> list[object]:
        return []


def _bare_lifecycle() -> ExpertLifecycleManager:
    lifecycle = object.__new__(ExpertLifecycleManager)
    lifecycle._settings = cast(Settings, SimpleNamespace())
    lifecycle._base_mcp_manager = None
    lifecycle._generation = 0
    lifecycle._bundle = None
    lifecycle._workspace_default = "."
    lifecycle._injected_layout = cast(ProjectCapabilityLayout | None, SimpleNamespace())
    lifecycle._cached_layout = None
    return lifecycle


async def test_failed_candidate_keeps_old_runtime_and_mcp_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = _bare_lifecycle()
    old_mcp = _MCPManager()
    old_extension = object()
    deps = SimpleNamespace(
        expert=SimpleNamespace(
            environment_catalog=MagicMock(),
            lifecycle_manager=lifecycle,
            runtime_mcp_manager=old_mcp,
            extension_manager=old_extension,
        )
    )

    async def fail_rebuild() -> None:
        raise RuntimeError("candidate is invalid")

    monkeypatch.setattr(lifecycle, "rebuild", fail_rebuild)

    with pytest.raises(RuntimeError, match="candidate is invalid"):
        await reload_expert_runtime(cast(AgentRunnerDeps, deps))

    assert old_mcp.shutdown_calls == 0
    assert deps.expert.extension_manager is old_extension
    assert deps.expert.runtime_mcp_manager is old_mcp


async def test_successful_candidate_swaps_full_bundle_before_closing_old_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    lifecycle = _bare_lifecycle()
    old_mcp = _MCPManager(events)
    new_mcp = _MCPManager()
    new_extension = _ExtensionManager()
    new_expert_registry = object()
    candidate = ExpertRuntimeBundle(
        extension_manager=new_extension,
        skill_provider=object(),
        runtime_mcp_manager=new_mcp,
        expert_registry=cast(ExpertRegistry, new_expert_registry),
    )
    deps = SimpleNamespace(
        expert=SimpleNamespace(
            environment_catalog=MagicMock(),
            lifecycle_manager=lifecycle,
            runtime_mcp_manager=old_mcp,
            extension_manager=object(),
            expert_registry=object(),
            capability_policy_registry=MagicMock(),
        ),
        skill_provider=None,
        hook_dispatcher=None,
    )

    async def rebuild() -> ExpertRuntimeBundle:
        events.append("build-candidate")
        lifecycle._bundle = candidate
        lifecycle._generation += 1
        return candidate

    def refresh(*args: object, **kwargs: object) -> None:
        events.append("apply-candidate")

    monkeypatch.setattr(lifecycle, "rebuild", rebuild)
    monkeypatch.setattr("prismclaw.runtime.capability_refresh.refresh_runner_capabilities", refresh)

    await reload_expert_runtime(cast(AgentRunnerDeps, deps))

    assert events == ["build-candidate", "apply-candidate", "shutdown-old-mcp"]
    assert deps.expert.extension_manager is new_extension
    assert deps.expert.runtime_mcp_manager is new_mcp
    # 目录已替换，读侧快照必须指向新注册表，否则热更新后仍解析旧专家。
    assert deps.expert.environment_catalog.snapshot().expert_registry is new_expert_registry
    assert old_mcp.shutdown_calls == 1
    assert new_mcp.shutdown_calls == 0


async def test_lifecycle_candidate_does_not_change_cached_bundle_until_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lifecycle = _bare_lifecycle()
    old_bundle = ExpertRuntimeBundle(extension_manager=object(), skill_provider=object())
    new_bundle = ExpertRuntimeBundle(extension_manager=object(), skill_provider=object())
    lifecycle._bundle = old_bundle
    lifecycle._generation = 3

    async def build() -> ExpertRuntimeBundle:
        return new_bundle

    monkeypatch.setattr(lifecycle, "_build_bundle", build)

    candidate = await lifecycle.build_candidate()
    assert lifecycle._bundle is old_bundle
    assert lifecycle.generation == 3

    committed = lifecycle.commit(candidate)
    assert committed is new_bundle
    assert lifecycle._bundle is new_bundle
    assert lifecycle.generation == 4
    assert await lifecycle.get_bundle() is new_bundle
