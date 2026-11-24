from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI

from prismclaw.infra.settings import Settings
from prismclaw.runtime.facade.runtime_mode import build_runtime_mode_snapshot


def apply_runtime_mode_state(app: FastAPI, *, settings: Settings | None = None) -> dict[str, Any]:
    state = getattr(app, "state", None)
    if state is None:
        raise RuntimeError("app.state is not available")
    snapshot = build_runtime_mode_snapshot(app, settings=settings)
    state.runtime_mode_snapshot = snapshot
    web_deps = getattr(state, "web_deps", None)
    if web_deps is not None:
        web_deps.runtime_mode_snapshot = snapshot

    return snapshot
