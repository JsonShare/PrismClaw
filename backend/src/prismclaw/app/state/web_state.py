from __future__ import annotations

from typing import Any

from prismclaw.infra.settings import Settings


def _require_runtime_sandbox_policy(state: Any) -> Any:
    sandbox_policy = getattr(state, "sandbox_policy", None)
    if sandbox_policy is None:
        raise RuntimeError(
            "web runtime state requires app.state.sandbox_policy before wiring channel deps"
        )
    return sandbox_policy


def sync_web_runtime_state(
    app: Any,
    runner_deps: Any,
    *,
    settings: Settings | None = None,
) -> None:
    state = getattr(app, "state", None)
    if state is None:
        return

    web_deps = getattr(state, "web_deps", None)
    if web_deps is not None:
        web_deps.runner_deps = runner_deps
        web_deps.workspace_base = getattr(state, "workspace_base", None)
        web_deps.worker_registry = getattr(state, "worker_registry", None)
        web_deps.runtime_mode_snapshot = getattr(state, "runtime_mode_snapshot", None)
        web_deps.llm_client = getattr(runner_deps, "llm", None)
        web_deps.trajectory_persistence = getattr(state, "trajectory_persistence", None)
        from prismclaw.runtime.facade.services import PromptOptimizer
        web_deps.prompt_optimizer_factory = PromptOptimizer
        from prismclaw.runtime.default_chat_service import DefaultChatService
        web_deps.chat_service = DefaultChatService(deps=runner_deps)
        from prismclaw.runtime.facade.foundation import HotPathTelemetry
        web_deps.hotpath_telemetry = HotPathTelemetry()
        from prismclaw.app.state.office_review_impl import _OfficeReviewImpl
        web_deps.office_review = _OfficeReviewImpl()
        from prismclaw.runtime.skills.origin import (
            classify_skill_group_label,
            classify_skill_group_path,
            classify_skill_origin,
        )
        web_deps.skill_origin_fns = {
            "classify_skill_origin": classify_skill_origin,
            "classify_skill_group_path": classify_skill_group_path,
            "classify_skill_group_label": classify_skill_group_label,
        }
        from prismclaw.runtime.skills.parser import parse_skill_file
        web_deps.skill_parse_fn = parse_skill_file
        from prismclaw.runtime.skills.clawhub_client import create_client
        web_deps.skill_hub_client_factory = create_client
        from prismclaw.runtime.skills.installer import install_skill
        web_deps.skill_install_fn = install_skill
        from prismclaw.runtime.facade.llm import resolve_provider_for_model
        web_deps.resolve_provider_fn = resolve_provider_for_model
        from prismclaw.runtime.facade.runtime_mode import build_runtime_mode_snapshot
        web_deps.build_runtime_mode_snapshot_fn = build_runtime_mode_snapshot
        from prismclaw.runtime.facade.commands import run_extract
        from prismclaw.runtime.facade.services import format_extraction_result_zh
        web_deps.extract_fn = run_extract
        web_deps.format_extraction_fn = format_extraction_result_zh
        from prismclaw.runtime.facade.review import get_review_registry
        web_deps.review_registry_fn = get_review_registry
        from prismclaw.assembly.plugins.reload_service import reload_app_plugin_runtime
        web_deps.reload_plugin_fn = reload_app_plugin_runtime
        from prismclaw.app.gateway import takeover as _takeover_mod
        web_deps.takeover_fns = {
            "cluster_takeover_tasks": _takeover_mod.cluster_takeover_tasks,
            "evaluate_takeover_eligibility": _takeover_mod.evaluate_takeover_eligibility,
            "persisted_takeover_histories_for_ownerships": _takeover_mod.persisted_takeover_histories_for_ownerships,
            "latest_persisted_takeover_states_for_ownerships": _takeover_mod.latest_persisted_takeover_states_for_ownerships,
            "spawn_takeover_recovery": _takeover_mod.spawn_takeover_recovery,
            "takeover_reason_catalog": _takeover_mod.takeover_reason_catalog,
        }
        from prismclaw.runtime.facade.review import review_lifecycle as _review_lifecycle
        web_deps.review_lifecycle_fn = _review_lifecycle
        from prismclaw.runtime.facade.toolkit import build_file_change, get_tracking_store
        from prismclaw.runtime.facade.toolkit import upload_pipeline as _upload_pipeline
        web_deps.upload_fns = {
            "LocalSink": _upload_pipeline.LocalSink,
            "upload_files": _upload_pipeline.upload_files,
            "build_file_change": build_file_change,
            "get_tracking_store": get_tracking_store,
        }
        from prismclaw.runtime.facade.execution import normalize_clarify_questions
        from prismclaw.runtime.facade.review import (
            apply_office_ops,
        )
        from prismclaw.runtime.facade.review import (
            structured_office_fmt_from_path as fmt_from_path,
        )
        web_deps.review_utils = {
            "normalize_clarify_questions": normalize_clarify_questions,
            "apply_office_ops": apply_office_ops,
            "fmt_from_path": fmt_from_path,
        }
        from prismclaw.app.web.routers.office_review import _set_apply_office_ops
        _set_apply_office_ops(apply_office_ops)
        from prismclaw.assembly.runtime_coordinator import (
            get_app_workspace_runner_deps_async,
            get_app_workspace_runner_deps_state,
        )
        web_deps.workspace_runner_deps_fn = get_app_workspace_runner_deps_async
        web_deps.workspace_runner_deps_state_fn = get_app_workspace_runner_deps_state
        if settings is not None:
            web_deps.settings_full = settings
            web_deps.agent_settings = settings.agent
            web_deps.evolution_settings = (
                settings.evolution if settings.evolution.enabled else None
            )

        session_store = getattr(web_deps, "session_store", None)
        session_router = getattr(web_deps, "session_router", None)
        if session_store is not None and session_router is not None:
            from prismclaw.app.web.routes import set_web_deps

            trajectory_persistence = getattr(state, "trajectory_persistence", None)
            if trajectory_persistence is None:
                session_factory = getattr(state, "session_factory", None)
                if session_factory is not None:
                    from prismclaw.persistence.trajectory.pg_persistence import (
                        PgTrajectoryPersistence,
                    )
                    from prismclaw.schema.trajectory import init_state as _traj_init
                    from prismclaw.schema.trajectory import view as _traj_view
                    trajectory_persistence = PgTrajectoryPersistence(
                        session_factory,
                        state_initializer=_traj_init,
                        view_fn=_traj_view,
                    )

            set_web_deps(
                store=session_store,
                session_router=session_router,
                memory_store=getattr(web_deps, "memory_store", None),
                task_manager=getattr(web_deps, "task_manager", None),
                redis_client=getattr(web_deps, "redis_client", None),
                session_factory=session_factory,
                llm_client=getattr(web_deps, "llm_client", None),
                evolution_settings=getattr(web_deps, "evolution_settings", None),
                nudge_hook=getattr(web_deps, "nudge_hook", None),
                trajectory_persistence=trajectory_persistence,
            )

            # _routes_helpers 模块有独立的模块级全局变量，
            # 被 workspace.py 等路由使用，需要同步初始化。
            from prismclaw.app.web._routes_helpers import set_web_deps as _helpers_set_web_deps
            _helpers_set_web_deps(
                store=session_store,
                session_router=session_router,
                memory_store=getattr(web_deps, "memory_store", None),
                task_manager=getattr(web_deps, "task_manager", None),
                redis_client=getattr(web_deps, "redis_client", None),
                session_factory=session_factory,
                llm_client=getattr(web_deps, "llm_client", None),
                evolution_settings=getattr(web_deps, "evolution_settings", None),
                nudge_hook=getattr(web_deps, "nudge_hook", None),
            )

        if session_router is not None:
            from prismclaw.app.web.routers.openai_compat import set_openai_deps

            set_openai_deps(
                DefaultChatService(runner_deps),
                session_router,
                workspace_base=getattr(state, "workspace_base", None),
                web_settings=getattr(state, "web_settings", None),
                redis_client=getattr(state, "redis_client", None),
                sandbox_policy=_require_runtime_sandbox_policy(state),
            )
