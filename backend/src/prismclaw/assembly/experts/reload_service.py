from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

    from prismclaw.engine.execution.capabilities.extension_manager import ExtensionManager
    from prismclaw.schema.shared import ProjectCapabilityLayout

from prismclaw.app.web.deps import WebDeps
from prismclaw.assembly.experts.expert_lifecycle import (
    ExpertLifecycleManager,
    ExpertRuntimeBundle,
)
from prismclaw.assembly.runtime_coordinator import (
    apply_app_runtime_bundle,
    build_expert_reload_runtime_bundle,
)
from prismclaw.assembly.runtime_coordinator import (
    clear_workspace_runner_deps_cache as _clear_workspace_runner_deps_cache,
)
from prismclaw.assembly.runtime_coordinator import (
    get_app_workspace_extension_manager as _get_app_workspace_extension_manager,
)
from prismclaw.assembly.runtime_coordinator import (
    get_app_workspace_runner_deps as _get_app_workspace_runner_deps,
)
from prismclaw.engine.execution.loop.deps import AgentRunnerDeps
from prismclaw.infra.environment_catalog import EnvironmentCatalog
from prismclaw.infra.settings import Settings
from prismclaw.schema.services.experts import ExpertReloadSummary

logger = logging.getLogger(__name__)


def sync_expert_lifecycle_settings(
    deps: AgentRunnerDeps,
    settings: Settings,
    *,
    invalidate: bool = True,
) -> bool:
    lifecycle = getattr(deps.expert, "lifecycle_manager", None)
    if not isinstance(lifecycle, ExpertLifecycleManager):
        return False
    lifecycle.update_settings(settings, invalidate=invalidate)
    return True


def sync_app_expert_lifecycle_settings(
    app: FastAPI,
    settings: Settings,
    *,
    invalidate: bool = True,
) -> bool:
    runner_deps = getattr(getattr(app, "state", None), "runner_deps", None)
    updated = False
    if isinstance(runner_deps, AgentRunnerDeps):
        updated = sync_expert_lifecycle_settings(
            runner_deps,
            settings,
            invalidate=invalidate,
        )
    _clear_workspace_runner_deps_cache(app)
    return updated


def apply_app_settings(
    app: FastAPI,
    settings: Settings,
    *,
    invalidate_expert_runtime: bool = True,
) -> None:
    state = getattr(app, "state", None)
    if state is None:
        raise RuntimeError("app.state is not available")

    state.settings = settings
    state.web_settings = getattr(getattr(settings, "channels", None), "web", None)

    if invalidate_expert_runtime:
        sync_app_expert_lifecycle_settings(app, settings, invalidate=True)

    runner_deps = getattr(state, "runner_deps", None)
    if runner_deps is not None:
        apply_app_runtime_state(
            app,
            runner_deps,
            settings=settings,
            clear_workspace_cache=False,
        )


def apply_app_runtime_state(
    app: FastAPI,
    runner_deps: AgentRunnerDeps,
    *,
    settings: Settings | None = None,
    clear_workspace_cache: bool = True,
) -> None:
    _state = getattr(app, "state", None)
    fn = getattr(_state, "apply_runtime_state_fn", None)
    if fn is None:
        raise RuntimeError("app.state.apply_runtime_state_fn is not configured")
    fn(
        app,
        runner_deps,
        settings=settings,
        clear_workspace_cache=clear_workspace_cache,
    )


def build_app_web_deps(
    app: FastAPI,
    *,
    settings: Settings | None = None,
    runner_deps: AgentRunnerDeps | None = None,
) -> WebDeps | None:
    _state = getattr(app, "state", None)
    fn = getattr(_state, "build_web_deps_fn", None)
    if not callable(fn):
        raise RuntimeError("app.state.build_web_deps_fn is not configured")
    built: object = fn(
        app,
        settings=settings,
        runner_deps=runner_deps,
    )
    if built is not None and not isinstance(built, WebDeps):
        raise RuntimeError("app.state.build_web_deps_fn 返回了非 WebDeps 对象")
    return built


async def reload_expert_runtime(
    deps: AgentRunnerDeps,
    *,
    settings: Settings | None = None,
) -> ExpertReloadSummary:
    lifecycle = getattr(deps.expert, "lifecycle_manager", None)
    if not isinstance(lifecycle, ExpertLifecycleManager):
        raise RuntimeError("expert lifecycle manager is not available")
    if settings is not None:
        lifecycle.update_settings(settings, invalidate=False)

    old_runtime_mcp_manager = getattr(deps.expert, "runtime_mcp_manager", None)
    bundle = await lifecycle.rebuild()
    _apply_bundle_to_runner_deps(
        deps,
        bundle,
        settings=lifecycle.settings,
        workspace_path=lifecycle.workspace_default_path,
        capability_layout=lifecycle.capability_layout,
    )
    await _shutdown_replaced_runtime_mcp_manager(
        lifecycle,
        old_runtime_mcp_manager,
    )
    return _summarize_bundle(lifecycle, bundle)


