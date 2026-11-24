from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

    from prismclaw.engine.execution.loop.deps import AgentRunnerDeps

from prismclaw.app.state.web_state import sync_web_runtime_state
from prismclaw.assembly.workspace.cache import (
    clear_workspace_runner_deps_cache,
)
from prismclaw.infra.settings import Settings


def apply_app_runtime_state(
    app: FastAPI,
    runner_deps: AgentRunnerDeps,
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

    runtime_mcp_manager = runner_deps.plugin.runtime_mcp_manager
    if runtime_mcp_manager is not None or not hasattr(state, "mcp_manager"):
        state.mcp_manager = runtime_mcp_manager

    state.dream_ops = runner_deps.dream.ops or runner_deps.dream.review_service

    sync_web_runtime_state(app, runner_deps, settings=settings)

    if clear_workspace_cache:
        clear_workspace_runner_deps_cache(app)
