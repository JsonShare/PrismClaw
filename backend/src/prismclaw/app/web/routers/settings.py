"""Settings and profile API routes – extracted from routes.py (router split)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from prismclaw.app.auth import resolve_profile_and_mode
from prismclaw.app.auth.auth import get_current_user
from prismclaw.app.web.deps import get_web_deps
from prismclaw.app.web._routes_helpers import (
    _redis_client,
)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class WebSettingsResponse(BaseModel):
    default_mode: str
    runtime_mode: dict[str, Any] | None = None
    email_verification_enabled: bool = False


class UserProfileResponse(BaseModel):
    user_id: str


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _get_web_settings(request: Request) -> Any:
    web_deps = get_web_deps(request)
    ws = web_deps.web_settings
    if ws is not None:
        return ws
    settings = web_deps.settings_full
    if settings is None:
        raise HTTPException(status_code=500, detail="Settings not configured")
    channels = getattr(settings, "channels", None)
    if channels is None:
        raise HTTPException(status_code=500, detail="Settings not configured")
    return getattr(channels, "web", None)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

settings_router = APIRouter(tags=["settings"])


@settings_router.get("/settings", response_model=WebSettingsResponse)
async def get_web_settings(request: Request) -> WebSettingsResponse:
    web_deps = get_web_deps(request)
    web_settings = web_deps.settings_full.channels.web
    runtime_mode = web_deps.runtime_mode_snapshot
    return WebSettingsResponse(
        default_mode=web_settings.default_mode,
        runtime_mode=runtime_mode if isinstance(runtime_mode, dict) else None,
        email_verification_enabled=bool(web_settings.smtp.enabled and web_deps.user_repository is not None),
    )


@settings_router.get("/profile", response_model=UserProfileResponse)
async def get_profile(
    request: Request,
    user_id: str = Depends(get_current_user),
) -> UserProfileResponse:
    web_settings = _get_web_settings(request)
    user_profile, _mode = await resolve_profile_and_mode(
        channel="web",
        user_id=user_id,
        redis_client=_redis_client,
        message_mode=None,
        deployment_default=web_settings.default_mode,
    )
    return UserProfileResponse(
        user_id=user_id,
    )
