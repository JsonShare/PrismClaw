from __future__ import annotations

import logging
from typing import Any

from prismclaw.infra.settings import Settings

from prismclaw.assembly.bundles_data import AppRuntimeBundle
from prismclaw.assembly.wiring.runtime_mode import apply_runtime_mode_state

logger = logging.getLogger(__name__)


async def build_plugin_reload_runtime_bundle(
    app: Any,
    *,
    settings: Settings | None = None,
    runner_deps: Any | None = None,
) -> AppRuntimeBundle:
    state = getattr(app, "state", None)
    if state is None:
        raise RuntimeError("app.state is not available")

    effective_settings = settings or getattr(state, "settings", None)
    if effective_settings is None:
        raise RuntimeError("server settings are not configured")

    effective_runner_deps = runner_deps or getattr(state, "runner_deps", None)
    if effective_runner_deps is None:
        raise RuntimeError("runner_deps is not available")

    _apply_fn = getattr(state, "apply_runtime_state_fn", None)
    if _apply_fn is None:
        raise RuntimeError("app.state.apply_runtime_state_fn is not configured")
    _apply_fn(
        app,
        effective_runner_deps,
        settings=effective_settings,
    )
    apply_runtime_mode_state(app, settings=effective_settings)

    web_deps = None
    if effective_settings.channels.web.enabled:
        _build_deps_fn = getattr(state, "build_web_deps_fn", None)
        if _build_deps_fn is None:
            raise RuntimeError("app.state.build_web_deps_fn is not configured")
        web_deps = _build_deps_fn(
            app,
            settings=effective_settings,
            runner_deps=effective_runner_deps,
        )
        state.web_deps = web_deps
        _sync_fn = getattr(state, "sync_web_state_fn", None)
        if _sync_fn is not None:
            _sync_fn(app, effective_runner_deps, settings=effective_settings)

    return AppRuntimeBundle(
        runner_deps=effective_runner_deps,
        web_deps=web_deps,
        dream_dispatch_consumer=getattr(state, "dream_dispatch_consumer", None),
        dream_dispatch_health_task_id=getattr(state, "dream_dispatch_health_task_id", None),
    )
