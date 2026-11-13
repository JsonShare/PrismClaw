from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from prismclaw.engine.execution.loop.deps import AgentRunnerDeps
    from prismclaw.engine.execution.loop.runner import RunRequest
    from prismclaw.schema.session import SessionTree

from prismclaw.engine.compression.context_zones import zone_of

# --- extracted sub-modules ---
from prismclaw.engine.execution.query._catalog_builder import (
    _build_available_skills_catalog,
    _build_available_subagents_catalog,
    _session_follow_up_notes,
    _subagent_catalog_state,
    _tool_prompt_summaries,
)
from prismclaw.engine.execution.query._static_context_types import (
    StaticContextResult,
    StaticTurnShellSnapshot,
    _load_prior_replacement_state,
    _resolve_static_shell_cache,
    _shell_cache_key,
    _skill_catalog_fingerprint,
    _subagent_catalog_fingerprint,
)
from prismclaw.engine.execution.query._turn_helpers import _require_capability_catalog
from prismclaw.engine.execution.request.model_fallback import resolve_model_max_output
from prismclaw.engine.execution.request.system_prompt import (
    PromptInputs,
    build_frozen_prefix,
    build_identity_text,
    build_preferences_text,
)
from prismclaw.engine.foundation.telemetry.hotpath import HotPathTelemetry
from prismclaw.engine.security.gate.governance import runtime_notes_for
from prismclaw.schema.agent.types import WorkspaceMode
from prismclaw.schema.services.skills import SkillInvocationState
from prismclaw.schema.session.types import now_iso

logger = logging.getLogger(__name__)
_HOTPATH = HotPathTelemetry()


async def build_static_context_for_runner(
    *,
    request: Any,
    deps: Any,
    tree: Any,
    tool_workspace_path: Any,
    effective_model: str,
    mode: WorkspaceMode = "work",
    skill_invocation: SkillInvocationState | None = None,
    discovered_tools: set[str] | None = None,
    subagent_depth: int = 0,
) -> StaticContextResult:
    builder = StaticContextBuilder()
    return await builder.build(
        request=request,
        deps=deps,
        tree=tree,
        tool_workspace_path=tool_workspace_path,
        effective_model=effective_model,
        mode=mode,
        skill_invocation=skill_invocation,
        discovered_tools=discovered_tools,
        subagent_depth=subagent_depth,
    )


