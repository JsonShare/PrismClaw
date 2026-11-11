from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable

from prismclaw.infra.settings import Settings

from prismclaw.assembly.bundles_data import AppInfrastructureBundle, AppRuntimeBundle


async def build_app_runtime_bundle(
    app: Any,
    *,
    settings: Settings,
    infra: AppInfrastructureBundle,
    project_root: Path | str | None,
    dream_health_loop: Callable[..., Awaitable[None]],
    dream_health_interval_s: float,
    db_engine: Any = None,
) -> AppRuntimeBundle:
    from prismclaw.assembly.workspace.runner_assembly import create_agent_runner_deps

    runner_deps = await create_agent_runner_deps(
        settings,
        infra.session_store,
        run_snapshot_store=infra.run_snapshot_store,
        workspace_store=infra.workspace_store,
        task_manager=infra.task_manager,
        memory_store=infra.memory_store,
        redis_client=infra.redis_client,
        lock_manager=infra.lock_manager,
        mcp_death_handler=infra.mcp_death_handler,
        external_tool_registrar=infra.external_tool_registrar,
        mcp_manager=infra.mcp_manager,
        worker_registry=infra.worker_registry,
        dream_dispatch_queue=infra.dream_dispatch_queue,
        session_factory=getattr(app.state, "session_factory", None),
        trajectory_persistence=getattr(app.state, "trajectory_persistence", None),
        project_root=project_root,
        db_engine=db_engine,
    )
    _state = getattr(app, "state", None)
    _apply_fn = getattr(_state, "apply_runtime_state_fn", None)
    if _apply_fn is None:
        raise RuntimeError("app.state.apply_runtime_state_fn is not configured")
    _apply_fn(
        app,
        runner_deps,
        settings=settings,
        clear_workspace_cache=False,
    )

    # Dream dispatch（运行时服务已迁移至 runtime.dream_starter）
    from prismclaw.runtime.dream_starter import start_dream_dispatch_if_available

    dream_result = await start_dream_dispatch_if_available(
        app=app,
        runner_deps=runner_deps,
        infra=infra,
        settings=settings,
        dream_health_loop=dream_health_loop,
        dream_health_interval_s=dream_health_interval_s,
    )
    dream_dispatch_consumer = dream_result.dream_dispatch_consumer
    dream_dispatch_health_task_id = dream_result.dream_dispatch_health_task_id

    web_deps = None
    if settings.channels.web.enabled:
        _build_deps_fn = getattr(_state, "build_web_deps_fn", None)
        if _build_deps_fn is None:
            raise RuntimeError("app.state.build_web_deps_fn is not configured")
        web_deps = _build_deps_fn(
            app,
            settings=settings,
            runner_deps=runner_deps,
        )
        if _state is not None:
            _state.web_deps = web_deps
        _sync_fn = getattr(_state, "sync_web_state_fn", None)
        if _sync_fn is not None:
            _sync_fn(app, runner_deps, settings=settings)

    return AppRuntimeBundle(
        runner_deps=runner_deps,
        web_deps=web_deps,
        dream_dispatch_consumer=dream_dispatch_consumer,
        dream_dispatch_health_task_id=dream_dispatch_health_task_id,
    )


def apply_app_runtime_bundle(app: Any, bundle: AppRuntimeBundle) -> None:
    state = getattr(app, "state", None)
    if state is None:
        raise RuntimeError("app.state is not available")

    state.runner_deps = bundle.runner_deps
    state.web_deps = bundle.web_deps
    state.dream_dispatch_consumer = bundle.dream_dispatch_consumer
    state.dream_dispatch_health_task_id = bundle.dream_dispatch_health_task_id