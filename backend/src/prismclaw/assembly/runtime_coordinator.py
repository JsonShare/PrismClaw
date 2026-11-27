from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from fastapi import FastAPI

    from prismclaw.app.gateway.worker_registry import WorkerRegistry
    from prismclaw.engine.execution.capabilities.extension_manager import ExtensionManager
    from prismclaw.engine.execution.loop.deps import AgentRunnerDeps
    from prismclaw.engine.experts.registry.expert_registry import ExpertRegistry

from prismclaw.assembly.bundles.expert_reload import (
    build_expert_reload_runtime_bundle,
)
from prismclaw.assembly.bundles.infrastructure import (
    apply_app_infrastructure_bundle,
    build_app_infrastructure_bundle,
)
from prismclaw.assembly.bundles.lifecycle import (
    apply_app_lifecycle_bundle,
    build_app_lifecycle_bundle,
)
from prismclaw.assembly.bundles.runtime import (
    apply_app_runtime_bundle,
    build_app_runtime_bundle,
)
from prismclaw.assembly.bundles_data import (
    AppInfrastructureBundle,
    AppLifecycleBundle,
    AppRuntimeBundle,
)
from prismclaw.assembly.experts.expert_lifecycle import ExpertLifecycleManager
from prismclaw.assembly.lifecycle.shutdown import shutdown_app_runtime
from prismclaw.assembly.wiring.runtime_mode import apply_runtime_mode_state
from prismclaw.assembly.workspace.cache import (
    clear_workspace_runner_deps_cache,
)
from prismclaw.assembly.workspace.cache import (
    get_app_workspace_extension_manager as _get_app_workspace_extension_manager,
)
from prismclaw.assembly.workspace.cache import (
    get_app_workspace_extension_manager_async as _get_app_workspace_extension_manager_async,
)
from prismclaw.assembly.workspace.cache import (
    get_app_workspace_runner_deps as _get_app_workspace_runner_deps,
)
from prismclaw.assembly.workspace.cache import (
    get_app_workspace_runner_deps_async as _get_app_workspace_runner_deps_async,
)
from prismclaw.assembly.workspace.cache import (
    get_app_workspace_runner_deps_state as _get_app_workspace_runner_deps_state,
)
from prismclaw.assembly.workspace.runner_deps import build_workspace_scoped_runner_deps
from prismclaw.infra.config.module_release import (
    ModuleReleaseStore,
    ReleaseDocument,
    validate_local_packages,
)

logger = logging.getLogger(__name__)


def collect_local_package_roots(expert_registry: ExpertRegistry) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for expert in expert_registry.list_experts():
        root = expert.root_path or Path(expert.path)
        if root is None:
            raise RuntimeError(f"deployed expert {expert.expert_id} has no local root")
        roots[expert.expert_id] = Path(root)
    return roots


class ModuleRuntimeCoordinator:
    def __init__(
        self,
        *,
        store: ModuleReleaseStore,
        worker_registry: WorkerRegistry,
        runner_deps: AgentRunnerDeps,
        package_roots: Mapping[str, str | Path],
        poll_interval_seconds: float = 30.0,
        ack_ttl_seconds: int = 90,
    ) -> None:
        self._store = store
        self._worker_registry = worker_registry
        self._deps = runner_deps
        self._package_roots = dict(package_roots)
        self._poll_interval_seconds = poll_interval_seconds
        self._ack_ttl_seconds = ack_ttl_seconds
        self._ready = False
        self._applied_version: int | None = None
        self._target_version: int | None = None
        self._error: str | None = None
        self._sync_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def health(self) -> dict[str, Any]:
        return {
            "ready": self._ready,
            "applied_version": self._applied_version,
            "target_version": self._target_version,
            "error": self._error,
        }

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        await self.sync_once()
        self._running = True
        self._tasks = [
            asyncio.create_task(self._subscribe_loop()),
            asyncio.create_task(self._poll_loop()),
        ]

    async def stop(self) -> None:
        self._running = False
        tasks = self._tasks
        self._tasks = []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._poll_interval_seconds)
            try:
                await self.sync_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("module release poll failed", exc_info=True)

    async def _subscribe_loop(self) -> None:
        while True:
            pubsub = None
            try:
                pubsub = self._store.pubsub()
                await pubsub.subscribe(self._store.channel)
                while True:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=1.0,
                    )
                    if message is not None:
                        await self.sync_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("module release subscription failed; polling remains active")
                await asyncio.sleep(min(1.0, self._poll_interval_seconds))
            finally:
                if pubsub is not None:
                    try:
                        close_pubsub = cast(Callable[[], Awaitable[None]], pubsub.aclose)
                        await close_pubsub()
                    except Exception:
                        logger.debug("module release pubsub close failed", exc_info=True)

    async def sync_once(self) -> bool:
        async with self._sync_lock:
            try:
                document: ReleaseDocument | None = await self._store.fetch_current()
                if document is None:
                    raise RuntimeError("RELEASE_CURRENT_MISSING: no current release")
                self._target_version = document.version
                if self._ready and self._applied_version == document.version:
                    return True
                validate_local_packages(document, self._package_roots)
                if self._applied_version != document.version:
                    await self._apply_document(document)
                await self._store.ack(
                    document.version,
                    self._worker_registry.worker_id,
                    ttl_seconds=self._ack_ttl_seconds,
                )
                self._ready = True
                self._error = None
                return True
            except Exception as exc:
                self._ready = False
                self._error = str(exc)
                return False

    async def _apply_document(self, document: ReleaseDocument) -> None:
        lifecycle = self._deps.expert.lifecycle_manager
        registry = self._deps.expert.capability_policy_registry
        if not isinstance(lifecycle, ExpertLifecycleManager) or registry is None:
            raise RuntimeError("module runtime candidate dependencies are not configured")
        runtime_candidate = await lifecycle.build_candidate()
        bundle = runtime_candidate.bundle
        state_candidate = registry.build_candidate(
            document.capability_policy,
            version=document.version,
            catalog=bundle.expert_registry,
        )
        old_mcp = self._deps.expert.runtime_mcp_manager

        # 无 await 交换段：所有可能失败的候选构建和校验均已在此之前完成。
        lifecycle.commit(runtime_candidate)
        from prismclaw.assembly.experts.reload_service import _apply_bundle_to_runner_deps

        _apply_bundle_to_runner_deps(
            self._deps,
            bundle,
            settings=lifecycle.settings,
            workspace_path=lifecycle.workspace_default_path,
            capability_layout=lifecycle.capability_layout,
        )
        registry.commit(state_candidate)
        self._applied_version = document.version

        from prismclaw.assembly.experts.reload_service import (
            _shutdown_replaced_runtime_mcp_manager,
        )

        try:
            await _shutdown_replaced_runtime_mcp_manager(lifecycle, old_mcp)
        except Exception:
            logger.warning("replaced runtime MCP shutdown failed", exc_info=True)


