from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from prismclaw.schema.services.plugins import PluginReloadSummary
from prismclaw.assembly.plugins.plugin_lifecycle import (
    PluginLifecycleManager,
    PluginRuntimeBundle,
)
from prismclaw.engine.execution.loop.bootstrap import AgentRunnerDeps
from prismclaw.assembly.runtime_coordinator import (
    apply_app_runtime_bundle,
    build_plugin_reload_runtime_bundle,
    clear_workspace_runner_deps_cache as _clear_workspace_runner_deps_cache,
    get_app_workspace_extension_manager as _get_app_workspace_extension_manager,
    get_app_workspace_runner_deps as _get_app_workspace_runner_deps,
)
from prismclaw.infra.settings import Settings

logger = logging.getLogger(__name__)


def sync_plugin_lifecycle_settings(
    deps: AgentRunnerDeps,
    settings: Settings,
    *,
    invalidate: bool = True,
) -> bool:
    lifecycle = getattr(deps.plugin, "lifecycle_manager", None)
    if not isinstance(lifecycle, PluginLifecycleManager):
        return False
    lifecycle.update_settings(settings, invalidate=invalidate)
    return True


def sync_app_plugin_lifecycle_settings(
    app: Any,
    settings: Settings,
    *,
    invalidate: bool = True,
) -> bool:
    runner_deps = getattr(getattr(app, "state", None), "runner_deps", None)
    updated = False
    if isinstance(runner_deps, AgentRunnerDeps):
        updated = sync_plugin_lifecycle_settings(
            runner_deps,
            settings,
            invalidate=invalidate,
        )
    _clear_workspace_runner_deps_cache(app)
    return updated


def apply_app_settings(
    app: Any,
    settings: Settings,
    *,
    invalidate_plugin_runtime: bool = True,
) -> None:
    state = getattr(app, "state", None)
    if state is None:
        raise RuntimeError("app.state is not available")

    state.settings = settings
    state.web_settings = getattr(getattr(settings, "channels", None), "web", None)

    if invalidate_plugin_runtime:
        sync_app_plugin_lifecycle_settings(app, settings, invalidate=True)

    runner_deps = getattr(state, "runner_deps", None)
    if runner_deps is not None:
        apply_app_runtime_state(
            app,
            runner_deps,
            settings=settings,
            clear_workspace_cache=False,
        )


def apply_app_runtime_state(
    app: Any,
    runner_deps: Any,
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
    app: Any,
    *,
    settings: Settings | None = None,
    runner_deps: Any | None = None,
) -> Any | None:
    _state = getattr(app, "state", None)
    fn = getattr(_state, "build_web_deps_fn", None)
    if fn is None:
        raise RuntimeError("app.state.build_web_deps_fn is not configured")
    return fn(
        app,
        settings=settings,
        runner_deps=runner_deps,
    )


async def reload_plugin_runtime(
    deps: AgentRunnerDeps,
    *,
    settings: Settings | None = None,
) -> PluginReloadSummary:
    lifecycle = getattr(deps.plugin, "lifecycle_manager", None)
    if not isinstance(lifecycle, PluginLifecycleManager):
        raise RuntimeError("plugin lifecycle manager is not available")
    if settings is not None:
        lifecycle.update_settings(settings, invalidate=False)

    await _shutdown_replaced_runtime_mcp_manager(
        lifecycle,
        getattr(deps.plugin, "runtime_mcp_manager", None),
    )
    bundle = await lifecycle.rebuild()
    _apply_bundle_to_runner_deps(
        deps,
        bundle,
        settings=lifecycle.settings,
        workspace_path=lifecycle.workspace_default_path,
        capability_layout=lifecycle.capability_layout,
    )
    return _summarize_bundle(lifecycle, bundle)


