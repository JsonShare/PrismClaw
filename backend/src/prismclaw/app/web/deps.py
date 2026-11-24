from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request

if TYPE_CHECKING:
    from prismclaw.app.gateway.worker_registry import WorkerRegistry
    from prismclaw.app.session_router import SessionRouter
    from prismclaw.app.web.chat.chat_protocol import SessionQueue
    from prismclaw.app.web.connection import ConnectionRegistry
    from prismclaw.engine.execution.loop.deps import AgentRunnerDeps
    from prismclaw.engine.services.prompt_optimizer import PromptOptimizer
    from prismclaw.infra.settings import Settings
    from prismclaw.schema.agent.types import AgentDeps
    from prismclaw.schema.scheduler.scheduler import (
        SchedulerEngineProtocol,
        SchedulerStoreProtocol,
    )
    from prismclaw.schema.storage import SessionStore


@dataclass
class WebDeps:
    session_store: SessionStore
    session_router: SessionRouter
    workspace_base: Path
    runner_deps: AgentDeps
    session_queue: SessionQueue
    connection_registry: ConnectionRegistry
    settings_full: Settings

    redis_client: Any = None
    memory_store: Any = None
    # 跨 worker 运行态在场证明（Redis 心跳 + 中止 Pub/Sub）；无 Redis 时为 None。
    run_presence: Any = None
    task_manager: Any = None
    evolution_settings: Any = None
    nudge_hook: Any = None
    llm_client: Any = None
    agent_settings: Any = None
    worker_registry: WorkerRegistry | None = None
    runtime_mode_snapshot: dict[str, Any] | None = None
    sandbox_policy: Any = None
    scheduler_context: dict[str, Any] | None = None
    web_settings: Any = None  # WebSettings (app.state.web_settings)
    gateway_router: Any = None  # app.state.gateway_router
    domain_task_emitter: Any = None  # DomainTaskEmitter 端口实现（schema.services.domain 协议）
    mcp_manager: Any = None  # app.state.mcp_manager
    # scheduler 独立字段（从 app.state 收敛，替代 scheduler_context dict）
    scheduler_store: SchedulerStoreProtocol | None = None
    scheduler_engine: SchedulerEngineProtocol | None = None
    scheduler_dispatch_queue: Any = None
    # auth 相关
    user_repository: Any = None
    apply_settings_fn: Any = (
        None  # apply_app_settings(app, settings, *, invalidate_plugin_runtime=True)
    )
    cleanup_working_dir_fn: Any = None  # async cleanup_working_dir(working_dir)
    workspace_runner_deps_fn: (
        Callable[..., Awaitable[AgentRunnerDeps | None]] | None
    ) = None  # async get_app_workspace_runner_deps_async(app, user_id, workspace_root)
    workspace_runner_deps_state_fn: Any = (
        None  # get_app_workspace_runner_deps_state(app, user_id, workspace_root)
    )
    plugin_trust_fns: Any = None  # 插件信任判定函数，来自 engine.services.plugins.trust
    dream_admin_ops: Any = None  # DreamAdminOps class from engine.dream.governance.ops
    trajectory_persistence: Any = None  # TrajectoryPersistence implementation
    prompt_optimizer_factory: Callable[[Any], PromptOptimizer] | None = None
    extract_fn: Any = None  # async run_extract(...)
    format_extraction_fn: Any = None  # format_extraction_result_zh(result)
    review_registry_fn: Any = (
        None  # get_review_registry(workspace_root) from engine.services.review_service
    )
    reload_plugin_fn: Any = None  # reload_app_plugin_runtime from app.plugins.reload_service
    takeover_fns: Any = None  # dict of takeover functions from app.gateway.takeover
    upload_fns: Any = None  # 上传链路实现，来自 engine.services.upload_service
    review_utils: Any = None  # 审阅工具集，来自 engine.services.review_service
    chat_service: Any = None  # ChatService Protocol instance
    hotpath_telemetry: Any = None  # HotPathTelemetry instance
    office_review: Any = None  # OfficeReviewProtocol instance
    skill_origin_fns: Any = (
        None  # {classify_skill_origin, classify_skill_group_path, classify_skill_group_label}
    )
    skill_parse_fn: Any = None  # parse_skill_file
    skill_hub_client_factory: Any = None  # create_client
    skill_install_fn: Any = None
    resolve_provider_fn: Any = (
        None  # resolve_provider_for_model from engine.services.llm.provider_resolver
    )
    build_runtime_mode_snapshot_fn: Any = (
        None  # build_runtime_mode_snapshot from engine.runtime.mode
    )


def get_web_deps_from_app(app: FastAPI) -> WebDeps:
    # app.state 是无类型袋（Starlette State），此处按装配契约固定为 WebDeps。
    deps: WebDeps = app.state.web_deps
    return deps


def get_web_deps(request: Request) -> WebDeps:
    return get_web_deps_from_app(request.app)
