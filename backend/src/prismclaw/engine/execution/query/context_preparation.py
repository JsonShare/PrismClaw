from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prismclaw.engine.execution.loop.deps import AgentRunnerDeps
    from prismclaw.engine.execution.loop.runner import RunRequest
    from prismclaw.schema.agent.context import BootstrapContextProvider
    from prismclaw.schema.agent.session import SessionHeader
    from prismclaw.schema.session import SessionTree

from prismclaw.engine.compression.context_zones import zone_of
from prismclaw.engine.execution.capabilities.catalog import RuntimeCatalog

# --- extracted sub-modules ---
from prismclaw.engine.execution.query._catalog_builder import (
    _build_available_skills_catalog,
    _build_available_subagents_catalog,
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
from prismclaw.engine.execution.request.model_fallback import (
    UnknownModelError,
    get_model_spec,
    resolve_model_max_output,
)
from prismclaw.engine.execution.request.system_prompt import (
    PromptInputs,
    build_frozen_prefix,
    build_frozen_prompt_sections,
    build_identity_text,
    build_preferences_text,
)
from prismclaw.engine.experts import prompt_slot
from prismclaw.engine.experts.codes import ExpertErrorCode
from prismclaw.engine.experts.prompt_slot import ExpertPromptSlotCollector
from prismclaw.engine.experts.scope import ActiveScope, RejectedScope, Scope
from prismclaw.engine.foundation.telemetry.hotpath import HotPathTelemetry
from prismclaw.engine.security.gate.governance import runtime_notes_for
from prismclaw.engine.subagent.definition.parser import parse_agent_md
from prismclaw.schema.services.skills import SkillInvocationState
from prismclaw.schema.session.types import now_iso
from prismclaw.schema.shared.utils import estimate_tokens

logger = logging.getLogger(__name__)
_HOTPATH = HotPathTelemetry()


async def build_static_context_for_runner(
    *,
    request: RunRequest,
    deps: AgentRunnerDeps,
    tree: SessionTree,
    tool_workspace_path: str | None,
    effective_model: str,
    skill_invocation: SkillInvocationState | None = None,
    discovered_tools: set[str] | None = None,
    subagent_depth: int = 0,
    visible_tool_names: set[str],
    session_scope: Scope | None,
) -> StaticContextResult:
    builder = StaticContextBuilder()
    return await builder.build(
        request=request,
        deps=deps,
        tree=tree,
        tool_workspace_path=tool_workspace_path,
        effective_model=effective_model,
        skill_invocation=skill_invocation,
        discovered_tools=discovered_tools,
        subagent_depth=subagent_depth,
        visible_tool_names=visible_tool_names,
        session_scope=session_scope,
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
        visible_tool_names: set[str],
        session_scope: Scope | None,
        skill_invocation: SkillInvocationState | None = None,
        discovered_tools: set[str] | None = None,
        subagent_depth: int = 0,
    ) -> StaticContextResult:
        started_at = time.monotonic()
        result = "built"
        try:
            capability_catalog = _require_capability_catalog(deps)
            shell_cache = _resolve_static_shell_cache(deps)
            session = getattr(tree, "header", None)
            expert_persona = ""
            expert_id = ""
            if session is not None:

                async def resolve_expert_prompt(**_kwargs: object) -> str | None:
                    return await self._read_expert_agent_markdown(session_scope)

                expert_persona = await ExpertPromptSlotCollector(resolve_expert_prompt).collect(
                    session
                )
                expert_id = session.expert_id or ""
            expert_persona_digest = (
                hashlib.sha256(expert_persona.encode("utf-8")).hexdigest() if expert_persona else ""
            )
            cache_key = _shell_cache_key(
                request=request,
                tool_workspace_path=tool_workspace_path,
                effective_model=effective_model,
                deps=deps,
                skill_invocation=skill_invocation,
                discovered_tools=discovered_tools,
                subagent_depth=subagent_depth,
                visible_tool_names=visible_tool_names,
                skill_catalog_fingerprint=_skill_catalog_fingerprint(capability_catalog),
                subagent_catalog_fingerprint=_subagent_catalog_fingerprint(capability_catalog),
                expert_id=expert_id,
                expert_persona_digest=expert_persona_digest,
            )
            shell_snapshot = shell_cache.get(cache_key)
            if shell_snapshot is None:
                shell_snapshot = await self._build_shell_snapshot(
                    request=request,
                    deps=deps,
                    capability_catalog=capability_catalog,
                    tool_workspace_path=tool_workspace_path,
                    effective_model=effective_model,
                    subagent_depth=subagent_depth,
                    visible_tool_names=visible_tool_names,
                    expert_persona=expert_persona,
                    session=session,
                )
                shell_cache.put(cache_key, shell_snapshot)
            else:
                result = "reused"
                budgeted_persona = self._apply_expert_persona_budget(
                    expert_persona,
                    fixed_prompt_tokens=shell_snapshot.fixed_prompt_tokens,
                    effective_model=effective_model,
                    session=session,
                )
                if budgeted_persona != shell_snapshot.expert_persona:
                    shell_snapshot = await self._build_shell_snapshot(
                        request=request,
                        deps=deps,
                        capability_catalog=capability_catalog,
                        tool_workspace_path=tool_workspace_path,
                        effective_model=effective_model,
                        subagent_depth=subagent_depth,
                        visible_tool_names=visible_tool_names,
                        expert_persona=expert_persona,
                        session=session,
                    )
                    shell_cache.put(cache_key, shell_snapshot)
                    result = "built"
            task_spec = getattr(request, "task_spec", None)
            strategy_notes = list(runtime_notes_for(task_spec))
            tool_summaries = list(shell_snapshot.tool_summaries)
            # 冻结前缀只索引项目技能名、专家包计数与核心子代理；专家包内的
            # 技能/子代理由 skill_search / subagent_search 按需披露。
            available_skills = shell_snapshot.available_skills
            prompt_inputs = PromptInputs(
                session_id=request.session_id,
                workspace_id=request.workspace_id,
                agent_id=request.agent_id,
                identity=build_identity_text(),
                preferences=build_preferences_text(),
                expert_persona=shell_snapshot.expert_persona,
                tools=tool_summaries,
                project_skill_names=available_skills,
                marketplace_summaries=shell_snapshot.marketplace_summaries,
                available_subagents=shell_snapshot.available_subagents,
                workspace_path=shell_snapshot.workspace_path,
                subagent_registry_size=shell_snapshot.subagent_registry_size,
                task_spec=task_spec,
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
            )
            logger.info(
                "prompt_shell result=%s sections=%d chars=%d skills=%d packages=%d "
                "subagents=%d skill_name_chars=%d",
                result,
                len(shell_snapshot.frozen_result.blocks),
                len(shell_snapshot.frozen_result.text),
                len(shell_snapshot.available_skills),
                len(shell_snapshot.marketplace_summaries),
                len(shell_snapshot.available_subagents),
                sum(len(name) for name in shell_snapshot.available_skills),
            )
            return StaticContextResult(
                prompt_inputs=prompt_inputs,
                frozen_result=shell_snapshot.frozen_result,
                history_budget=shell_snapshot.history_budget,
                prior_replacement_state=_load_prior_replacement_state(tree),
                shell_snapshot=shell_snapshot,
            )
        finally:
            _HOTPATH.observe(
                "static_shell.build_ms",
                (time.monotonic() - started_at) * 1000.0,
                labels={
                    "subagent": subagent_depth > 0,
                    "result": result,
                },
            )

    async def _build_shell_snapshot(
        self,
        *,
        request: RunRequest,
        deps: AgentRunnerDeps,
        capability_catalog: RuntimeCatalog,
        tool_workspace_path: str | None,
        effective_model: str,
        subagent_depth: int,
        visible_tool_names: set[str],
        expert_persona: str = "",
        session: SessionHeader | None = None,
    ) -> StaticTurnShellSnapshot:
        tool_summaries = tuple(
            _tool_prompt_summaries(
                capability_catalog=capability_catalog,
                visible_names=visible_tool_names,
                subagent_depth=subagent_depth,
            )
        )
        sub_agent_tool = deps.tools.get("sub_agent")
        subagent_registry_size = 0
        if sub_agent_tool is not None:
            subagent_registry_size = _subagent_catalog_state(capability_catalog)
        workspace_path = str(tool_workspace_path) if tool_workspace_path else None
        # 冻结前缀只放项目技能名、专家包计数与核心子代理；专家包内能力由
        # skill_search / subagent_search 披露。排序确定性保证跨轮字节稳定。
        # 静态壳是 (workspace 事实, model) 的纯函数：能力索引恒定构建，
        # 不随复杂度裁剪（SDD R1 / D1）。
        available_skills, marketplace_summaries = _build_available_skills_catalog(
            capability_catalog=capability_catalog,
            workspace_path=workspace_path,
            tool_summaries=tool_summaries,
        )
        available_subagents = _build_available_subagents_catalog(
            capability_catalog=capability_catalog,
            tool_summaries=tool_summaries,
        )
        # bootstrap 是项目约定（AGENTS.md 等），简单轮次问的恰是「这个项目
        # 怎么做事」，跳过它在语义上是错的（SDD D2 / DD-2）。恒定加载后，
        # 它与 catalogs 同属 semi_stable 随壳缓存命中：首轮付费，后续由
        # 前缀缓存覆盖。不做字符上限——超预算时 evict_rank=0 先撤，
        # 由 _apply_budget_blocks 持久化到磁盘再 drop，不会撑爆 system zone。
        bootstrap_text = await StaticContextBuilder._load_bootstrap_text(
            bootstrap_provider=deps.context_bootstrap_provider,
            session_id=request.session_id,
        )
        if bootstrap_text is not None:
            logger.info("bootstrap_chars=%d", len(bootstrap_text))
        frozen_prompt_inputs = PromptInputs(
            session_id=request.session_id,
            workspace_id=request.workspace_id,
            agent_id=request.agent_id,
            identity=build_identity_text(),
            preferences=build_preferences_text(),
            expert_persona="",
            tools=tool_summaries,
            project_skill_names=available_skills,
            marketplace_summaries=marketplace_summaries,
            available_subagents=available_subagents,
            workspace_path=workspace_path,
            subagent_registry_size=subagent_registry_size,
        )
        fixed_prompt_tokens = sum(
            estimate_tokens(section.text)
            for section in build_frozen_prompt_sections(
                frozen_prompt_inputs,
                bootstrap=bootstrap_text,
            )
        )
        expert_persona = self._apply_expert_persona_budget(
            expert_persona,
            fixed_prompt_tokens=fixed_prompt_tokens,
            effective_model=effective_model,
            session=session,
        )
        frozen_prompt_inputs.expert_persona = expert_persona
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
                history_budget,
                keep_recent_tokens,
                tools_wire_tokens,
            )
        return StaticTurnShellSnapshot(
            workspace_id=request.workspace_id,
            workspace_path=workspace_path,
            model=effective_model,
            tool_summaries=tool_summaries,
            subagent_registry_size=subagent_registry_size,
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
            expert_persona=expert_persona,
            fixed_prompt_tokens=fixed_prompt_tokens,
        )

    @staticmethod
    async def _read_expert_agent_markdown(scope: Scope | None) -> str | None:
        """只读作用域里已解析的人格源，不再自建 scope（每 run 只构建一次）。"""
        if scope is None:
            return None
        if isinstance(scope, RejectedScope):
            # 只有「未部署」把原因透出给调用方；禁用等状态由 run 入口早退处理。
            if scope.code is ExpertErrorCode.EXPERT_UNAVAILABLE:
                raise ValueError(scope.reason or "selected expert is unavailable")
            return None
        if not isinstance(scope, ActiveScope):
            return None
        if scope.persona_source is None:
            raise ValueError("selected expert has no persona source")
        agent = await asyncio.to_thread(parse_agent_md, scope.persona_source)
        return agent.system_prompt

    @staticmethod
    def _apply_expert_persona_budget(
        expert_persona: str,
        *,
        fixed_prompt_tokens: int,
        effective_model: str,
        session: SessionHeader | None,
    ) -> str:
        if not expert_persona:
            return ""
        try:
            context_window = get_model_spec(effective_model).context_window
        except UnknownModelError:
            return expert_persona
        estimated = fixed_prompt_tokens + estimate_tokens(expert_persona)
        threshold = context_window * 0.5
        if estimated < threshold:
            return expert_persona
        session_id = session.id if session is not None else ""
        expert_id = (session.expert_id or "") if session is not None else ""
        prompt_slot.emit_event(
            ExpertErrorCode.EXPERT_PROMPT_TOO_LONG,
            session_id=session_id,
            expert_id=expert_id,
            detail="expert persona and fixed sections reached half of the model context window",
            estimated_tokens=estimated,
            context_window=context_window,
            threshold=threshold,
        )
        logger.warning(
            "%s: expert_id=%s estimated_tokens=%d threshold=%s context_window=%d; "
            "using empty expert persona",
            ExpertErrorCode.EXPERT_PROMPT_TOO_LONG,
            expert_id,
            estimated,
            threshold,
            context_window,
        )
        return ""

    @staticmethod
    async def _load_bootstrap_text(
        *,
        bootstrap_provider: BootstrapContextProvider,
        session_id: str,
    ) -> str | None:
        try:
            return await bootstrap_provider.get_bootstrap(session_id)
        except Exception:
            logger.warning("get_bootstrap failed", exc_info=True)
            return None