async def reload_app_plugin_runtime(
    app: Any,
    *,
    settings: Settings | None = None,
) -> PluginReloadSummary:
    runner_deps = getattr(getattr(app, "state", None), "runner_deps", None)
    if not isinstance(runner_deps, AgentRunnerDeps):
        raise RuntimeError("app runner_deps is not available")

    summary = await reload_plugin_runtime(runner_deps, settings=settings)
    effective_settings = settings or getattr(getattr(app, "state", None), "settings", None)
    bundle = await build_plugin_reload_runtime_bundle(
        app,
        settings=effective_settings,
        runner_deps=runner_deps,
    )
    apply_app_runtime_bundle(app, bundle)
    return summary


def get_app_workspace_runner_deps(
    app: Any,
    *,
    user_id: str,
    workspace_root: Path,
) -> Any | None:
    return _get_app_workspace_runner_deps(
        app,
        user_id=user_id,
        workspace_root=workspace_root,
    )


async def get_app_workspace_runner_deps_async(
    app: Any,
    *,
    user_id: str,
    workspace_root: Path,
    wait_for_ready: bool = True,
) -> Any | None:
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
    app: Any,
    *,
    user_id: str,
    workspace_root: Path,
) -> Any | None:
    return _get_app_workspace_extension_manager(
        app,
        user_id=user_id,
        workspace_root=workspace_root,
    )


async def get_app_workspace_extension_manager_async(
    app: Any,
    *,
    user_id: str,
    workspace_root: Path,
    wait_for_ready: bool = True,
) -> Any | None:
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
    bundle: PluginRuntimeBundle,
    *,
    settings: Settings,
    workspace_path: Path,
    capability_layout: Any = None,
) -> None:
    """把重建后的 bundle 重新应用到 deps 上，并触发能力刷新。

    capability_layout 优先走 PluginLifecycleManager.capability_layout 公共
    属性（由调用方传入）—— 它携带项目级 agents/skills/plugins 目录的完整
    解析结果，是 SubAgentRegistry 能正确扫描到 ``~/.prismclaw/assets/agents/``
    的唯一可靠来源。若不传，内部会退而从 lifecycle_manager._project_root
    私有属性取 project_root（脆弱兜底：reload 周期里该属性若丢失 → 项目子
    代理回落到 workspace 默认目录下扫描，结果为 0）。
    """
    from prismclaw.runtime.capability_refresh import refresh_runner_capabilities

    deps.plugin.extension_manager = bundle.extension_manager
    deps.skill_provider = bundle.skill_provider
    deps.hook_dispatcher = bundle.hook_dispatcher
    if bundle.runtime_mcp_manager is not None or getattr(deps.plugin, "runtime_mcp_manager", None) is None:
        deps.plugin.runtime_mcp_manager = bundle.runtime_mcp_manager

    # project_root 只在 capability_layout 也没有时才作为兜底
    fallback_project_root = getattr(
        deps.plugin.lifecycle_manager,
        "_project_root",
        None,
    ) if capability_layout is None else None

    _ = refresh_runner_capabilities(
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
    lifecycle: PluginLifecycleManager,
    runtime_mcp_manager: Any,
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
    lifecycle: PluginLifecycleManager,
    bundle: PluginRuntimeBundle,
) -> PluginReloadSummary:
    extension_manager = bundle.extension_manager
    runtime_views = extension_manager.iter_plugin_runtime_capabilities()
    if runtime_views:
        return PluginReloadSummary(
            plugin_count=len(runtime_views),
            command_count=sum(len(runtime.commands) for runtime in runtime_views),
            skill_count=sum(len(runtime.skills) for runtime in runtime_views),
            agent_count=sum(len(runtime.agents) for runtime in runtime_views),
            generation=lifecycle.generation,
        )
    graphs = extension_manager.iter_plugin_capability_graphs()
    return PluginReloadSummary(
        plugin_count=len(graphs),
        command_count=sum(len(graph.command_capabilities) for graph in graphs),
        skill_count=sum(len(graph.skill_capabilities) for graph in graphs),
        agent_count=sum(len(graph.agent_capabilities) for graph in graphs),
        generation=lifecycle.generation,
    )