async def reload_app_expert_runtime(
    app: FastAPI,
    *,
    settings: Settings | None = None,
) -> ExpertReloadSummary:
    runner_deps = getattr(getattr(app, "state", None), "runner_deps", None)
    if not isinstance(runner_deps, AgentRunnerDeps):
        raise RuntimeError("app runner_deps is not available")

    summary = await reload_expert_runtime(runner_deps, settings=settings)
    effective_settings = settings or getattr(getattr(app, "state", None), "settings", None)
    bundle = await build_expert_reload_runtime_bundle(
        app,
        settings=effective_settings,
        runner_deps=runner_deps,
    )
    apply_app_runtime_bundle(app, bundle)
    return summary


def get_app_workspace_runner_deps(
    app: FastAPI,
    *,
    user_id: str,
    workspace_root: Path,
) -> AgentRunnerDeps | None:
    return _get_app_workspace_runner_deps(
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
    from prismclaw.assembly.runtime_coordinator import (
        get_app_workspace_runner_deps_async as _impl,
    )

    return await _impl(
        app,
        user_id=user_id,
        workspace_root=workspace_root,
        wait_for_ready=wait_for_ready,
    )


def get_app_workspace_extension_manager(
    app: FastAPI,
    *,
    user_id: str,
    workspace_root: Path,
) -> ExtensionManager | None:
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
    from prismclaw.assembly.runtime_coordinator import (
        get_app_workspace_extension_manager_async as _impl,
    )

    return await _impl(
        app,
        user_id=user_id,
        workspace_root=workspace_root,
        wait_for_ready=wait_for_ready,
    )


def _apply_bundle_to_runner_deps(
    deps: AgentRunnerDeps,
    bundle: ExpertRuntimeBundle,
    *,
    settings: Settings,
    workspace_path: Path,
    capability_layout: ProjectCapabilityLayout | None = None,
) -> None:
    """把重建后的 bundle 重新应用到 deps 上，并触发能力刷新。

    capability_layout 优先走 ExpertLifecycleManager.capability_layout 公共
    属性（由调用方传入）—— 它携带项目级 agents/skills/experts 目录的完整
    解析结果，是 SubAgentRegistry 能正确扫描到 ``~/.prismclaw/assets/agents/``
    的唯一可靠来源。若不传，内部会退而从 lifecycle_manager._project_root
    私有属性取 project_root（脆弱兜底：reload 周期里该属性若丢失 → 项目子
    代理回落到 workspace 默认目录下扫描，结果为 0）。
    """
    from prismclaw.runtime.capability_refresh import refresh_runner_capabilities

    deps.expert.extension_manager = bundle.extension_manager
    deps.expert.expert_registry = bundle.expert_registry
    if (
        bundle.expert_registry is not None
        and deps.expert.capability_policy_registry is not None
    ):
        # 目录已整体替换，读侧快照必须指向新注册表，否则 scope 解析会读到旧专家。
        deps.expert.environment_catalog = EnvironmentCatalog(
            expert_registry=bundle.expert_registry,
            capability_policy_registry=deps.expert.capability_policy_registry,
        )
    deps.skill_provider = bundle.skill_provider
    if (
        bundle.runtime_mcp_manager is not None
        or getattr(deps.expert, "runtime_mcp_manager", None) is None
    ):
        deps.expert.runtime_mcp_manager = bundle.runtime_mcp_manager

    # project_root 只在 capability_layout 也没有时才作为兜底
    fallback_project_root = (
        getattr(
            deps.expert.lifecycle_manager,
            "_project_root",
            None,
        )
        if capability_layout is None
        else None
    )

    refresh_runner_capabilities(
        deps,
        settings=settings,
        workspace_path=workspace_path,
        extension_manager=bundle.extension_manager,
        skill_provider=bundle.skill_provider,
        capability_layout=capability_layout,
        project_root=fallback_project_root,
        refresh_mcp_tools=True,
    )

async def _shutdown_replaced_runtime_mcp_manager(
    lifecycle: ExpertLifecycleManager,
    runtime_mcp_manager: object,
) -> None:
    if runtime_mcp_manager is None:
        return

    base_manager = getattr(lifecycle, "_base_mcp_manager", None)
    managers = getattr(runtime_mcp_manager, "_managers", None)
    if isinstance(managers, list):
        seen: set[int] = set()
        for manager in managers:
            if manager is None or manager is base_manager:
                continue
            manager_id = id(manager)
            if manager_id in seen:
                continue
            seen.add(manager_id)
            shutdown = getattr(manager, "shutdown", None)
            if callable(shutdown):
                await shutdown()
        return

    if runtime_mcp_manager is base_manager:
        return

    shutdown = getattr(runtime_mcp_manager, "shutdown", None)
    if callable(shutdown):
        await shutdown()


def _summarize_bundle(
    lifecycle: ExpertLifecycleManager,
    bundle: ExpertRuntimeBundle,
) -> ExpertReloadSummary:
    extension_manager = bundle.extension_manager
    return ExpertReloadSummary(
        expert_count=len(extension_manager.iter_expert_descriptors()),
        command_count=len(extension_manager.iter_expert_command_descriptors()),
        skill_count=len(extension_manager.iter_skill_descriptors()),
        agent_count=len(extension_manager.iter_agent_descriptors()),
        generation=lifecycle.generation,
    )
