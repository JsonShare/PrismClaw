"""Static shared-token auth for the streamable-http transport.

MCP clients send headers once at connection time, so per-request signed
timestamps are not an option; a secret compared with ``hmac.compare_digest``
is the appropriate boundary. The token is provisioned through the
``WSM_TOKEN`` environment variable. Fails closed: an unset token rejects all
MCP traffic over HTTP.
"""

from __future__ import annotations

import hmac
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

_EXEMPT_PATHS = {"/health", "/"}


class TokenAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)
        provided = request.headers.get("x-wsm-token", "")
        if not self._token:
            logger.error("WSM_TOKEN is empty; rejecting HTTP MCP request (fail-closed)")
            return JSONResponse({"error": "server token not configured"}, status_code=503)
        if not hmac.compare_digest(provided, self._token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)
