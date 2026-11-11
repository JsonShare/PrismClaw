"""Application lifespan management — startup/shutdown orchestration."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from fastapi import FastAPI

logger = logging.getLogger(__name__)
_DREAM_DISPATCH_HEALTH_LOG_INTERVAL_S = 60.0


def _derive_project_root(config_file: Path | None) -> Path:
    """薄语义层：保持原名（外部 grep 可定位），底层走分层解析。"""
    from prismclaw.infra.paths import resolve_project_root

    return resolve_project_root(hint=config_file)


def _publish_capability_layout(app: FastAPI, runner_deps: Any) -> None:
    """从冷启动 plugin lifecycle 取出唯一能力布局：挂到 app.state 并聚合记录日志。

    布局是驱动冷启动 skills/plugins/agents 发现的同一份对象；health 端点直接
    读取它，启动日志只在此处汇总一次（诊断在 layout builder 唯一点产生）。
    """
    from prismclaw.schema.shared import DiagnosticLevel

    lifecycle = getattr(getattr(runner_deps, "plugin", None), "lifecycle_manager", None)
    layout = getattr(lifecycle, "capability_layout", None)
    app.state.capability_layout = layout
    if layout is None:
        return

    summary = layout.summary_dict()
    logger.info(
        "capability layout: roots=%s | existing skills=%d plugins=%d agents=%d | diagnostics=%d",
        [f"{root['source']}:{root['path']}" for root in summary["roots"]],
        len(summary["skills"]["existing"]),
        len(summary["plugins"]["existing"]),
        len(summary["agents"]["existing"]),
        len(summary["diagnostics"]),
    )
    for diag in layout.diagnostics:
        if diag.level is DiagnosticLevel.ERROR:
            logger.error("capability layout [%s] %s: %s", diag.level.value, diag.code, diag.message)
        elif diag.level is DiagnosticLevel.WARN:
            logger.warning("capability layout [%s] %s: %s", diag.level.value, diag.code, diag.message)
        else:
            logger.info("capability layout [%s] %s: %s", diag.level.value, diag.code, diag.message)


async def _init_user_store(app: FastAPI, settings: Any, redis_client: Any, session_factory: Any) -> None:
    """Build the DB-backed web user store and register it as the default.

    PostgreSQL only, using the application-wide shared session_factory. On first run
    the table is seeded from config / bootstrap env vars (legacy plaintext
    passwords are re-hashed). If init fails (unreachable DB, etc.) we fall back
    to the legacy config-list store so the web channel still starts — but auth
    will not persist changes and an operator should fix the DB backend.
    """
    from prismclaw.app.auth.handler_helpers import (
        _build_default_store,
        register_default_user_profile_store,
    )

    web = settings.channels.web
    app.state.user_repository = None
    app.state.user_profile_store = None

    # Web disabled or user store explicitly off → config-based fallback store.
    if not web.enabled or not web.user_store.enabled:
        store = _build_default_store(
            channel="web", redis_client=redis_client, user_configs=web.users
        )
        register_default_user_profile_store(store)
        app.state.user_profile_store = store
        return

    try:
        from prismclaw.persistence.user.models import init_schema
        from prismclaw.persistence.user.factory import create_user_store, create_user_profile_store
        from prismclaw.persistence.user.seed import seed_from_config

        engine = app.state.db_engine
        await init_schema(engine)
        repo = create_user_store(
            session_factory,
            lockout_threshold=web.user_store.lockout_threshold,
            lockout_window_seconds=web.user_store.lockout_window_seconds,
            scheme=web.user_store.password_scheme,
        )
        summary = await seed_from_config(
            repo,
            web,
            bootstrap_admin_id=web.user_store.bootstrap_admin_id,
            bootstrap_admin_password=web.user_store.bootstrap_admin_password,
        )
        logger.info(
            "user store initialised (backend=postgresql, seeded=%s, created=%s)",
            summary.get("seeded"),
            summary.get("created"),
        )
        store = create_user_profile_store(repo)
        register_default_user_profile_store(store)
        app.state.user_repository = repo
        app.state.user_profile_store = store
        # web_deps is built before _init_user_store runs; sync the repo into
        # the cached WebDeps so get_web_deps(request).user_repository is non-None.
        if getattr(app.state, "web_deps", None) is not None:
            app.state.web_deps.user_repository = repo
    except Exception:
        logger.exception(
            "user store init failed (DB unreachable); falling back to config-based "
            "users (auth uses backend/configs/server.yaml and will not persist changes)."
        )
        store = _build_default_store(
            channel="web", redis_client=redis_client, user_configs=web.users
        )
        register_default_user_profile_store(store)
        app.state.user_profile_store = store


async def _startup_health_gate(
    redis_client: Any,
    db_engine: Any,
    *,
    max_attempts: int = 15,
    interval_seconds: float = 2.0,
) -> None:
    """Verify Redis and Postgres reachability before spawning background tasks.

    Waits up to ``max_attempts * interval_seconds`` for Redis + Postgres
    to become reachable (handles Docker Compose start-order races).
    Hard-fails with a clear message if both are still unreachable.

    Redis is required for scheduler dispatch, curator, dream, and session
    affinity.  Postgres is required for user store, memory, scheduler
    state, and dream state.
    """
    from prismclaw.infra.redis import ping

    if redis_client is None:
        raise RuntimeError(
            "Redis is not configured. Redis is required — aborting startup."
        )
    if db_engine is None:
        raise RuntimeError(
            "PostgreSQL engine not initialised. Aborting startup."
        )

    last_redis_err = last_pg_err = None
    for attempt in range(1, max_attempts + 1):
        redis_ok = False
        pg_ok = False

        try:
            redis_ok = await ping(redis_client)
        except Exception as exc:
            last_redis_err = exc

        try:
            from sqlalchemy import text
            async with db_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            pg_ok = True
        except Exception as exc:
            last_pg_err = exc

        if redis_ok and pg_ok:
            logger.info(
                "Startup health gate passed after %d attempt(s): "
                "Redis + PostgreSQL reachable.",
                attempt,
            )
            return

        if attempt < max_attempts:
            parts = []
            if not redis_ok:
                parts.append("Redis unreachable")
            if not pg_ok:
                parts.append("PostgreSQL unreachable")
            logger.warning(
                "Startup health gate attempt %d/%d: %s — retrying in %.0fs",
                attempt, max_attempts, " + ".join(parts), interval_seconds,
            )
            await asyncio.sleep(interval_seconds)

    raise RuntimeError(
        f"Startup health gate failed after {max_attempts} attempts. "
        f"{'Redis' if not redis_ok else 'PostgreSQL'} is unreachable. "
        f"Verify services are running. "
        f"Last Redis error: {last_redis_err}. "
        f"Last PostgreSQL error: {last_pg_err}."
    )


async def _worker_heartbeat_loop(registry) -> None:
    try:
        while True:
            await asyncio.sleep(registry._heartbeat_interval)
            try:
                await registry.heartbeat()
            except Exception:
                logger.warning("Worker heartbeat failed", exc_info=True)
    except asyncio.CancelledError:
        return


async def _dream_dispatch_health_loop(
    dream_ops: Any,
    *,
    interval_s: float = _DREAM_DISPATCH_HEALTH_LOG_INTERVAL_S,
) -> None:
    try:
        while True:
            await _log_dream_dispatch_health(dream_ops)
            await asyncio.sleep(max(5.0, float(interval_s)))
    except asyncio.CancelledError:
        return


async def _log_dream_dispatch_health(dream_ops: Any) -> None:
    try:
        status = await dream_ops.get_dispatch_status()
    except Exception:
        logger.warning("dream dispatch health check failed", exc_info=True)
        return
    summary = status.get("summary") if isinstance(status, dict) else {}
    session_health = status.get("session_health") if isinstance(status, dict) else {}
    dead_letter_metrics = status.get("dead_letter_metrics") if isinstance(status, dict) else {}
    summary = summary if isinstance(summary, dict) else {}
    session_health = session_health if isinstance(session_health, dict) else {}
    dead_letter_metrics = dead_letter_metrics if isinstance(dead_letter_metrics, dict) else {}
    health_status = str(summary.get("health_status", "unknown") or "unknown")
    log_fn = logger.debug
    if health_status in {"degraded", "unavailable"}:
        log_fn = logger.warning
    log_fn(
        "dream dispatch health status=%s available=%s backlog=%s dead_letters=%s "
        "blocked_replays=%s impacted_sessions=%s blocked_sessions=%s latest_error=%s",
        health_status,
        bool(status.get("available")) if isinstance(status, dict) else False,
        int(summary.get("queue_backlog", 0) or 0),
        int(dead_letter_metrics.get("total_count", 0) or 0),
        int(summary.get("blocked_replay_count", 0) or 0),
        int(session_health.get("impacted_session_count", 0) or 0),
        int(session_health.get("blocked_session_count", 0) or 0),
        dead_letter_metrics.get("latest_error"),
    )


async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    from prismclaw.infra.db import create_shared_engine
    from prismclaw.infra.redis import close_client, get_client
    from prismclaw.infra.settings import find_config_file, load_settings
    from prismclaw.infra.task_manager import TaskManager
    from prismclaw.persistence.lock.factory import create_lock_manager
    from prismclaw.persistence.session.factory import create_session_store
    from prismclaw.persistence.workspace.factory import create_workspace_store

    settings = load_settings()
    project_root = _derive_project_root(find_config_file())
    app.state.project_root = project_root

    # ── 启动同步：backend/.prismclaw → ~/.prismclaw ──────────────────────
    # SKILL.md / PRISMCLAW.md 全写 ~/.prismclaw 路径，repo 里权威源在
    # backend/.prismclaw/；运行时代码和文档统一读 ~/.prismclaw，启动时
    # 把静态能力目录（skills/plugins/agents）同步过去，运行时数据保留。
    try:
        from prismclaw.infra.prismclaw_home import sync_prismclaw_home
        sync_prismclaw_home(project_root)
    except Exception:
        logger.exception("sync_prismclaw_home failed; continuing startup")

    # 清理 workspace 根的旧目录（uploads/outputs/context_blocks/tool_results/tmp）
    try:
        from prismclaw.infra.prismclaw_home import cleanup_stale_workspace_dirs
        cleanup_stale_workspace_dirs()
    except Exception:
        logger.exception("cleanup_stale_workspace_dirs failed; continuing startup")

    # Shared PostgreSQL engine — used by user store, memory store, scheduler,
    # dream, curator, and context audit subsystems.
    app.state.db_engine = create_shared_engine(settings.storage)

    # Shared session_factory — created once, consumed by all persistence
    # factory functions. Replaces per-site `make_session_factory(db_engine)`.
    from sqlalchemy.ext.asyncio import async_sessionmaker
    session_factory = async_sessionmaker(app.state.db_engine, expire_on_commit=False)
    app.state.session_factory = session_factory

    from prismclaw.runtime.facade.security import resolve_sandbox_state
    from prismclaw.schema.config.config import SandboxConfig

    sandbox_config = SandboxConfig(
        enforce=settings.sandbox.enforce,
        default_filesystem=settings.sandbox.default_filesystem.model_dump(),
        default_network=settings.sandbox.default_network.model_dump(),
        default_env_allowlist=list(settings.sandbox.default_env_allowlist),
    )
    sandbox_state = resolve_sandbox_state(sandbox_config)
    app.state.sandbox_state = sandbox_state
    app.state.sandbox_policy = sandbox_state.policy
    if sandbox_state.warning:
        logger.warning("sandbox: %s", sandbox_state.warning)

    # Wire app-level callbacks for assembly to invoke (avoids assembly→app imports)
    from prismclaw.app.state.runtime_state import apply_app_runtime_state
    from prismclaw.app.state.web_state import sync_web_runtime_state
    from prismclaw.app.state.deps import build_app_web_deps
    from prismclaw.app.state.web_globals import (
        reset_web_channel_runtime_globals,
        shutdown_web_channel_runtime_globals,
    )
    from prismclaw.app.gateway.takeover import auto_takeover_loop
    from prismclaw.app.gateway.factory import (
        build_gateway_components,
        build_run_ownership_store,
    )

    app.state.apply_runtime_state_fn = apply_app_runtime_state
    app.state.sync_web_state_fn = sync_web_runtime_state
    app.state.build_web_deps_fn = build_app_web_deps
    app.state.shutdown_web_globals_fn = shutdown_web_channel_runtime_globals
    app.state.auto_takeover_loop_fn = auto_takeover_loop

    from prismclaw.assembly.runtime_coordinator import (
        apply_app_infrastructure_bundle,
        apply_app_lifecycle_bundle,
        apply_app_runtime_bundle,
        apply_runtime_mode_state,
        build_app_infrastructure_bundle,
        build_app_lifecycle_bundle,
        build_app_runtime_bundle,
        shutdown_app_runtime,
    )
    from prismclaw.runtime.facade.dream import RedisDreamDispatchQueue
    from prismclaw.app.gateway.worker_registry import WorkerRegistry, generate_worker_id

    from prismclaw.persistence.memory.factory import create_memory_store

    create_memory_store_fn = create_memory_store

    mcp_manager_factory = None
    if settings.mcp.enabled:
        from prismclaw.runtime.facade.integrations import MCPClientManager

        mcp_manager_factory = MCPClientManager

    try:
        infra_bundle = await build_app_infrastructure_bundle(
            settings=settings,
            sandbox_policy=app.state.sandbox_policy,
            db_engine=app.state.db_engine,
            session_factory=session_factory,
            get_client_fn=get_client,
            lock_manager_factory=create_lock_manager,
            session_store_factory=create_session_store,
            task_manager_factory=TaskManager,
            worker_registry_factory=WorkerRegistry,
            generate_worker_id_fn=generate_worker_id,
            worker_heartbeat_loop=_worker_heartbeat_loop,
            create_workspace_store_fn=create_workspace_store,
            create_memory_store_fn=create_memory_store_fn,
            dream_dispatch_queue_factory=RedisDreamDispatchQueue,
            mcp_manager_factory=mcp_manager_factory,
            run_ownership_factory=build_run_ownership_store,
        )
    except Exception:
        logger.exception("failed to create infrastructure bundle")
        raise
    apply_app_infrastructure_bundle(app, infra_bundle)
    redis_client = infra_bundle.redis_client
    db_engine = app.state.db_engine
    lock_manager = infra_bundle.lock_manager
    task_manager = infra_bundle.task_manager
    worker_id = infra_bundle.worker_id
    workspace_base = infra_bundle.workspace_base
    memory_store = infra_bundle.memory_store

    # ── Health gate: verify Redis + PostgreSQL reachability before domain
    # infrastructure and user store init.
    await _startup_health_gate(redis_client, db_engine)

    # ── 事件循环停顿观测 ────────────────────────────────────────────────
    # 同步阻塞（文件 I/O、zip 压缩、长 CPU 段）会让心跳/锁续约/调度 tick 一起
    # 迟到；把停顿量化成日志信号，任何回归都能在发生时被定位，而不是靠下游
    # 超时倒推。观测本身不触碰业务路径。
    from prismclaw.infra.loop_lag import EventLoopLagMonitor

    task_manager.spawn(
        "event-loop-lag-monitor",
        EventLoopLagMonitor().run(),
        category="generic",
        owner=worker_id,
    )

    # ── Domain Bundle: infrastructure phase ──────────────────────────────
    domain_registry = None
    domain_bundles = []
    try:
        from prismclaw.runtime.facade.foundation import DomainRegistry
        from prismclaw.schema.services.domain import DomainInfrastructureContext

        domain_registry = DomainRegistry()
        # 域启用由编译产物 ENABLED_DOMAINS 决定（domains.yaml 唯一真实来源）。
        # 生成文件缺失（未编译过）时回退空列表 = 加载所有已安装域（旧行为）。
        # 已编译但 ENABLED_DOMAINS=[]（domains.yaml 中所有域 enabled=false）时，
        # 语义是「明确启用 0 个域」——此时必须传 load_all_when_empty=False，
        # 否则 load_enabled 会把空列表误判为「未指定」而加载全部已安装域，
        # 导致 get_manifest() 去读已被编译脚本删除的 _manifest_generated.py。
        try:
            from prismclaw.schema.services._capability_filters_generated import (
                ENABLED_DOMAINS,
            )
            compiled = True
        except ImportError:
            ENABLED_DOMAINS = []
            compiled = False
            logger.warning(
                "_capability_filters_generated.py 缺失，回退为加载所有已安装域；"
                "请运行 node scripts/compile/domains-registry.mjs"
            )
        domain_bundles = domain_registry.load_enabled(
            list(ENABLED_DOMAINS), load_all_when_empty=not compiled
        )
        infra_ctx = DomainInfrastructureContext(
            db_engine=db_engine,
            redis_client=redis_client,
            session_factory=session_factory,
            settings=settings,
            app=app,
        )
        await domain_registry.apply_infrastructure(domain_bundles, infra_ctx)
        infra_bundle.domain_stores.update(infra_ctx.domain_stores)
        # 保存 domain_stores 到 app.state，供路由访问（旧路径，渐进迁移）
        app.state.domain_stores = infra_ctx.domain_stores
        # 类型化 AppState（新路径，供 Depends 注入）
        from prismclaw.infra.app_state import AppState
        _app_state = AppState()
        for _name, _store in infra_ctx.domain_stores.items():
            _app_state.register_domain_store(_name, _store)
        app.state._app_state = _app_state
        logger.info("Domain infrastructure: %d bundles loaded", len(domain_bundles))
    except Exception:
        logger.exception("Domain infrastructure phase failed")

    app.state.web_deps = None
    app.state.dream_dispatch_consumer = None
    app.state.dream_dispatch_health_task_id = None
    app.state.runtime_mode_snapshot = None

    # Wrap startup in try/finally so that MCP supervisor tasks and other
    # async resources are cleaned up even if a later startup step fails
    # (without this, the yield-after cleanup is skipped on exception,
    # leaving mcp:supervisor tasks pending).
    try:
        runtime_bundle = await build_app_runtime_bundle(
            app,
            settings=settings,
            infra=infra_bundle,
            project_root=project_root,
            dream_health_loop=_dream_dispatch_health_loop,
            dream_health_interval_s=_DREAM_DISPATCH_HEALTH_LOG_INTERVAL_S,
            db_engine=db_engine,
        )
        apply_app_runtime_bundle(app, runtime_bundle)
        runner_deps = runtime_bundle.runner_deps
        runner_deps.worker_id = worker_id
        runner_deps.persistence.run_ownership_store = getattr(infra_bundle, "run_ownership_store", None)

        # 发布唯一项目能力布局：聚合启动日志 + 供 /api/health 读取（含降级诊断）。
        _publish_capability_layout(app, runner_deps)

        # ── Domain Bundle: runtime phase ────────────────────────────────
        if domain_registry is not None and domain_bundles is not None:
            try:
                from prismclaw.schema.services.domain import DomainRuntimeContext

                def _make_runtime_ctx(domain_name: str) -> DomainRuntimeContext:
                    from prismclaw.runtime.facade.foundation import (
                        DomainIsolatedTool,
                        DomainIsolatedHook,
                    )
                    from prismclaw.schema.services._capability_filters_generated import (
                        CAPABILITY_FILTERS,
                    )
                    return DomainRuntimeContext(
                        domain_name=domain_name,
                        tool_registry=runner_deps.tools,
                        hook_dispatcher=runner_deps.hook_dispatcher,
                        governance_reducers=runtime_bundle.domain_governance_reducers,
                        skill_provider=runner_deps.skill_provider,
                        tool_wrapper=DomainIsolatedTool,
                        hook_wrapper=DomainIsolatedHook,
                        domain_stores=infra_bundle.domain_stores,
                        capability_filter=CAPABILITY_FILTERS.get(domain_name),
                    )
                domain_registry.apply_runtime(domain_bundles, _make_runtime_ctx)
                # 将域 governance reducers 追加到 ToolGovernance
                if runner_deps.governance is not None and runtime_bundle.domain_governance_reducers:
                    runner_deps.governance.add_reducers(runtime_bundle.domain_governance_reducers)
                logger.info("Domain runtime: %d bundles registered", len(domain_bundles))
            except Exception:
                logger.exception("Domain runtime phase failed")

        reset_web_channel_runtime_globals()
        lifecycle_bundle = await build_app_lifecycle_bundle(
            app,
            settings=settings,
            redis_client=redis_client,
            worker_id=worker_id,
            task_manager=task_manager,
            runner_deps=runner_deps,
            lock_manager=lock_manager,
            workspace_base=workspace_base,
            memory_store=memory_store,
            gateway_factory=build_gateway_components,
        )
        apply_app_lifecycle_bundle(app, lifecycle_bundle)
        apply_runtime_mode_state(app, settings=settings)

        # ── Domain Bundle: lifecycle phase ───────────────────────────────
        # 必须用 is not None 判断：domain_bundles = [] 是空列表（falsy），
        # 但仍然要执行 lifecycle 注册（哪怕 0 个 bundle）。
        if domain_registry is not None and domain_bundles is not None:
            try:
                from prismclaw.schema.services.domain import DomainLifecycleContext

                lifecycle_ctx = DomainLifecycleContext(
                    app=app,
                    settings=settings,
                    task_manager=task_manager,
                )
                domain_registry.apply_lifecycle(domain_bundles, lifecycle_ctx)
                # 挂载域路由到 FastAPI
                # 必须插入到 SPA catch-all mount 之前，
                # 否则 SPAStaticFiles 会先匹配所有路径并返回 404，
                # 导致域 API 路由永远不可达。
                from starlette.routing import Mount
                from prismclaw.app.web.spa import SPAStaticFiles
                spa_mount_idx = None
                for i, route in enumerate(app.routes):
                    if isinstance(route, Mount) and isinstance(route.app, SPAStaticFiles):
                        spa_mount_idx = i
                        break
                route_count_before = len(app.routes)
                for router, prefix in lifecycle_ctx.domain_routers:
                    app.include_router(router, prefix=prefix)
                if spa_mount_idx is not None:
                    # include_router 追加到末尾；将域路由移到 SPA mount 之前
                    n_new = len(app.routes) - route_count_before
                    domain_routes = app.routes[-n_new:]
                    app.routes[-n_new:] = []
                    app.routes[spa_mount_idx:spa_mount_idx] = domain_routes
                # 保存域注册表到 app.state（供 API 查询）
                app.state.domain_registry = domain_registry
                app.state.domain_bundles = domain_bundles
                logger.info("Domain lifecycle: %d bundles registered", len(domain_bundles))
            except Exception:
                logger.exception("Domain lifecycle phase failed")

        # ── Web user store (DB-backed, replaces config users[]) ─────────────
        await _init_user_store(app, settings, redis_client, session_factory)

        # ── Trajectory audit tables (PostgreSQL) ───────────────────────────
        try:
            from prismclaw.persistence.trajectory.pg_persistence import PgTrajectoryPersistence
            from prismclaw.schema.trajectory import init_state as _traj_init, view as _traj_view

            trajectory_persistence = PgTrajectoryPersistence(
                session_factory,
                state_initializer=_traj_init,
                view_fn=_traj_view,
            )
            await trajectory_persistence.init_schema()
            app.state.trajectory_persistence = trajectory_persistence
            logger.info("Trajectory audit PostgreSQL tables initialized.")
            # Backfill trajectory_persistence into runner_deps.persistence and
            # web_deps — build_app_runtime_bundle runs BEFORE this init, so
            # PersistenceBundle.trajectory_persistence is still None at that
            # point.  Without this patch, _handle_cell_event silently drops
            # all cells (persistence=None → early return).
            _rd = getattr(app.state, "runner_deps", None)
            if _rd is not None and hasattr(_rd, "persistence"):
                _rd.persistence.trajectory_persistence = trajectory_persistence
            _wd = getattr(app.state, "web_deps", None)
            if _wd is not None:
                _wd.trajectory_persistence = trajectory_persistence
            # Also update the module-level global in routes.py so that
            # API fallback `_trajectory_persistence` stays consistent.
            try:
                import prismclaw.app.web.routes as _routes_mod
                _routes_mod._trajectory_persistence = trajectory_persistence
            except Exception:
                pass
        except Exception:
            logger.exception(
                "trajectory audit table init failed (DB unreachable); "
                "trajectory will operate in degraded mode."
            )
            app.state.trajectory_persistence = None

        # ── Scheduler system ───────────────────────────────────────────────
        app.state.scheduler_store = None
        app.state.scheduler_engine = None
        app.state.scheduler_dispatch_queue = None
        app.state.scheduler_broadcast = None
        if settings.scheduler.enabled:
            from prismclaw.runtime.facade.scheduler import (
                RedisSchedulerDispatchQueue,
                SchedulerDispatchConsumer,
            )
            from prismclaw.runtime.facade.scheduler import SchedulerEngine
            from prismclaw.runtime.facade.scheduler import ScheduledRunExecutor
            from prismclaw.persistence.scheduler.factory import create_scheduler_store
            from prismclaw.persistence.scheduler.store import init_schema

            await init_schema(app.state.db_engine)
            scheduler_store = create_scheduler_store(session_factory)
            app.state.scheduler_store = scheduler_store

            if redis_client is None:
                logger.warning(
                    "scheduler: Redis unavailable — scheduler system disabled. "
                    "All scheduled task APIs will return 503."
                )
                settings.scheduler.enabled = False
            else:
                dispatch_queue = RedisSchedulerDispatchQueue(
                    redis_client,
                    key_prefix=settings.scheduler.dispatch_key_prefix,
                )
                app.state.scheduler_dispatch_queue = dispatch_queue

                async def _scheduler_broadcast(user_id: str, event_type: str, data: dict) -> None:
                    from prismclaw.app.web.websocket.websocket import broadcast_to_user
                    await broadcast_to_user(app, user_id, event_type, data)

                app.state.scheduler_broadcast = _scheduler_broadcast

                async def _run_scheduled_chat(
                    *, user_id: str, conversation_id: str, content: str, workspace_id: str,
                ) -> None:
                    """定时任务的执行入口 = 一次普通 chat turn。

                    虚拟 socket 的 send_json 复用主链路的 fanout 实现：有活跃
                    连接就投递（界面打开时实时显示），没有活跃连接则正常返回
                    （静默执行）。对 send_event 而言这始终是一次成功的发送，
                    否则「没有界面」会被误判成 WS 故障（ws_dead 置位后
                    fanout 也失败），触发 chat.done 的 8 次退避重试
                    （~31.5s），拖慢主链路收尾并导致时间线 / 交付物来不及
                    落库。
                    消息 / 执行时间线 / 交付物仍由主链路照常落库。
                    """
                    from types import SimpleNamespace

                    from prismclaw.app.web.chat.chat_runner import _run_chat
                    from prismclaw.app.web.connection import (
                        ConnectionState,
                        _fanout_to_peer_connections,
                    )
                    from prismclaw.app.web.protocol.messages import ChatSendMessage

                    state = ConnectionState(
                        ws=SimpleNamespace(app=app),
                        ws_session_id=f"scheduler:{conversation_id}",
                        user_id=user_id,
                    )

                    async def _virtual_socket_send(envelope: dict) -> None:
                        await _fanout_to_peer_connections(state, envelope)

                    state.ws.send_json = _virtual_socket_send
                    msg = ChatSendMessage(
                        conversation_id=conversation_id,
                        workspace_id=workspace_id,
                        content=content,
                    )
                    await _run_chat(state, msg, app.state.web_deps.web_settings)

                executor = ScheduledRunExecutor(
                    app=app,
                    store=scheduler_store,
                    settings=settings.scheduler,
                    broadcast=_scheduler_broadcast,
                    run_chat=_run_scheduled_chat,
                )

                engine = SchedulerEngine(
                    store=scheduler_store,
                    dispatch_enqueue=dispatch_queue.enqueue,
                    settings=settings.scheduler,
                    lock_manager=lock_manager,
                    redis=redis_client,
                    worker_id=worker_id,
                )
                app.state.scheduler_engine = engine

                consumer = SchedulerDispatchConsumer(
                    queue=dispatch_queue,
                    handler=executor.execute,
                    max_concurrent=settings.scheduler.max_concurrent_executions,
                    max_per_user=settings.scheduler.max_concurrent_per_user,
                )

                task_manager.spawn("scheduler-engine", engine.start(), category="generic", owner=worker_id)
                task_manager.spawn("scheduler-consumer", consumer.start(), category="consumer", owner=worker_id)
                logger.info("Scheduler system started (worker=%s)", worker_id)

                # Sync scheduler fields into web_deps (created earlier by
                # build_app_web_deps, before scheduler_store existed).
                _web_deps = getattr(app.state, "web_deps", None)
                if _web_deps is not None:
                    _web_deps.scheduler_store = scheduler_store
                    _web_deps.scheduler_engine = engine
                    _web_deps.scheduler_dispatch_queue = dispatch_queue
                    _web_deps.scheduler_context = {
                        "scheduler_store": scheduler_store,
                        "scheduler_engine": engine,
                        "scheduler_dispatch": dispatch_queue,
                        "scheduler_broadcast": app.state.scheduler_broadcast,
                        "redis_client": redis_client,
                    }

        if settings.channels.web.enabled and app.state.web_deps is not None:
            logger.info("Web channel enabled (worker=%s)", worker_id)

        yield
    finally:
        # Ensure MCP supervisor tasks and other async resources are always
        # cleaned up, even if startup failed before yield.
        _mcp_manager = getattr(app.state, "mcp_manager", None)
        if _mcp_manager is not None:
            shutdown_fn = getattr(_mcp_manager, "shutdown", None)
            if callable(shutdown_fn):
                try:
                    await shutdown_fn()
                except Exception:
                    logger.debug("mcp_manager startup-failure cleanup error", exc_info=True)

        # Graceful scheduler shutdown: release leader lock before TaskManager cancels coroutines
        scheduler_engine = getattr(app.state, "scheduler_engine", None)
        if scheduler_engine is not None:
            scheduler_engine.stop()

        await shutdown_app_runtime(app, close_client_fn=close_client)

        # Release the shared PostgreSQL engine.
        _db_engine = getattr(app.state, "db_engine", None)
        if _db_engine is not None:
            try:
                await _db_engine.dispose()
            except Exception:
                logger.debug("db engine dispose failed (expected during shutdown)", exc_info=True)
