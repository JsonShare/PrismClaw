from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Request

if TYPE_CHECKING:
    from prismclaw.schema.agent.types import AgentDeps
    from prismclaw.app.session_router import SessionRouter
    from prismclaw.app.web.chat.chat_protocol import SessionQueue
    from prismclaw.app.web.connection import ConnectionRegistry
    from prismclaw.app.gateway.worker_registry import WorkerRegistry
    from prismclaw.infra.settings import Settings
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
    task_manager: Any = None
    evolution_settings: Any = None
    nudge_hook: Any = None
    llm_client: Any = None
    agent_settings: Any = None
    worker_registry: WorkerRegistry | None = None
    audit_logger: Any = None
    runtime_mode_snapshot: dict[str, Any] | None = None
    sandbox_policy: Any = None
    scheduler_context: dict[str, Any] | None = None
    web_settings: Any = None           # WebSettings (app.state.web_settings)
    gateway_router: Any = None         # app.state.gateway_router
    domain_task_emitter: Any = None   # DomainTaskEmitter 端口实现（schema.services.domain 协议）
    mcp_manager: Any = None            # app.state.mcp_manager
    # scheduler 独立字段（从 app.state 收敛，替代 scheduler_context dict）
    scheduler_store: Any = None
    scheduler_engine: Any = None
    scheduler_dispatch_queue: Any = None
    # auth 相关
    user_repository: Any = None
    apply_settings_fn: Any = None       # apply_app_settings(app, settings, *, invalidate_plugin_runtime=True)
    cleanup_working_dir_fn: Any = None  # async cleanup_working_dir(working_dir)
    workspace_runner_deps_fn: Any = None  # async get_app_workspace_runner_deps_async(app, user_id, workspace_root)
    workspace_runner_deps_state_fn: Any = None  # get_app_workspace_runner_deps_state(app, user_id, workspace_root)
    plugin_trust_fns: Any = None          # {is_source_trusted_for_load, resolve_workspace_trusted} from engine.services.plugins.trust
    dream_admin_ops: Any = None           # DreamAdminOps class from engine.dream.governance.ops
    trajectory_persistence: Any = None  # TrajectoryPersistence implementation
    prompt_optimizer_factory: Any = None    # callable(llm_client) -> PromptOptimizer
    extract_fn: Any = None                  # async run_extract(...)
    format_extraction_fn: Any = None        # format_extraction_result_zh(result)
    review_registry_fn: Any = None          # get_review_registry(workspace_root) from engine.services.review_service
    reload_plugin_fn: Any = None            # reload_app_plugin_runtime from app.plugins.reload_service
    takeover_fns: Any = None                # dict of takeover functions from app.runtime.lifecycle.takeover
    review_lifecycle_fn: Any = None         # lifecycle module from engine.services.review_service (has gc_blobs)
    upload_fns: Any = None                  # {LocalSink, upload_files, build_file_change, get_tracking_store} from engine.services.upload_service
    review_utils: Any = None                # {normalize_clarify_questions, apply_office_ops, fmt_from_path} from engine.services.review_service
    chat_service: Any = None                # ChatService Protocol instance
    hotpath_telemetry: Any = None           # HotPathTelemetry instance
    office_review: Any = None               # OfficeReviewProtocol instance
    skill_origin_fns: Any = None            # {classify_skill_origin, classify_skill_group_path, classify_skill_group_label}
    skill_parse_fn: Any = None              # parse_skill_file
    skill_hub_client_factory: Any = None    # create_client
    skill_install_fn: Any = None
    resolve_provider_fn: Any = None       # resolve_provider_for_model from engine.services.llm.provider_resolver
    build_runtime_mode_snapshot_fn: Any = None  # build_runtime_mode_snapshot from engine.runtime.mode


def get_web_deps(request: Request) -> WebDeps:
    return request.app.state.web_deps


def user_root(workspace_base: Path, user_id: str) -> Path:
    # user_id 可能已经带了 "web_" 前缀（JWT sub 生成时加的），
    # 也可能没有（旧 token 或非 web 用户）。避免重复拼接成 web_web_xxx。
    root_dir = user_id if user_id.startswith("web_") else f"web_{user_id}"
    root = workspace_base / root_dir
    root.mkdir(parents=True, exist_ok=True)
    return root
