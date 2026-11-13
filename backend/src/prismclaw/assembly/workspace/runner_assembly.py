from __future__ import annotations

import dataclasses
import json
import logging
import os
from pathlib import Path
from typing import Any

from prismclaw.engine.execution.skills.runtime import SkillRuntime
from prismclaw.engine.services.llm.client import LiteLLMClient
from prismclaw.schema.llm.errors import LLMError
from prismclaw.engine.services.llm.provider_resolver import resolve_provider_for_model
from prismclaw.engine.execution.loop.deps import AgentRunnerDeps, DreamBundle, PersistenceBundle, PluginBundle
from prismclaw.engine.runtime.client import WorkspaceRuntimeClient
from prismclaw.schema.services.env_types import ExecutionEnv
from prismclaw.engine.task_profile.service import TaskProfileService
from prismclaw.engine.execution.checkpoint import RunSnapshotStore
from prismclaw.engine.toolkit.registry.builtin import (
    register_builtin_tools,
    register_tier2_builtin_tools,
)
from prismclaw.engine.toolkit.registry.registry import ToolRegistry
from prismclaw.engine.toolkit.file.workspace import WorkspaceResolver
from prismclaw.engine.compression.context_engine import DefaultContextEngine
from prismclaw.engine.hooks import HookRegistry
from prismclaw.assembly.plugins.runtime_assembly import (
    PluginRuntimeAssembly,
    build_plugin_runtime,
)
from prismclaw.runtime.memory_aftermath import (
    build_memory_aftermath_boundary,
)
from prismclaw.runtime.capability_refresh import (
    build_capability_catalog,
    build_subagent_registry,
    register_subagent_tools,
    schedule_mcp_capability_refresh,
)
from prismclaw.runtime.skills.mcp_skills import mcp_prompt_resolver, mcp_skill_manifests_builder
from prismclaw.infra.settings import Settings
from prismclaw.infra.task_manager import TaskManager
from prismclaw.schema.config.config import AgentRunConfig, WorkspaceConfig
from prismclaw.schema.storage.storage_types import MemoryStore, SessionStore, WorkspaceStore


def _build_runtime_client() -> ExecutionEnv:
    """构造执行客户端：生产=HTTP 容器，开发/测试=进程内同逻辑传输。

    返回**非 None** 的客户端，工具侧不再有任何本地 subprocess 降级分支——
    本机（开发/测试）与生产复用同一份 :class:`RuntimeExecutor` 执行核心，
    仅传输形态不同（进程内直接调用 vs HTTP 到 Runtime-Agent 容器）。

    行为：
      - 配置了 ``PRISMCLAW_RUNTIME_AGENT_URLS`` → :class:`WorkspaceRuntimeClient`
        （HTTP transport，容器内执行，生产/远程统一路径）；
      - 未配置且**非生产** → :class:`InProcessRuntimeClient`（本机直接调用
        RuntimeExecutor，不引入任何分叉的降级实现）；
      - 未配置且**生产** → fail-fast 抛错，避免静默退回进程内执行而绕过
        容器隔离（即「无降级」）。
    """
    deployment_env = os.getenv("PRISMCLAW_DEPLOYMENT_ENV", "development").lower()
    env_map = os.getenv("PRISMCLAW_RUNTIME_AGENT_URLS")
    if env_map:
        try:
            agent_urls = json.loads(env_map)
        except json.JSONDecodeError:
            logging.getLogger(__name__).warning(
                "PRISMCLAW_RUNTIME_AGENT_URLS is not valid JSON: %r", env_map
            )
            agent_urls = None
        if isinstance(agent_urls, dict) and agent_urls:
            return WorkspaceRuntimeClient(
                agent_urls=agent_urls,
                hmac_secret=os.getenv("PRISMCLAW_RUNTIME_HMAC_SECRET", "dev-secret"),
            )
    # 未配置 HTTP agent
    if deployment_env == "production":
        raise RuntimeError(
            "Production requires PRISMCLAW_RUNTIME_AGENT_URLS to be set; "
            "refusing to fall back to in-process execution (would bypass "
            "container isolation)."
        )
    from prismclaw.engine.runtime.in_process_client import InProcessRuntimeClient

    # 注入沙箱策略到进程内传输
    from prismclaw.engine.security.sandbox.state import resolve_sandbox_state
    from prismclaw.schema.config.config import SandboxConfig
    try:
        _sb_state = resolve_sandbox_state(SandboxConfig(enforce=True))
        _sb_policy = _sb_state.policy if hasattr(_sb_state.policy, 'wrap_bash_command') else None
    except Exception:
        _sb_policy = None

    return InProcessRuntimeClient(sandbox_policy=_sb_policy)

logger = logging.getLogger(__name__)


@dataclasses.dataclass(slots=True)
class KernelRuntimeBundle:
    llm: LiteLLMClient
    resolver: WorkspaceResolver
    tools: ToolRegistry
    hooks: HookRegistry
    config: AgentRunConfig
    task_profile_service: TaskProfileService
    bootstrap_files: list[str]


