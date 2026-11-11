"""PrismClaw application layer — FastAPI assembly, routing, lifecycle."""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Request

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from prismclaw.infra.logging_setup import install_logging
from prismclaw.infra.paths import resolve_project_root
from prismclaw.infra.settings import load_settings
from prismclaw.schema.storage.storage_types import SessionStore

from prismclaw.app._lifespan import _lifespan
from prismclaw.app._middleware import (
    _install_security_headers,
    _install_rate_limiter,
    _install_tracing,
    _install_metrics,
)
from prismclaw.app._health import build_health_endpoint

logger = logging.getLogger(__name__)

install_logging(
    level="DEBUG",
    logger_levels={
        "prismclaw.engine.execution.loop.runner": "DEBUG",
    },
)


async def get_session_store(request: Request) -> SessionStore:
    return request.app.state.session_store


def create_app() -> FastAPI:
    install_logging()
    settings = load_settings()
    app = FastAPI(title="PrismClaw", version="0.1.0", lifespan=_lifespan)

    _install_security_headers(app)
    _install_rate_limiter(app)
    _install_tracing(app)

    build_health_endpoint(app)

    if settings.channels.web.enabled:
        from starlette.middleware.cors import CORSMiddleware

        from prismclaw.app.auth.auth_routes import auth_router
        from prismclaw.app.web.routers.openai_compat import openai_router
        from prismclaw.app.web.routes import web_router
        from prismclaw.app.web.routers.scheduled_tasks import scheduler_router
        from prismclaw.app.web.websocket.websocket import ws_router

        # Expose Prometheus /metrics BEFORE the SPA catch-all mount so the exact
        # path is not shadowed by the SPA fallback to index.html.
        _install_metrics(app)

        allow_origins = list(settings.channels.web.cors_origins or [])
        for origin in ("http://localhost:5173", "http://127.0.0.1:5173"):
            if origin not in allow_origins:
                allow_origins.append(origin)

        app.add_middleware(
            CORSMiddleware,
            allow_origins=allow_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type"],
        )

        app.include_router(auth_router)
        app.include_router(web_router)
        app.include_router(ws_router)
        app.include_router(openai_router)
        app.include_router(scheduler_router)

        spa_dir = resolve_project_root() / "frontend" / "dist"
        if spa_dir.is_dir():
            _install_metrics(app)
            from prismclaw.app.web.spa import SPAStaticFiles

            app.mount("/", SPAStaticFiles(directory=str(spa_dir), html=True), name="spa")

    return app


__all__ = ["create_app", "get_session_store"]