class StaticContextBuilder:
    async def build(
        self,
        *,
        request: RunRequest,
        deps: AgentRunnerDeps,
        tree: SessionTree,
        tool_workspace_path: str | None,
        effective_model: str,
        mode: WorkspaceMode = "work",
        skill_invocation: SkillInvocationState | None = None,
        discovered_tools: set[str] | None = None,
        subagent_depth: int = 0,
    ) -> StaticContextResult:
        started_at = time.monotonic()
        request_mode = "unknown"
        is_fast_path = False
        result = "built"
        try:
            # run_seconds 仅参与 fast_path 判定（<30s）；真实超时由 effort.hard_s 执行
            from prismclaw.engine.execution.control.kernel import resolve_effort_budget
            _effort_budget = resolve_effort_budget(
                effort=getattr(request, "effort", "standard") or "standard",
                long_running=bool(getattr(request, "long_running", False)),
                tiers=deps.config.effort,
            )
            run_seconds = _effort_budget.hard_s
            request_mode = deps.task_request_policy.resolve_request_mode(
                tree=tree,
                run_seconds=run_seconds,
            )
            is_fast_path = request_mode == "fast_path"
            capability_catalog = _require_capability_catalog(deps)
            shell_cache = _resolve_static_shell_cache(deps)
            cache_key = _shell_cache_key(
                request=request,
                tool_workspace_path=tool_workspace_path,
                effective_model=effective_model,
                request_mode=request_mode,
                mode=mode,
                deps=deps,
                skill_invocation=skill_invocation,
                discovered_tools=discovered_tools,
                subagent_depth=subagent_depth,
                skill_catalog_fingerprint=_skill_catalog_fingerprint(capability_catalog),
                subagent_catalog_fingerprint=_subagent_catalog_fingerprint(capability_catalog),
            )
            shell_snapshot = shell_cache.get(cache_key)
            if shell_snapshot is None:
                shell_snapshot = await self._build_shell_snapshot(
                    request=request,
                    deps=deps,
                    capability_catalog=capability_catalog,
                    tool_workspace_path=tool_workspace_path,
                    effective_model=effective_model,
                    request_mode=request_mode,
                    mode=mode,
                    is_fast_path=is_fast_path,
                    skill_invocation=skill_invocation,
                    discovered_tools=discovered_tools,
                    subagent_depth=subagent_depth,
                )
                shell_cache.put(cache_key, shell_snapshot)
            else:
                result = "reused"
            task_spec = getattr(request, "task_spec", None)
            review_requirements = getattr(request, "review_requirements", None)
            strategy_notes = list(
                runtime_notes_for(review_requirements)
                if review_requirements is not None
                else []
            )
            tool_summaries = list(shell_snapshot.tool_summaries)
            strategy_notes.extend(_session_follow_up_notes(tree, tool_summaries))
            # 冻结前缀只索引项目技能名、插件包计数与核心子代理；插件内的
            # 技能/子代理由 skill_search / subagent_search 按需披露。
            available_skills = shell_snapshot.available_skills
            prompt_inputs = PromptInputs(
                session_id=request.session_id,
                workspace_id=request.workspace_id,
                agent_id=request.agent_id,
                model=shell_snapshot.model,
                identity=build_identity_text(),
                preferences=build_preferences_text(),
                tools=tool_summaries,
                project_skill_names=available_skills,
                marketplace_summaries=shell_snapshot.marketplace_summaries,
                available_subagents=shell_snapshot.available_subagents,
                workspace_path=shell_snapshot.workspace_path,
                subagent_registry_size=shell_snapshot.subagent_registry_size,
                handoff_enabled=shell_snapshot.handoff_enabled,
                any_manifest_has_can_handoff_to=shell_snapshot.any_manifest_has_can_handoff_to,
                max_handoff_chain_length=shell_snapshot.max_handoff_chain_length,
                workspace_mode=mode,
                task_spec=task_spec,
                review_requirements=review_requirements,
                active_skill_name=skill_invocation.skill_name if skill_invocation else None,
                allowed_tools_override=tuple(skill_invocation.allowed_tools_override or [])
                if skill_invocation
                else (),
                invocation_context=(
                    skill_invocation.invocation_context if skill_invocation is not None else "skill"
                ),
                runtime_strategy_notes=tuple(strategy_notes),
                # 注入当前时间（本地化格式 + UTC 精确时间），让模型在每一轮
                # 都能获取准确的实时日期，而不依赖训练数据中的知识截止日期。
                now_iso=now_iso(),
                workspace_tag=getattr(request, "workspace_tag", None),
            )
            return StaticContextResult(
                prompt_inputs=prompt_inputs,
                frozen_result=shell_snapshot.frozen_result,
                history_budget=shell_snapshot.history_budget,
                request_mode=request_mode,
                prior_replacement_state=_load_prior_replacement_state(tree),
                shell_snapshot=shell_snapshot,
            )
        finally:
            _HOTPATH.observe(
                "static_shell.build_ms",
                (time.monotonic() - started_at) * 1000.0,
                labels={
                    "request_mode": request_mode,
                    "fast_path": is_fast_path,
                    "subagent": subagent_depth > 0,
                    "result": result,
                },
            )

    async def _build_shell_snapshot(
        self,
        *,
        request: RunRequest,
        deps: AgentRunnerDeps,
        capability_catalog: Any,
        tool_workspace_path: str | None,
        effective_model: str,
        request_mode: str | None,
        mode: WorkspaceMode = "work",
        is_fast_path: bool,
        skill_invocation: SkillInvocationState | None,
        discovered_tools: set[str] | None,
        subagent_depth: int,
    ) -> StaticTurnShellSnapshot:
        tool_summaries = tuple(
            _tool_prompt_summaries(
                capability_catalog=capability_catalog,
                skill_invocation=skill_invocation,
                discovered_tools=discovered_tools,
                mode=mode,
                subagent_depth=subagent_depth,
                # 无头执行（无会话队列）下不在提示词里宣传需要交互用户的工具，
                # 与实际下发给模型的工具清单保持一致。
                headless=getattr(deps, "session_queue", None) is None,
            )
        )
        sub_agent_tool = deps.tools.get("sub_agent")
        subagent_registry_size = 0
        handoff_enabled = False
        any_manifest_has_can_handoff_to = False
        max_handoff_chain_length = 5
        if sub_agent_tool is not None and deps.handoff_supervisor is not None:
            handoff_enabled = True
            max_handoff_chain_length = deps.handoff_supervisor.max_chain_length
            subagent_registry_size, any_manifest_has_can_handoff_to = _subagent_catalog_state(
                capability_catalog
            )
        workspace_path = str(tool_workspace_path) if tool_workspace_path else None
        # 冻结前缀只放项目技能名、插件包计数与核心子代理；插件内能力由
        # skill_search / subagent_search 披露。排序确定性保证跨轮字节稳定。
        available_skills, marketplace_summaries = _build_available_skills_catalog(
            capability_catalog=capability_catalog,
            workspace_path=workspace_path,
            tool_summaries=tool_summaries,
        )
        available_subagents = _build_available_subagents_catalog(
            capability_catalog=capability_catalog,
            tool_summaries=tool_summaries,
        )
        bootstrap_text = None
        if not is_fast_path:
            bootstrap_text = await StaticContextBuilder._load_bootstrap_text(
                bootstrap_provider=deps.context_bootstrap_provider,
                session_id=request.session_id,
            )
        frozen_prompt_inputs = PromptInputs(
            session_id=request.session_id,
            workspace_id=request.workspace_id,
            agent_id=request.agent_id,
            model=effective_model,
            identity=build_identity_text(),
            preferences=build_preferences_text(),
            tools=tool_summaries,
            project_skill_names=available_skills,
            marketplace_summaries=marketplace_summaries,
            available_subagents=available_subagents,
            workspace_path=workspace_path,
            subagent_registry_size=subagent_registry_size,
            handoff_enabled=handoff_enabled,
            any_manifest_has_can_handoff_to=any_manifest_has_can_handoff_to,
            max_handoff_chain_length=max_handoff_chain_length,
            workspace_mode=mode,
            workspace_tag=getattr(request, "workspace_tag", None),
        )
        # SDD R-A2 / W13: deduct the measured tool-schema wire cost (fingerprint
        # cached on the registry) from the history budget. Full-registry
        # measurement is an upper bound for governance-filtered request
        # subsets — the safe direction for a budget.
        tools_wire_tokens = 0
        tools_registry = getattr(deps, "tools", None)
        if tools_registry is not None:
            try:
                tools_wire_tokens = int(tools_registry.wire_tokens_estimate())
            except Exception:
                logger.exception("tools wire token estimate failed; budget ignores tool schemas")
        history_budget = deps.config.context.budget.compute_history_budget(
            model_max_output=resolve_model_max_output(effective_model),
            tools_wire_tokens=tools_wire_tokens,
        )
        # 口径说明：此处是静态壳构建阶段，尚无本轮 messages，图片 token
        # 无法计数，故 zone_of 未扣 image_tokens（orchestrator/pipeline 的
        # 运行时求解会扣）。差异 ≤ max_images_inline×tokens_per_image 的
        # ratio 缩放，由 clamp 的 floor/cap 吸收。
        system_zone_budget = zone_of(
            deps.config.context.budget,
            "system",
        )
        # SDD R-A2: keep_recent imbalance must be visible, not silently
        # tolerated — a budget below keep_recent×1.5 means post-compaction
        # history immediately re-approaches the trigger line.
        keep_recent_tokens = int(
            getattr(deps.config.context.compaction.history, "keep_recent_tokens", 0)
        )
        if keep_recent_tokens > 0 and history_budget < keep_recent_tokens * 1.5:
            logger.warning(
                "context.budget: history_budget %d < keep_recent_tokens %d × 1.5 — "
                "compaction will re-trigger within a few turns of each summary. "
                "Rebalance: raise window_tokens, lower reserve, or lower "
                "keep_recent_tokens (tools_wire_tokens=%d is already deducted).",
                history_budget, keep_recent_tokens, tools_wire_tokens,
            )
        return StaticTurnShellSnapshot(
            workspace_id=request.workspace_id,
            workspace_path=workspace_path,
            model=effective_model,
            request_mode=request_mode,
            tool_summaries=tool_summaries,
            subagent_registry_size=subagent_registry_size,
            handoff_enabled=handoff_enabled,
            any_manifest_has_can_handoff_to=any_manifest_has_can_handoff_to,
            max_handoff_chain_length=max_handoff_chain_length,
            available_skills=available_skills,
            marketplace_summaries=marketplace_summaries,
            available_subagents=available_subagents,
            frozen_result=build_frozen_prefix(
                frozen_prompt_inputs,
                budget=system_zone_budget,
                bootstrap=bootstrap_text,
                ports=getattr(deps, "ports", None),
            ),
            history_budget=history_budget,
            tools_wire_tokens=tools_wire_tokens,
        )

    @staticmethod
    async def _load_bootstrap_text(
        *,
        bootstrap_provider: Any,
        session_id: str,
    ) -> str | None:
        try:
            return await bootstrap_provider.get_bootstrap(session_id)
        except Exception:
            logger.warning("get_bootstrap failed", exc_info=True)
            return None
