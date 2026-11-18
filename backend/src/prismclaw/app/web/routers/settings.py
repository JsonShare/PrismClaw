"""Settings and profile API routes – extracted from routes.py (router split)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from prismclaw.app.auth.auth import get_current_user
from prismclaw.app.web.deps import get_web_deps

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class WebSettingsResponse(BaseModel):
    runtime_mode: dict[str, Any] | None = None
    email_verification_enabled: bool = False


class UserProfileResponse(BaseModel):
    user_id: str


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
        runtime_mode=runtime_mode if isinstance(runtime_mode, dict) else None,
        email_verification_enabled=bool(web_settings.smtp.enabled and web_deps.user_repository is not None),
    )


@settings_router.get("/profile", response_model=UserProfileResponse)
async def get_profile(
    request: Request,
    user_id: str = Depends(get_current_user),
) -> UserProfileResponse:
    return UserProfileResponse(
        user_id=user_id,
    )
