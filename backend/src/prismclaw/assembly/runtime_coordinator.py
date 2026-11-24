from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

    from prismclaw.engine.execution.capabilities.extension_manager import ExtensionManager
    from prismclaw.engine.execution.loop.deps import AgentRunnerDeps

from prismclaw.assembly.bundles.infrastructure import (
    apply_app_infrastructure_bundle,
    build_app_infrastructure_bundle,
)
from prismclaw.assembly.bundles.lifecycle import (
    apply_app_lifecycle_bundle,
    build_app_lifecycle_bundle,
)
from prismclaw.assembly.bundles.plugin_reload import (
    build_plugin_reload_runtime_bundle,
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

__all__ = [
    "AppInfrastructureBundle",
    "AppLifecycleBundle",
    "AppRuntimeBundle",
    "apply_app_infrastructure_bundle",
    "apply_app_lifecycle_bundle",
    "apply_app_runtime_bundle",
    "apply_runtime_mode_state",
    "build_app_infrastructure_bundle",
    "build_app_lifecycle_bundle",
    "build_app_runtime_bundle",
    "build_plugin_reload_runtime_bundle",
    "clear_workspace_runner_deps_cache",
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
