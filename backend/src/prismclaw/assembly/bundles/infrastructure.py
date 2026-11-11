from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from prismclaw.assembly.bundles_data import AppInfrastructureBundle
from prismclaw.engine.runtime.mode import redis_is_required
from prismclaw.infra.settings import Settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, AsyncSession

logger = logging.getLogger(__name__)


async def build_app_infrastructure_bundle(
    *,
    settings: Settings,
    sandbox_policy: Any,
    db_engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    get_client_fn: Callable[[Any], Awaitable[Any]],
    lock_manager_factory: Callable[..., Any],
    session_store_factory: Callable[..., Any],
    task_manager_factory: Callable[..., Any],
    worker_registry_factory: Callable[..., Any],
    generate_worker_id_fn: Callable[[], str],
    worker_heartbeat_loop: Callable[[Any], Awaitable[None]],
    create_workspace_store_fn: Callable[..., Any],
    create_memory_store_fn: Callable[..., Any] | None = None,
    dream_dispatch_queue_factory: Callable[..., Any] | None = None,
    mcp_manager_factory: Callable[..., Any] | None = None,
    run_ownership_factory: Callable[..., Any] | None = None,
) -> AppInfrastructureBundle:
    if sandbox_policy is None:
        raise RuntimeError(
            "build_app_infrastructure_bundle requires a resolved sandbox_policy"
        )
    redis_client = None
    try:
        redis_client = await get_client_fn(settings.redis)
    except Exception:
        if redis_is_required(settings):
            raise
        logger.warning("redis unavailable; continuing in fallback mode", exc_info=True)
    lock_manager = lock_manager_factory(
        redis_client,
        key_prefix=settings.redis.key_prefix,
    )
    session_store = session_store_factory(
        redis_client,
        lock_manager,
        ttl_seconds=settings.redis.ttl_seconds,
    )
    run_snapshot_store = None
    if session_factory is not None:
        from prismclaw.persistence.pg_checkpoint.factory import create_run_snapshot_store
        from prismclaw.engine.execution.checkpoint.persistence import parse_run_snapshot_json
        run_snapshot_store = create_run_snapshot_store(session_factory, payload_parser=parse_run_snapshot_json)
    run_ownership_store = None
    if settings.affinity.enabled and redis_client is not None:
        if run_ownership_factory is not None:
            run_ownership_store = run_ownership_factory(redis_client, settings)
    task_manager = task_manager_factory(
        default_shutdown_grace_s=float(settings.shutdown_grace_seconds),
    )

    worker_id = generate_worker_id_fn()
    worker_registry = worker_registry_factory(
        redis_client=redis_client if settings.affinity.enabled else None,
        worker_id=worker_id,
        heartbeat_interval=settings.affinity.heartbeat_interval,
    )
    if getattr(worker_registry, "available", False):
        await worker_registry.register()
        task_manager.spawn(
            "worker-heartbeat",
            worker_heartbeat_loop(worker_registry),
            category="heartbeat",
        )
        logger.info("Worker registered: %s", worker_id)

    dream_dispatch_queue = None
    if (
        dream_dispatch_queue_factory is not None
        and settings.evolution.enabled
        and settings.evolution.dream.enabled
        and redis_client is not None
    ):
        dream_dispatch_queue = dream_dispatch_queue_factory(
            redis_client,
            key_prefix=f"{settings.redis.key_prefix}dream:dispatch:",
        )

    workspace_store = create_workspace_store_fn(settings, redis_client=redis_client)
    workspace_base = Path(settings.workspaces.default).expanduser()

    memory_store = None
    if create_memory_store_fn is not None:
        memory_store = create_memory_store_fn(
            db_engine,
            settings.memory,
            settings.embedding,
            redis_client,
        )

    mcp_manager = None
    mcp_death_handler = None
    external_tool_registrar = None
    if mcp_manager_factory is not None and settings.mcp.enabled:
        mcp_manager = mcp_manager_factory(settings.mcp, task_manager=task_manager)
        mcp_manager.set_sandbox_policy(sandbox_policy)
        mcp_manager._emit_sandbox_startup_advisories()
        mcp_manager.start_background()
        mcp_death_handler = mcp_manager._handle_server_death
        external_tool_registrar = mcp_manager.attach_and_register

    return AppInfrastructureBundle(
        redis_client=redis_client,
        lock_manager=lock_manager,
        session_store=session_store,
        run_snapshot_store=run_snapshot_store,
        run_ownership_store=run_ownership_store,
        task_manager=task_manager,
        worker_registry=worker_registry,
        worker_id=worker_id,
        dream_dispatch_queue=dream_dispatch_queue,
        workspace_store=workspace_store,
        workspace_base=workspace_base,
        memory_store=memory_store,
        mcp_manager=mcp_manager,
        mcp_death_handler=mcp_death_handler,
        external_tool_registrar=external_tool_registrar,
    )


def apply_app_infrastructure_bundle(app: Any, bundle: AppInfrastructureBundle) -> None:
    state = getattr(app, "state", None)
    if state is None:
        raise RuntimeError("app.state is not available")

    state.redis_client = bundle.redis_client
    state.session_store = bundle.session_store
    state.run_snapshot_store = bundle.run_snapshot_store
    state.run_ownership_store = bundle.run_ownership_store
    state.task_manager = bundle.task_manager
    state.worker_registry = bundle.worker_registry
    state.dream_dispatch_queue = bundle.dream_dispatch_queue
    state.workspace_store = bundle.workspace_store
    state.workspace_base = bundle.workspace_base
    state.memory_store = bundle.memory_store
    state.mcp_manager = bundle.mcp_manager
