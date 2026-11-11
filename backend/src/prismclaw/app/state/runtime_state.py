from __future__ import annotations

from typing import Any

from prismclaw.infra.settings import Settings

from prismclaw.assembly.workspace.cache import (
    clear_workspace_runner_deps_cache,
)
from prismclaw.assembly.wiring.runtime_state import (  # noqa: F401
    resolve_dream_boundary,
    resolve_dream_scheduler,
)
from prismclaw.app.state.web_state import sync_web_runtime_state


def apply_app_runtime_state(
    app: Any,
    runner_deps: Any,
    *,
    settings: Settings | None = None,
    clear_workspace_cache: bool = True,
) -> None:
    state = getattr(app, "state", None)
    if state is None:
        raise RuntimeError("app.state is not available")

    state.runner_deps = runner_deps
    if settings is not None:
        state.settings = settings
        state.web_settings = getattr(getattr(settings, "channels", None), "web", None)
        llm_client = getattr(runner_deps, "llm", None)
        if llm_client is not None:
            llm_client.default_model = settings.models.default_model

    runtime_mcp_manager = getattr(runner_deps, "runtime_mcp_manager", None)
    if runtime_mcp_manager is not None or not hasattr(state, "mcp_manager"):
        state.mcp_manager = runtime_mcp_manager

    state.dream_review_service = getattr(runner_deps.dream, "review_service", None)
    state.dream_boundary = getattr(runner_deps.dream, "boundary", None)
    state.dream_ops = getattr(runner_deps.dream, "ops", None) or getattr(
        runner_deps.dream, "review_service", None
    )
    state.dream_scheduler = resolve_dream_boundary(runner_deps)

    sync_web_runtime_state(app, runner_deps, settings=settings)

    if clear_workspace_cache:
        clear_workspace_runner_deps_cache(app)
