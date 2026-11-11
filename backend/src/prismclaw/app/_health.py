"""Health check endpoint builder."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request

logger = logging.getLogger(__name__)


def build_health_endpoint(app: FastAPI) -> None:
    """Register the ``/api/health`` endpoint on *app*."""

    @app.get("/api/health")
    async def health(request: Request) -> dict:
        from prismclaw.infra.redis import ping as redis_ping
        from prismclaw.runtime.facade.runtime_mode import build_runtime_mode_snapshot

        backend = getattr(request.app.state, "session_store", None)
        redis_client = getattr(request.app.state, "redis_client", None)
        result: dict = {"status": "ok", "storage": type(backend).__name__ if backend else "none"}
        if redis_client is not None:
            result["redis"] = "ok" if await redis_ping(redis_client) else "error"

        mcp_manager = getattr(request.app.state, "mcp_manager", None)
        if mcp_manager is not None:
            summary = mcp_manager.connection_summary()
            result["mcp"] = {
                "ready": mcp_manager.is_ready(),
                "n_connected": summary.n_connected,
                "n_failed": summary.n_failed,
                "n_pending": summary.n_pending,
                "n_disabled": summary.n_disabled,
                "total_tools": summary.total_tools,
            }

        # 项目级能力布局（skills/plugins/agents 单一事实源）。ERROR（如锚定根但
        # 三类目录全空）降级；WARN（未锚定/个别目录缺失）仅上报不降级，避免误伤裸部署。
        capability_layout = getattr(request.app.state, "capability_layout", None)
        if capability_layout is not None:
            result["capabilities"] = capability_layout.summary_dict()
            if capability_layout.has_error and result.get("status") == "ok":
                result["status"] = "degraded"

        sandbox_state = getattr(request.app.state, "sandbox_state", None)
        if sandbox_state is not None:
            from prismclaw.runtime.facade.security import health_advisory

            result["sandbox"] = health_advisory(sandbox_state)

        worker_registry = getattr(request.app.state, "worker_registry", None)
        if worker_registry is not None:
            result["worker_id"] = worker_registry.worker_id
            if worker_registry.available:
                workers = await worker_registry.active_workers()
                result["cluster_size"] = sum(1 for w in workers if w["status"] == "healthy")

        runtime_mode = build_runtime_mode_snapshot(request.app)
        result["runtime_mode"] = runtime_mode
        if runtime_mode.get("mode") == "fallback" and result.get("status") == "ok":
            result["status"] = "degraded"

        dream_ops = getattr(request.app.state, "dream_ops", None)
        if dream_ops is not None:
            try:
                dispatch_status = await dream_ops.get_dispatch_status()
            except Exception:
                logger.warning("health endpoint dream dispatch check failed", exc_info=True)
                dispatch_status = {"available": False, "summary": {"health_status": "unavailable"}}
            summary = dispatch_status.get("summary") if isinstance(dispatch_status, dict) else {}
            dead_letter_metrics = (
                dispatch_status.get("dead_letter_metrics") if isinstance(dispatch_status, dict) else {}
            )
            session_health = dispatch_status.get("session_health") if isinstance(dispatch_status, dict) else {}
            summary = summary if isinstance(summary, dict) else {}
            dead_letter_metrics = dead_letter_metrics if isinstance(dead_letter_metrics, dict) else {}
            session_health = session_health if isinstance(session_health, dict) else {}
            health_status = str(summary.get("health_status", "unknown") or "unknown")
            result["dream_dispatch"] = {
                "available": bool(dispatch_status.get("available")) if isinstance(dispatch_status, dict) else False,
                "health_status": health_status,
                "queue_backlog": int(summary.get("queue_backlog", 0) or 0),
                "dead_letter_count": int(dead_letter_metrics.get("total_count", 0) or 0),
                "blocked_replay_count": int(summary.get("blocked_replay_count", 0) or 0),
                "impacted_session_count": int(session_health.get("impacted_session_count", 0) or 0),
                "blocked_session_count": int(session_health.get("blocked_session_count", 0) or 0),
                "latest_error": dead_letter_metrics.get("latest_error"),
            }
            if health_status in {"degraded", "unavailable"} and result.get("status") == "ok":
                result["status"] = "degraded"

        return result
