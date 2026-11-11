from __future__ import annotations

from typing import Any

from prismclaw.infra.settings import Settings

from prismclaw.runtime.facade.runtime_mode import build_runtime_mode_snapshot


def apply_runtime_mode_state(app: Any, *, settings: Settings | None = None) -> dict[str, Any]:
    state = getattr(app, "state", None)
    if state is None:
        raise RuntimeError("app.state is not available")
    snapshot = build_runtime_mode_snapshot(app, settings=settings)
    state.runtime_mode_snapshot = snapshot
    web_deps = getattr(state, "web_deps", None)
    if web_deps is not None:
        setattr(web_deps, "runtime_mode_snapshot", snapshot)

    return snapshot