@dataclasses.dataclass(slots=True)
class DreamRuntimeBundle:
    dreaming_engine: Any = None
    dream_scheduler: Any = None
    dream_review_service: Any = None
    dream_boundary: Any = None
    dream_ops: Any = None
    dream_audit_log: Any = None
    dream_state_store: Any = None
    dream_candidate_store: Any = None
    dream_governance_store: Any = None
    retrieval_audit_log: Any = None
    edge_store: Any = None


@dataclasses.dataclass(slots=True)
class MemoryRuntimeBundle:
    context_engine: DefaultContextEngine
    nudge_hook: Any = None


def _build_provider_specs(providers: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    """Convert infra.settings.ProviderSettings.models to ProviderModelSpecs format."""
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for provider_name, provider in providers.items():
        models: dict[str, dict[str, Any]] = {}
        for model_id, entry in getattr(provider, "models", {}).items():
            spec: dict[str, Any] = {}
            ctx = getattr(entry, "context_window", None)
            max_out = getattr(entry, "max_output_tokens", None)
            if ctx is not None:
                spec["context_window"] = int(ctx)
            if max_out is not None:
                spec["max_output_tokens"] = int(max_out)
            if spec:
                models[str(model_id)] = spec
        if models:
            result[provider_name] = models
    return result


def _configure_token_counting(settings: Settings) -> None:
    """按 ``token_counting`` 配置安装全局计数插件（默认 heuristic）。

    计数插件必须在任何预算/压缩/埋点调用之前安装；配置错误时 fail-fast，
    避免静默退回默认系数导致预算口径与配置不一致。
    """
    from prismclaw.token_counting import build_token_counter, set_token_counter

    cfg = settings.token_counting
    options = cfg.heuristic.model_dump() if cfg.provider == "heuristic" else {}
    try:
        counter = build_token_counter(cfg.provider, **options)
    except ValueError as exc:
        raise RuntimeError(
            f"token_counting.provider='{cfg.provider}' is not registered. "
            f"Fix backend/configs/models.yaml before starting. {exc}"
        ) from exc
    set_token_counter(counter)


def build_kernel_runtime(settings: Settings) -> KernelRuntimeBundle:
    _configure_token_counting(settings)
    llm = LiteLLMClient(
        default_model=settings.models.default_model,
        providers=settings.models.providers,
        default_provider=settings.models.default_provider,
        unknown_prefix_policy=settings.models.unknown_prefix_policy,
    )
    if settings.models.providers:
        try:
            resolve_provider_for_model(
                settings.models.default_model,
                settings.models.providers,
                default_provider=settings.models.default_provider,
                unknown_prefix_policy=settings.models.unknown_prefix_policy,
            )
        except LLMError as exc:
            raise RuntimeError(
                f"models.default_model='{settings.models.default_model}' cannot be routed "
                f"to any configured provider. Fix backend/configs/models.yaml before starting. {exc}"
            ) from exc

    workspace_default = getattr(
        getattr(settings, "workspaces", None),
        "default",
        None,
    ) or str(Path.home() / "prismclaw-workspace")
    workspace_config = WorkspaceConfig(workspaces={"default": workspace_default})
    resolver = WorkspaceResolver(workspace_config)

    config = AgentRunConfig.model_validate(
        {
            "tool_mutation_policy": settings.agent.tool_mutation_policy,
            "context": settings.agent.context.model_dump(by_alias=True),
            "control": settings.agent.control.model_dump(),
            "timeouts": settings.agent.timeouts.model_dump(),
            "retry": settings.agent.retry.model_dump(),
            "run_loop": settings.agent.run_loop.model_dump(),
            "effort": settings.agent.effort.model_dump(),
            "vision": {
                "model": settings.vision.model,
                "api_key": settings.vision.api_key,
                "base_url": settings.vision.base_url,
                "timeout_seconds": settings.vision.timeout_seconds,
                "tokens_per_image": settings.vision.tokens_per_image,
                "max_images_inline": settings.vision.max_images_inline,
            },
            "evolution": {
                "enabled": settings.evolution.enabled,
                "extraction_model": settings.evolution.extraction_model,
                "max_candidates": settings.evolution.max_candidates,
                "min_tool_calls_for_extraction": settings.evolution.min_tool_calls_for_extraction,
                "dedup_overlap_threshold": settings.evolution.dedup_overlap_threshold,
                "max_sops_per_extraction": settings.evolution.max_sops_per_extraction,
                "description_max_chars": settings.evolution.description_max_chars,
                "procedure_max_chars": settings.evolution.procedure_max_chars,
                "curator_enabled": settings.evolution.curator.enabled,
                "dream_enabled": settings.evolution.dream.enabled,
                "curator": {
                    "enabled": settings.evolution.curator.enabled,
                    "check_interval_seconds": settings.evolution.curator.check_interval_seconds,
                    "interval_seconds": settings.evolution.curator.interval_seconds,
                    "stale_after_days": settings.evolution.curator.stale_after_days,
                    "archive_after_days": settings.evolution.curator.archive_after_days,
                    "graduation_enabled": settings.evolution.curator.graduation_enabled,
                    "graduation_mode": settings.evolution.curator.graduation_mode,
                    "graduation_model": settings.evolution.curator.graduation_model,
                    "promotion_min_use_count": settings.evolution.curator.promotion_min_use_count,
                    "promotion_min_days": settings.evolution.curator.promotion_min_days,
                    "llm_review_enabled": settings.evolution.curator.llm_review_enabled,
                    "llm_review_model": settings.evolution.curator.llm_review_model,
                    "llm_review_interval_seconds": settings.evolution.curator.llm_review_interval_seconds,
                    "llm_review_actions": list(settings.evolution.curator.llm_review_actions),
                    "llm_review_max_batch": settings.evolution.curator.llm_review_max_batch,
                },
                "dream": {
                    "enabled": settings.evolution.dream.enabled,
                    "idle_check_interval_seconds": settings.evolution.dream.idle_check_interval_seconds,
                    "idle_threshold_seconds": settings.evolution.dream.idle_threshold_seconds,
                    "nightly_check_interval_seconds": settings.evolution.dream.nightly_check_interval_seconds,
                    "nightly_timezone": settings.evolution.dream.nightly_timezone,
                    "nightly_start_hour_local": settings.evolution.dream.nightly_start_hour_local,
                    "nightly_end_hour_local": settings.evolution.dream.nightly_end_hour_local,
                    "nightly_catch_up_grace_minutes": settings.evolution.dream.nightly_catch_up_grace_minutes,
                    "nightly_max_sessions": settings.evolution.dream.nightly_max_sessions,
                    "multi_worker_scan_policy": settings.evolution.dream.multi_worker_scan_policy,
                    "publish_channel": settings.evolution.dream.publish_channel,
                    "memory_publish_channel": settings.evolution.dream.memory_publish_channel,
                    "artifact_publish_channel": settings.evolution.dream.artifact_publish_channel,
                    "canary_rollout_percentage": settings.evolution.dream.canary_rollout_percentage,
                    "canary_rollout_salt": settings.evolution.dream.canary_rollout_salt,
                    "shadow_publish_enabled": settings.evolution.dream.shadow_publish_enabled,
                    "shadow_memory_publish_enabled": settings.evolution.dream.shadow_memory_publish_enabled,
                    "shadow_artifact_publish_enabled": settings.evolution.dream.shadow_artifact_publish_enabled,
                    "tiers_enabled": settings.evolution.dream.tiers_enabled,
                    "contest_exit_days": settings.evolution.dream.contest_exit_days,
                    "deep_merge_min_positive": settings.evolution.dream.deep_merge_min_positive,
                    "correction_learning_enabled": settings.evolution.dream.correction_learning_enabled,
                },
            },
            "subagent": {
                "enabled": settings.subagent.enabled,
                "max_depth": settings.subagent.max_depth,
                "max_subagent_depth": settings.subagent.max_subagent_depth,
                "asset_subagents_dir": settings.subagent.asset_subagents_dir,
                "default_max_turns": settings.subagent.default_max_turns,
                "default_timeout_seconds": settings.subagent.default_timeout_seconds,
                "max_parallel_per_turn": settings.subagent.max_parallel_per_turn,
                "handoff_enabled": settings.subagent.handoff_enabled,
                "max_handoff_chain_length": settings.subagent.max_handoff_chain_length,
                "pending_handoff_ttl_turns": settings.subagent.pending_handoff_ttl_turns,
                "handoff_reverse_lookup_enabled": settings.subagent.handoff_reverse_lookup_enabled,
            },
            "sandbox": {
                "enforce": settings.sandbox.enforce,
                "default_filesystem": {
                    "allow_write": list(settings.sandbox.default_filesystem.allow_write),
                    "deny_read": list(settings.sandbox.default_filesystem.deny_read),
                    "deny_write": list(settings.sandbox.default_filesystem.deny_write),
                },
                "default_network": {
                    "allowed_domains": list(settings.sandbox.default_network.allowed_domains),
                    "denied_domains": list(settings.sandbox.default_network.denied_domains),
                },
                "default_env_allowlist": list(settings.sandbox.default_env_allowlist),
            },
            "affinity": {
                "enabled": settings.affinity.enabled,
                "ttl_seconds": settings.affinity.ttl_seconds,
                "heartbeat_interval": settings.affinity.heartbeat_interval,
                "stale_threshold": settings.affinity.stale_threshold,
                "forward_prefix": settings.affinity.forward_prefix,
                "renewal_interval": settings.affinity.renewal_interval,
            },
            "provider_specs": {
                "providers": _build_provider_specs(settings.models.providers),
            },
        }
    )
    bootstrap_files = list(
        getattr(getattr(settings, "workspaces", None), "bootstrap_files", None) or ["AGENTS.md"]
    )
    # Wire engine-layer configs (eliminate Service Locator anti-pattern)
    from prismclaw.engine.toolkit.file.read_image import set_vision_config
    from prismclaw.engine.execution.request.model_fallback import hydrate_spec_registry
    from prismclaw.schema.config.config import VisionConfig, ProviderModelSpecs, ProviderModelSpecEntry

    vision_cfg = VisionConfig(
        model=settings.vision.model,
        api_key=settings.vision.api_key,
        base_url=settings.vision.base_url,
        timeout_seconds=settings.vision.timeout_seconds,
    )
    set_vision_config(vision_cfg)

    provider_specs_data: dict[str, dict[str, ProviderModelSpecEntry]] = {}
    for pname, psettings in settings.models.providers.items():
        entries: dict[str, ProviderModelSpecEntry] = {}
        for mid, mentry in psettings.models.items():
            entries[mid] = ProviderModelSpecEntry(
                context_window=mentry.context_window,
                max_output_tokens=mentry.max_output_tokens,
            )
        provider_specs_data[pname] = entries
    hydrate_spec_registry(ProviderModelSpecs(providers=provider_specs_data))

    return KernelRuntimeBundle(
        llm=llm,
        resolver=resolver,
        tools=ToolRegistry(),
        hooks=HookRegistry(),
        config=config,
        task_profile_service=TaskProfileService(),
        bootstrap_files=bootstrap_files,
    )


def build_dream_runtime(
    settings: Settings,
    *,
    session_factory: Any,
    memory_store: MemoryStore | None,
    session_store: SessionStore,
    llm: LiteLLMClient,
    task_manager: TaskManager | None,
    lock_manager: Any,
    worker_registry: Any,
    dream_dispatch_queue: Any,
    db_engine: Any = None,
) -> DreamRuntimeBundle:
    if memory_store is None:
        from prismclaw.engine.dream.runtime_client import NullDreamBoundary

        return DreamRuntimeBundle(dream_boundary=NullDreamBoundary())
    if not settings.evolution.enabled or not settings.evolution.dream.enabled:
        from prismclaw.engine.dream.runtime_client import NullDreamBoundary

        return DreamRuntimeBundle(dream_boundary=NullDreamBoundary())

    if task_manager is None or lock_manager is None:
        from prismclaw.engine.dream.runtime_client import NullDreamBoundary

        logger.warning("dream runtime disabled: task_manager/lock_manager unavailable")
        return DreamRuntimeBundle(dream_boundary=NullDreamBoundary())

    from prismclaw.engine.dream.runtime_client import NullDreamBoundary
    from prismclaw.engine.dream.engine import DreamEngine
    from prismclaw.engine.dream.dispatch.lock import DistributedDreamLockProvider
    from prismclaw.engine.dream.governance.ops import InProcessDreamAdminOps
    from prismclaw.engine.dream.governance.review_service import DreamReviewService
    from prismclaw.engine.dream.dispatch.scheduler import DreamScheduler
    from prismclaw.engine.dream.state.status_service import DreamStatusService
    from prismclaw.persistence.dream.factory import create_dream_stores
    from prismclaw.persistence.audit.factory import create_audit_logger

    try:
        stores = create_dream_stores(session_factory)
        dream_audit_log = stores.audit_logger
        dream_state_store = stores.state_store
        dream_candidate_store = stores.candidate_store
        dream_governance_store = stores.governance_store
        decision_audit_store = stores.decision_audit_store
        retrieval_audit_log = create_audit_logger(session_factory)

        # E5: 记忆图谱边存储。独立于 MemoryStore，需要 db_engine。
        edge_store = None
        if db_engine is not None and settings.memory.memory_graph_enabled:
            from prismclaw.persistence.memory.edge_store import EdgeStore
            from prismclaw.engine.security.dlp.scanner import DlpScanner

            edge_store = EdgeStore(db_engine, dlp_scanner=DlpScanner())

        dreaming_engine = DreamEngine(
            memory_store=memory_store,
            session_store=session_store,
            llm_client=llm,
            audit_log=dream_audit_log,
            candidate_store=dream_candidate_store,
            governance_store=dream_governance_store,
            memory_publish_channel=settings.evolution.dream.effective_memory_publish_channel,
            artifact_publish_channel=settings.evolution.dream.effective_artifact_publish_channel,
            shadow_publish_enabled=settings.evolution.dream.shadow_publish_enabled,
            shadow_memory_publish_enabled=(
                settings.evolution.dream.effective_shadow_memory_publish_enabled
            ),
            shadow_artifact_publish_enabled=(
                settings.evolution.dream.effective_shadow_artifact_publish_enabled
            ),
            canary_rollout_percentage=settings.evolution.dream.effective_canary_rollout_percentage,
            canary_rollout_salt=settings.evolution.dream.canary_rollout_salt,
            tiers_enabled=bool(settings.evolution.dream.tiers_enabled),
            contest_exit_days=int(settings.evolution.dream.contest_exit_days),
            deep_merge_min_positive=int(settings.evolution.dream.deep_merge_min_positive),
            memory_forgetting_budget_entries=int(settings.memory.memory_forgetting_budget_entries),
            correction_learning_enabled=bool(settings.evolution.dream.correction_learning_enabled),
            edge_store=edge_store,
        )

        dream_review_service = DreamReviewService(
            dreaming_engine,
            status_service=DreamStatusService(
                state_store=dream_state_store,
                audit_log=dream_audit_log,
                governance_store=dream_governance_store,
                dispatch_reader=dream_dispatch_queue,
                memory_store=memory_store,
            ),
            memory_store=memory_store,
            session_store=session_store,
            state_store=dream_state_store,
            task_manager=task_manager,
            decision_audit_store=decision_audit_store,
            dispatch_queue=dream_dispatch_queue,
            governance_store=dream_governance_store,
        )

        lock_provider = DistributedDreamLockProvider(lock_manager, task_manager=task_manager)
        dream_scheduler = DreamScheduler(
            dreaming_engine=dreaming_engine,
            settings=settings.evolution.dream,
            state_store=dream_state_store,
            lock_provider=lock_provider,
            task_manager=task_manager,
            worker_registry=worker_registry,
            worker_stale_threshold_s=float(settings.affinity.stale_threshold),
            dispatch_queue=dream_dispatch_queue,
        )
        dream_scheduler.start(task_manager)

        from prismclaw.engine.dream.runtime_client import InProcessDreamBoundary
        boundary = InProcessDreamBoundary(
            scheduler=dream_scheduler,
            review_service=dream_review_service,
            state_store=dream_state_store,
        )
        dream_ops = InProcessDreamAdminOps(dream_review_service)

        return DreamRuntimeBundle(
            dreaming_engine=dreaming_engine,
            dream_scheduler=dream_scheduler,
            dream_review_service=dream_review_service,
            dream_ops=dream_ops,
            dream_audit_log=dream_audit_log,
            dream_state_store=dream_state_store,
            dream_candidate_store=dream_candidate_store,
            dream_governance_store=dream_governance_store,
            retrieval_audit_log=retrieval_audit_log,
            dream_boundary=boundary,
            edge_store=edge_store,
        )
    except Exception:
        logger.warning("dream runtime startup failed; continuing without dream", exc_info=True)
        return DreamRuntimeBundle(dream_boundary=NullDreamBoundary())


def build_memory_runtime(
    settings: Settings,
    *,
    workspace_store: WorkspaceStore | None,
    session_store: SessionStore,
    task_manager: TaskManager | None,
    memory_store: MemoryStore | None,
    redis_client: Any,
    llm: LiteLLMClient,
    dream_runtime: DreamRuntimeBundle,
    memory_gateway: Any = None,
) -> MemoryRuntimeBundle:
    nudge_hook = None
    if memory_store is not None:
        if task_manager is None:
            raise ValueError("memory runtime requires task_manager and lock_manager")
        from prismclaw.engine.hooks.memory_nudge_hook import MemoryNudgeHook

        nudge_hook = MemoryNudgeHook(interval=10)

    # SDD B1: one AttributionRecorder per app instance, shared by the
    # retriever (injection bookkeeping) and the aftermath (turn sealing +
    # validation signals). Only active when memory.attribution_enabled.
    attribution_recorder = None
    if memory_store is not None and bool(
        getattr(settings.memory, "attribution_enabled", False)
    ):
        from prismclaw.engine.memory.attribution import AttributionRecorder

        attribution_recorder = AttributionRecorder(memory_store)

    # SDD D9/T1-5 闭环：无归因环境的最小验证信号簿记。独立于
    # attribution_enabled——生产默认 False 时 curator 毕业的验证信号路径
    # （路径 B）与 use_count 回写（路径 A）仍可持续供给。
    validation_ledger = None
    if memory_store is not None:
        from prismclaw.engine.memory.validation_signals import ValidationSignalLedger

        validation_ledger = ValidationSignalLedger(memory_store)

    aftermath_boundary = build_memory_aftermath_boundary(
        memory_store=memory_store,
        session_store=session_store,
        task_manager=task_manager,
        redis_client=redis_client,
        llm_client=llm,
        evolution_settings=settings.evolution,
        nudge_hook=nudge_hook,
        dream_boundary=dream_runtime.dream_boundary or dream_runtime.dream_scheduler,
        dream_candidate_store=dream_runtime.dream_candidate_store,
        attribution_recorder=attribution_recorder,
        memory_gateway=memory_gateway,
        edge_store=dream_runtime.edge_store,
        validation_ledger=validation_ledger,
    )

    context_engine = DefaultContextEngine(
        workspace_store=workspace_store,
        bootstrap_files=list(
            getattr(getattr(settings, "workspaces", None), "bootstrap_files", None) or ["AGENTS.md"]
        ),
        memory_store=memory_store,
        memory_settings=settings.memory,
        keep_recent_tokens=settings.agent.context.compaction.history.keep_recent_tokens,
        threshold=settings.agent.context.compaction.predictive_threshold,
        chunk_token_budget=settings.agent.context.compaction.chunk_token_budget,
        compaction_timeout_s=settings.agent.context.compaction.timeout_seconds,
        compaction_config=settings.agent.context.compaction,
        summarize=_build_llm_summarizer(llm),
        session_store=session_store,
        llm_client=llm,
        evolution_settings=settings.evolution,
        retrieval_audit_log=dream_runtime.retrieval_audit_log,
        aftermath_boundary=aftermath_boundary,
        attribution_recorder=attribution_recorder,
        edge_store=dream_runtime.edge_store,
        validation_ledger=validation_ledger,
    )
    return MemoryRuntimeBundle(context_engine=context_engine, nudge_hook=nudge_hook)


def _build_llm_summarizer(llm: LiteLLMClient) -> Any:
    """LLM-backed summarizer callable for the compaction SummaryEngine.

    SDD context-governance W11 (2026-08-21, root cause of "compaction
    summaries lose user-stated facts"): the production assembly never wired
    a ``summarize`` callable into the context engine, so
    ``SummaryEngine.summarize_messages`` took the ``_summarize is None``
    fast path and EVERY production compaction emitted the lossy
    ``fallback_summary`` (last-5-user-messages + truncated response
    previews). The W8 structured template — including the mandatory
    "user-stated facts" carry-forward section — could never engage. This
    adapter mirrors the one in ``core/memory_archive.py``.
    """

    from prismclaw.schema.llm.request import PackedLLMRequest

    async def _summarize(
        payload: list[dict[str, Any]],
        *,
        model: str | None = None,
    ) -> str:
        response = await llm.complete(
            request=PackedLLMRequest(
                messages=payload,
                system="",
                context_payload=None,
                cache_strategy={},
            ),
            model=model,
        )
        return response.text

    return _summarize


def _validate_memory_runtime_prerequisites(
    *,
    memory_store: MemoryStore | None,
    redis_client: Any,
    task_manager: TaskManager | None,
    lock_manager: Any,
) -> None:
    if memory_store is None:
        return
    if redis_client is None:
        raise ValueError(
            "memory runtime requires redis_client. "
            "Ensure Redis is configured and reachable (redis.url in backend/configs/infra.yaml)."
        )
    if task_manager is None or lock_manager is None:
        raise ValueError("memory runtime requires task_manager and lock_manager")


def _build_summarize_fn(llm: LiteLLMClient) -> Any:
    """Build a per-result LLM summarization callback from the LLM stream function.

    The returned callable matches ``async def summarize_fn(prompt: str) -> str``.
    It uses ``llm.complete()`` (non-streaming) for a single-turn summary.
    """
    from prismclaw.schema.llm.request import PackedLLMRequest

    async def _summarize(prompt: str) -> str:
        response = await llm.complete(
            request=PackedLLMRequest(
                messages=[{"role": "user", "content": prompt}],
                system="",
                context_payload=None,
                cache_strategy={},
            ),
        )
        return response.text or ""

    return _summarize


def assemble_runner_deps(
    *,
    settings: Settings,
    session_store: SessionStore,
    run_snapshot_store: RunSnapshotStore | None,
    workspace_store: WorkspaceStore | None,
    task_manager: TaskManager | None,
    memory_store: MemoryStore | None,
    redis_client: Any,
    lock_manager: Any,
    mcp_death_handler: Any,
    external_tool_registrar: Any,
    kernel_runtime: KernelRuntimeBundle,
    plugin_runtime: PluginRuntimeAssembly,
    memory_runtime: MemoryRuntimeBundle,
    dream_runtime: DreamRuntimeBundle,
    trajectory_persistence: Any = None,
    memory_gateway: Any = None,
) -> AgentRunnerDeps:
    tools = kernel_runtime.tools
    subagent_workspace_path = kernel_runtime.resolver.resolve("default")
    subagent_registry = None
    if settings.subagent.enabled:
        # 必须复用 plugin_runtime 已构造的项目能力布局：settings.subagent.
        # asset_subagents_dir 是绝对路径(~/.prismclaw/assets/agents)，不传
        # capability_layout 时 SubAgentRegistry 会把它拼到 workspace 默认目录
        # （~/.prismclaw/workspaces）下，该目录不存在 → 项目子代理扫描结果为 0，
        # 模型可见清单里只剩插件 declarative agents。
        subagent_registry = build_subagent_registry(
            settings,
            workspace_path=subagent_workspace_path,
            capability_layout=plugin_runtime.capability_layout,
        )
    capability_catalog = build_capability_catalog(
        workspace_path=subagent_workspace_path,
        tool_registry=tools,
        skill_provider=plugin_runtime.skill_provider,
        extension_manager=plugin_runtime.extension_manager,
        subagent_registry=subagent_registry,
        mcp_skill_manifests_builder=mcp_skill_manifests_builder(
            plugin_runtime.runtime_mcp_manager
        ),
    )
    ext_mgr = plugin_runtime.extension_manager
    from prismclaw.runtime.skills.parser import parse_skill_file
    skill_runtime = SkillRuntime(
        capability_catalog=capability_catalog,
        mcp_prompt_resolver=mcp_prompt_resolver(plugin_runtime.runtime_mcp_manager),
        descriptor_registry=ext_mgr._descriptor_registry if ext_mgr else None,
        skill_parser=parse_skill_file,
    )
    # 同步插件级指令（PRISMCLAW.md）到 SkillRuntime
    if ext_mgr is not None and hasattr(ext_mgr, "iter_plugin_descriptors"):
        for descriptor in ext_mgr.iter_plugin_descriptors():
            instr = getattr(descriptor, "plugin_instructions", None)
            pname = getattr(descriptor, "plugin_name", None)
            if instr and pname:
                skill_runtime.register_plugin_instructions(pname, instr)
    register_builtin_tools(
        tools,
        kernel_runtime.resolver,
        capability_catalog,
        skill_runtime,
    )

    register_tier2_builtin_tools(
        tools,
        kernel_runtime.resolver,
        settings.builtin_tools,
        mcp_manager=plugin_runtime.runtime_mcp_manager,
    )

    if settings.scheduler.enabled and settings.scheduler.dialogue_creation_enabled:
        from prismclaw.engine.toolkit.misc.scheduled_task import ScheduledTaskTool

        tools.register(ScheduledTaskTool())

    hooks = kernel_runtime.hooks
    if memory_store is not None:
        from prismclaw.engine.hooks.working_memory_hook import WorkingMemoryHook
        from prismclaw.engine.toolkit.memory.memory_tools import MemoryTool
        from prismclaw.engine.toolkit.memory.update_working_memory import (
            UpdateWorkingMemoryTool,
        )

        tools.register(MemoryTool(memory_store, session_store, gateway=memory_gateway))
        tools.register(UpdateWorkingMemoryTool(redis_client))
        hooks.register(WorkingMemoryHook(redis_client))

        if memory_runtime.nudge_hook is not None:
            hooks.register(memory_runtime.nudge_hook)

        if settings.evolution.enabled:
            from prismclaw.engine.hooks.sop_tracker_hook import SopCandidateTracker

            hooks.register(
                SopCandidateTracker(
                    redis_client,
                    kernel_runtime.config.evolution,
                    task_manager=task_manager,
                    memory_store=memory_store,
                    session_store=session_store,
                    llm_client=kernel_runtime.llm,
                    nudge_hook=memory_runtime.nudge_hook,
                )
            )

    from prismclaw.engine.hooks.steer_hook import SteerHook

    hooks.register(SteerHook())

    if plugin_runtime.runtime_mcp_manager is not None:
        plugin_runtime.runtime_mcp_manager.attach_and_register(tools)
    elif external_tool_registrar is not None:
        external_tool_registrar(tools)

    handoff_supervisor: Any = None
    register_subagent_tools(
        tools,
        settings=settings,
        capability_catalog=capability_catalog,
        subagent_registry=subagent_registry,
        llm_client=kernel_runtime.llm,
        memory_store=memory_store,
        skill_runtime=skill_runtime,
    )
    if settings.subagent.enabled and capability_catalog.count_agent_entries() > 0:
        from prismclaw.engine.execution.handoff.store import PendingHandoffStore
        from prismclaw.engine.execution.handoff.supervisor import HandoffSupervisor

        if settings.subagent.handoff_enabled:
            handoff_store = PendingHandoffStore(
                redis_client=None,
                ttl_seconds=(
                    settings.subagent.pending_handoff_ttl_turns * 600
                ),
                reverse_lookup_enabled=(
                    settings.subagent.handoff_reverse_lookup_enabled
                ),
            )
            handoff_supervisor = HandoffSupervisor(
                store=handoff_store,
                max_handoff_chain_length=settings.subagent.max_handoff_chain_length,
            )

    from prismclaw.engine.execution.loop.tree_writer import publish_compaction_node
    from prismclaw.schema.agent.callbacks import AgentCallbacks

    deps = AgentRunnerDeps(
        callbacks=AgentCallbacks(
            on_compaction_node=publish_compaction_node,
            on_mcp_death=mcp_death_handler,
        ),
        llm=kernel_runtime.llm,
        tools=tools,
        context_engine=memory_runtime.context_engine,
        hooks=hooks,
        session_store=session_store,
        config=kernel_runtime.config,
        workspace_store=workspace_store,
        skill_provider=plugin_runtime.skill_provider,
        skill_runtime=skill_runtime,
        capability_catalog=capability_catalog,
        task_manager=task_manager,
        lock_manager=lock_manager,
        handoff_supervisor=handoff_supervisor,
        task_profile_service=kernel_runtime.task_profile_service,
        dream=DreamBundle(
            review_service=dream_runtime.dream_review_service,
            boundary=dream_runtime.dream_boundary,
            ops=dream_runtime.dream_ops,
        ),
        hook_dispatcher=plugin_runtime.hook_dispatcher,
        plugin=PluginBundle(
            extension_manager=plugin_runtime.extension_manager,
            runtime_mcp_manager=plugin_runtime.runtime_mcp_manager,
            lifecycle_manager=plugin_runtime.plugin_lifecycle_manager,
        ),
        persistence=PersistenceBundle(
            run_snapshot_store=run_snapshot_store,
            trajectory_persistence=trajectory_persistence,
        ),
        runtime_client=_build_runtime_client(),
    )
    prs = kernel_runtime.config.context.compaction.tool_results.per_result_summary
    if prs.enabled and kernel_runtime.llm is not None:
        deps.summarize_fn = _build_summarize_fn(kernel_runtime.llm)
    return deps


async def create_agent_runner_deps(
    settings: Settings,
    session_store: SessionStore,
    run_snapshot_store: RunSnapshotStore | None = None,
    workspace_store: WorkspaceStore | None = None,
    task_manager: TaskManager | None = None,
    memory_store: MemoryStore | None = None,
    redis_client=None,
    lock_manager=None,
    mcp_death_handler=None,
    external_tool_registrar=None,
    mcp_manager=None,
    worker_registry=None,
    dream_dispatch_queue=None,
    session_factory: Any = None,
    trajectory_persistence: Any = None,
    project_root: Path | str | None = None,
    db_engine: Any = None,
) -> AgentRunnerDeps:
    kernel_runtime = build_kernel_runtime(settings)
    _validate_memory_runtime_prerequisites(
        memory_store=memory_store,
        redis_client=redis_client,
        task_manager=task_manager,
        lock_manager=lock_manager,
    )
    workspace_default = getattr(getattr(settings, "workspaces", None), "default", None) or str(
        Path.home() / "prismclaw-workspace"
    )
    plugin_runtime = await build_plugin_runtime(
        settings,
        workspace_default=workspace_default,
        project_root=project_root,
        task_manager=task_manager,
        base_mcp_manager=mcp_manager,
    )
    dream_runtime = build_dream_runtime(
        settings,
        session_factory=session_factory,
        memory_store=memory_store,
        session_store=session_store,
        llm=kernel_runtime.llm,
        task_manager=task_manager,
        lock_manager=lock_manager,
        worker_registry=worker_registry,
        dream_dispatch_queue=dream_dispatch_queue,
        db_engine=db_engine,
    )
    # SDD B2: one write-arbitration gateway per app — mode off/shadow/active
    # via memory.gateway_mode; built here (needs llm + lock_manager) and
    # shared by the memory tool and the aftermath (auto-extracted chat facts).
    memory_gateway = None
    if memory_store is not None and str(
        getattr(settings.memory, "gateway_mode", "off") or "off"
    ) != "off":
        from prismclaw.engine.memory.gateway import MemoryWriteGateway

        memory_gateway = MemoryWriteGateway(
            memory_store=memory_store,
            llm_client=kernel_runtime.llm,
            lock_manager=lock_manager,
            memory_settings=settings.memory,
        )
    memory_runtime = build_memory_runtime(
        settings,
        workspace_store=workspace_store,
        session_store=session_store,
        task_manager=task_manager,
        memory_store=memory_store,
        redis_client=redis_client,
        llm=kernel_runtime.llm,
        dream_runtime=dream_runtime,
        memory_gateway=memory_gateway,
    )
    deps = assemble_runner_deps(
        settings=settings,
        session_store=session_store,
        run_snapshot_store=run_snapshot_store,
        workspace_store=workspace_store,
        task_manager=task_manager,
        memory_store=memory_store,
        redis_client=redis_client,
        lock_manager=lock_manager,
        mcp_death_handler=mcp_death_handler,
        external_tool_registrar=external_tool_registrar,
        kernel_runtime=kernel_runtime,
        plugin_runtime=plugin_runtime,
        memory_runtime=memory_runtime,
        dream_runtime=dream_runtime,
        trajectory_persistence=trajectory_persistence,
        memory_gateway=memory_gateway,
    )
    # MCP servers connect in a background supervisor; the catalog assembled
    # above cannot contain their prompt-type skills yet. Rebuild once the MCP
    # layer signals ready (event-driven one-shot, no polling).
    # capability_layout 必须一并透传：刷新路径会重建 subagent registry，缺它会
    # 把项目子代理再次扫成 0（覆盖掉启动时装配的结果）。
    schedule_mcp_capability_refresh(
        deps,
        settings=settings,
        workspace_path=kernel_runtime.resolver.resolve("default"),
        extension_manager=plugin_runtime.extension_manager,
        skill_provider=plugin_runtime.skill_provider,
        capability_layout=plugin_runtime.capability_layout,
    )
    return deps
