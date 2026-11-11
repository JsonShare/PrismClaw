"""FastAPI middleware installation helpers."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request

from prismclaw.app._env import _is_truthy_env

logger = logging.getLogger(__name__)


def _install_metrics(app: FastAPI) -> None:
    if not _is_truthy_env("PRISMCLAW_ENABLE_METRICS", default=True):
        return
    try:
        from prometheus_fastapi_instrumentator import Instrumentator

        Instrumentator().instrument(app).expose(app, endpoint="/metrics")
    except Exception:
        logger.warning("Prometheus metrics instrumentation unavailable", exc_info=True)


def _install_security_headers(app: FastAPI) -> None:
    """Inject baseline security headers (CSP, HSTS, X-Frame-Options, etc.).

    Applied to every response, including the SPA fallback HTML, so the browser
    enforces origin isolation and disables framing/mime sniffing.
    """

    # Development-friendly CSP: allow inline scripts (Vite HMR), data URIs
    # for fonts/images, and localhost connections for the dev server.
    # blob: is required by AudioWorklet.addModule(blobURL) used by voice input;
    # worklet module fetches are governed by script-src.
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "connect-src 'self' ws: wss: http://localhost:5173 http://127.0.0.1:5173; "
        "frame-src 'self' blob:; "
        "object-src 'self' blob:; "
        "media-src 'self' blob:; "
        "frame-ancestors 'none'"
    )

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", csp)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


def _install_rate_limiter(app: FastAPI) -> None:
    if not _is_truthy_env("PRISMCLAW_ENABLE_RATE_LIMIT", default=True):
        return
    try:
        from slowapi import Limiter
        from slowapi.errors import RateLimitExceeded
        from slowapi.middleware import SlowAPIMiddleware
        from slowapi.util import get_remote_address

        limiter = Limiter(
            key_func=get_remote_address,
            default_limits=["100/minute"],
            storage_uri="memory://",
        )
        app.state.limiter = limiter
        app.add_middleware(SlowAPIMiddleware)

        @app.exception_handler(RateLimitExceeded)
        async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
            from fastapi.responses import JSONResponse

            return JSONResponse(status_code=429, content={"error": "rate_limit_exceeded"})
    except Exception:
        logger.warning("Rate limiting unavailable", exc_info=True)


def _install_tracing(app: FastAPI) -> None:
    if not _is_truthy_env("PRISMCLAW_ENABLE_TRACING", default=True):
        return
    try:
        from opentelemetry import trace
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider

        provider = TracerProvider(resource=Resource.create({SERVICE_NAME: "prismclaw"}))
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)

        # Echo the current trace context on the response (W3C traceparent) so
        # clients/log aggregators can correlate a request with its span.
        @app.middleware("http")
        async def _traceparent_echo(request: Request, call_next):
            response = await call_next(request)
            span = trace.get_current_span()
            ctx = span.get_span_context()
            if ctx and ctx.trace_id:
                tp = "00-{:032x}-{:016x}-01".format(ctx.trace_id, ctx.span_id)
                response.headers["traceparent"] = tp
            return response
    except Exception:
        logger.warning("Distributed tracing unavailable", exc_info=True)