__all__ = [
    "AppInfrastructureBundle",
    "AppLifecycleBundle",
    "AppRuntimeBundle",
    "ModuleRuntimeCoordinator",
    "apply_app_infrastructure_bundle",
    "apply_app_lifecycle_bundle",
    "apply_app_runtime_bundle",
    "apply_runtime_mode_state",
    "build_app_infrastructure_bundle",
    "build_app_lifecycle_bundle",
    "build_app_runtime_bundle",
    "build_expert_reload_runtime_bundle",
    "clear_workspace_runner_deps_cache",
    "collect_local_package_roots",
    "get_app_workspace_extension_manager",
    "get_app_workspace_extension_manager_async",
    "get_app_workspace_runner_deps_state",
    "get_app_workspace_runner_deps",
    "get_app_workspace_runner_deps_async",
    "shutdown_app_runtime",
]


def get_app_workspace_runner_deps(
    app: FastAPI,
    *,
    user_id: str,
    workspace_root: Path,
) -> AgentRunnerDeps | None:
    """v4：同步入口，仅返回缓存或 base_deps。"""
    return _get_app_workspace_runner_deps(
        app,
        user_id=user_id,
        workspace_root=workspace_root,
    )


def get_app_workspace_runner_deps_state(
    app: FastAPI,
    *,
    user_id: str,
    workspace_root: Path,
) -> str:
    return _get_app_workspace_runner_deps_state(
        app,
        user_id=user_id,
        workspace_root=workspace_root,
    )


async def get_app_workspace_runner_deps_async(
    app: FastAPI,
    *,
    user_id: str,
    workspace_root: Path,
    wait_for_ready: bool = True,
) -> AgentRunnerDeps | None:
    """v4：异步入口，触发 workspace-scoped 构建。"""
    return await _get_app_workspace_runner_deps_async(
        app,
        user_id=user_id,
        workspace_root=workspace_root,
        build_scoped_runner_deps=build_workspace_scoped_runner_deps,
        wait_for_ready=wait_for_ready,
    )


def get_app_workspace_extension_manager(
    app: FastAPI,
    *,
    user_id: str,
    workspace_root: Path,
) -> ExtensionManager | None:
    """v4：同步入口，仅返回缓存或基础 extension_manager。"""
    return _get_app_workspace_extension_manager(
        app,
        user_id=user_id,
        workspace_root=workspace_root,
    )


async def get_app_workspace_extension_manager_async(
    app: FastAPI,
    *,
    user_id: str,
    workspace_root: Path,
    wait_for_ready: bool = True,
) -> ExtensionManager | None:
    """v4：异步入口，触发 workspace-scoped 构建。"""
    return await _get_app_workspace_extension_manager_async(
        app,
        user_id=user_id,
        workspace_root=workspace_root,
        wait_for_ready=wait_for_ready,
    )
