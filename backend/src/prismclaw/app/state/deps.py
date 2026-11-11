from __future__ import annotations

import logging
from typing import Any

from prismclaw.infra.settings import Settings

logger = logging.getLogger(__name__)


def build_app_web_deps(
    app: Any,
    *,
    settings: Settings | None = None,
    runner_deps: Any | None = None,
) -> Any | None:
    state = getattr(app, "state", None)
    if state is None:
        raise RuntimeError("app.state is not available")

    effective_settings = settings or getattr(state, "settings", None)
    if effective_settings is None:
        raise RuntimeError("server settings are not configured")
    if not effective_settings.channels.web.enabled:
        return None

    effective_runner_deps = runner_deps or getattr(state, "runner_deps", None)
    if effective_runner_deps is None:
        raise RuntimeError("runner_deps is not available")

    session_store = getattr(state, "session_store", None)
    if session_store is None:
        raise RuntimeError("session_store is not available")

    task_manager = getattr(state, "task_manager", None)
    if task_manager is None:
        raise RuntimeError("task_manager is not available")

    workspace_base = getattr(state, "workspace_base", None)
    if workspace_base is None:
        raise RuntimeError("workspace_base is not available")

    from prismclaw.app.session_router import SessionRouter
    from prismclaw.app.web.chat.chat_protocol import SessionQueue
    from prismclaw.app.web.deps import WebDeps
    from prismclaw.app.web.websocket.websocket import ConnectionRegistry
    from prismclaw.app.web.websocket.domain_task_emitter import WsDomainTaskEmitter
    from prismclaw.assembly.plugins.reload_service import apply_app_settings
    from prismclaw.assembly.runtime_coordinator import get_app_workspace_runner_deps_async
    from prismclaw.runtime.facade.execution import cleanup_working_dir
    from prismclaw.runtime.facade.plugins import (
        is_source_trusted_for_load,
        resolve_workspace_trusted,
    )
    from prismclaw.runtime.facade.security import AuditLogger
    from prismclaw.runtime.facade.services import maybe_spawn_extraction
    from prismclaw.schema.dream import DreamAdminOps

    web_nudge_hook = _resolve_web_nudge_hook(effective_runner_deps)

    async def _web_on_rotated(old_session_id: str) -> None:
        current_state = getattr(app, "state", None)
        if current_state is None:
            return

        current_memory_store = getattr(current_state, "memory_store", None)
        if current_memory_store is None:
            return

        current_task_manager = getattr(current_state, "task_manager", None)
        current_session_store = getattr(current_state, "session_store", None)
        if current_task_manager is None or current_session_store is None:
            return

        archive_owner = old_session_id.split(":s:", 1)[0] if ":s:" in old_session_id else None
        current_task_manager.spawn(
            f"consolidate:{old_session_id}",
            effective_runner_deps.context_memory_provider.consolidate_session(
                old_session_id
            ),
            category="archive",
            owner=archive_owner,
        )

        current_settings = getattr(current_state, "settings", effective_settings)
        if not getattr(getattr(current_settings, "evolution", None), "enabled", False):
            return

        current_web_deps = getattr(current_state, "web_deps", None)
        llm_client = getattr(current_web_deps, "llm_client", None)
        if llm_client is None:
            llm_client = getattr(effective_runner_deps, "llm", None)

        nudge_hook = getattr(current_web_deps, "nudge_hook", None)
        if nudge_hook is None:
            nudge_hook = web_nudge_hook

        await maybe_spawn_extraction(
            task_manager=current_task_manager,
            memory_store=current_memory_store,
            session_store=current_session_store,
            redis_client=getattr(current_state, "redis_client", None),
            llm_client=llm_client,
            session_id=old_session_id,
            settings=current_settings.evolution,
            nudge_hook=nudge_hook,
        )

    session_router = SessionRouter(
        store=session_store,
        on_session_rotated=_web_on_rotated,
    )
    session_queue = SessionQueue(task_manager=task_manager)
    if effective_runner_deps is not None:
        effective_runner_deps.session_queue = session_queue
    connection_registry = ConnectionRegistry()
    audit_logger = AuditLogger()
    return WebDeps(
        session_store=session_store,
        session_router=session_router,
        workspace_base=workspace_base,
        runner_deps=effective_runner_deps,
        session_queue=session_queue,
        connection_registry=connection_registry,
        settings_full=effective_settings,
        redis_client=getattr(state, "redis_client", None),
        memory_store=getattr(state, "memory_store", None),
        task_manager=task_manager,
        evolution_settings=(
            effective_settings.evolution if effective_settings.evolution.enabled else None
        ),
        nudge_hook=web_nudge_hook,
        llm_client=getattr(effective_runner_deps, "llm", None),
        agent_settings=effective_settings.agent,
        worker_registry=getattr(state, "worker_registry", None),
        audit_logger=audit_logger,
        runtime_mode_snapshot=getattr(state, "runtime_mode_snapshot", None),
        sandbox_policy=getattr(state, "sandbox_policy", None),
        scheduler_context=(
            {
                "scheduler_store": getattr(state, "scheduler_store", None),
                "scheduler_engine": getattr(state, "scheduler_engine", None),
                "scheduler_dispatch": getattr(state, "scheduler_dispatch_queue", None),
                "scheduler_broadcast": getattr(state, "scheduler_broadcast", None),
                "redis_client": getattr(state, "redis_client", None),
            }
            if getattr(state, "scheduler_store", None) is not None
            else None
        ),
        web_settings=getattr(state, "web_settings", None),
        gateway_router=getattr(state, "gateway_router", None),
        domain_task_emitter=WsDomainTaskEmitter(app),
        mcp_manager=getattr(state, "mcp_manager", None),
        scheduler_store=getattr(state, "scheduler_store", None),
        scheduler_engine=getattr(state, "scheduler_engine", None),
        scheduler_dispatch_queue=getattr(state, "scheduler_dispatch_queue", None),
        user_repository=getattr(state, "user_repository", None),
        apply_settings_fn=apply_app_settings,
        cleanup_working_dir_fn=cleanup_working_dir,
        workspace_runner_deps_fn=get_app_workspace_runner_deps_async,
        plugin_trust_fns={
            "is_source_trusted_for_load": is_source_trusted_for_load,
            "resolve_workspace_trusted": resolve_workspace_trusted,
        },
        dream_admin_ops=DreamAdminOps,
    )


def _resolve_web_nudge_hook(runner_deps: Any) -> Any:
    hooks = getattr(runner_deps, "hooks", None)
    hook_iter = getattr(hooks, "hooks", None)
    if not callable(hook_iter):
        return None

    from prismclaw.runtime.facade.hooks import MemoryNudgeHook
    for hook in hook_iter():
        if isinstance(hook, MemoryNudgeHook):
            return hook
    return None
