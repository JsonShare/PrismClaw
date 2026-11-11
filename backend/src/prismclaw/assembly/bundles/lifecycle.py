from __future__ import annotations

import logging
from typing import Any

from prismclaw.assembly.bundles_data import AppLifecycleBundle
from prismclaw.infra.settings import Settings

logger = logging.getLogger(__name__)


async def build_app_lifecycle_bundle(
    app: Any,
    *,
    settings: Settings,
    redis_client: Any,
    worker_id: str,
    task_manager: Any,
    runner_deps: Any,
    lock_manager: Any,
    workspace_base: Any,
    memory_store: Any,
    gateway_factory: Any = None,
) -> AppLifecycleBundle:
    gateway_router = None
    forward_consumer = None
    auto_takeover_task_id = None
    if settings.affinity.enabled:
        if gateway_factory is not None:
            gateway_router, forward_consumer = gateway_factory(
                redis_client, worker_id, settings, app=app,
            )
        if forward_consumer is not None:
            task_manager.spawn(
                "forward-consumer",
                forward_consumer.start(),
                category="consumer",
            )
        _auto_takeover_fn = getattr(app.state, "auto_takeover_loop_fn", None)
        if (
            _auto_takeover_fn is not None
            and getattr(app.state, "run_ownership_store", None) is not None
        ):
            auto_takeover_task_id = task_manager.spawn(
                "auto-takeover-loop",
                _auto_takeover_fn(
                    app.state,
                    interval_s=max(
                        5.0,
                        min(
                            float(settings.affinity.heartbeat_interval),
                            float(settings.affinity.stale_threshold) / 3.0,
                        ),
                    ),
                ),
                category="heartbeat",
                owner=worker_id,
            )
        logger.info("Session affinity gateway enabled (worker=%s)", worker_id)

    # Curator 后台循环（运行时服务已迁移至 runtime.curator_starter）
    from prismclaw.runtime.curator_starter import start_curator_if_enabled
    await start_curator_if_enabled(
        app=app,
        settings=settings,
        redis_client=redis_client,
        task_manager=task_manager,
        runner_deps=runner_deps,
        lock_manager=lock_manager,
        workspace_base=workspace_base,
        memory_store=memory_store,
    )

    return AppLifecycleBundle(
        gateway_router=gateway_router,
        forward_consumer=forward_consumer,
        auto_takeover_task_id=auto_takeover_task_id,
    )


def apply_app_lifecycle_bundle(app: Any, bundle: AppLifecycleBundle) -> None:
    state = getattr(app, "state", None)
    if state is None:
        raise RuntimeError("app.state is not available")

    state.gateway_router = bundle.gateway_router
    state.forward_consumer = bundle.forward_consumer
    state.auto_takeover_task_id = bundle.auto_takeover_task_id
    current_status = getattr(state, "auto_takeover_status", None)
    if isinstance(current_status, dict):
        updated_status = dict(current_status)
        updated_status["enabled"] = bool(bundle.auto_takeover_task_id)
        updated_status["task_id"] = bundle.auto_takeover_task_id
        state.auto_takeover_status = updated_status
    else:
        state.auto_takeover_status = {
            "enabled": bool(bundle.auto_takeover_task_id),
            "task_id": bundle.auto_takeover_task_id,
            "last_scan_at_epoch_ms": None,
            "last_scan_duration_ms": None,
            "last_error": None,
            "runs_considered": 0,
            "runs_started": 0,
            "runs_assigned_to_other_workers": 0,
            "blocked_reason_counts": {},
            "blocked_samples": [],
            "started_runs": [],
        }
