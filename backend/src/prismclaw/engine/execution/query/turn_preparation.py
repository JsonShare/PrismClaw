"""Turn preparation: assemble context, project messages, and pack LLM requests.

Pure helper functions and dataclasses live in ``_turn_helpers.py``; this module
keeps only the ``QueryTurnPreparator`` orchestrator and the top-level async
entry-points.
"""

from __future__ import annotations

import logging
from pathlib import Path as _Path
from dataclasses import replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from prismclaw.engine.execution.query._static_context_types import StaticContextResult

from prismclaw.engine.compression.budget_orchestrator import (
    load_budget_audit_trail,
    merge_budget_audit_trails,
)
from prismclaw.engine.execution.capabilities.catalog import CapabilityCatalogService
from prismclaw.engine.execution.capabilities.delta_projection import build_capability_delta_blocks
from prismclaw.engine.execution.request.packing import RequestPackager
from prismclaw.engine.security.gate.governance import runtime_notes_for
from prismclaw.engine.execution.request._prompt_types import build_system_prompt_section_state
from prismclaw.engine.compression.context_zones import resolve_runtime_zones
from prismclaw.engine.execution.request.system_prompt import (
    PromptInputs,
    SystemPromptResult,
    build_per_turn_suffix,
)
from prismclaw.engine.context.projection_engine import ContextProjectionEngine
from prismclaw.engine.context.projection_models import ProjectionMessageSet
from prismclaw.schema.content.replacement_state import ReplacementDecisionState
from prismclaw.engine.state.turn_state import TurnState
from prismclaw.schema.content.content_types import RuntimeControlArtifact
from prismclaw.schema.content.context import ContextBlock

from prismclaw.engine.execution.query._turn_helpers import (
    DynamicContextResult,
    TurnPreflightResult,
    _build_runtime_status_blocks,
    _require_capability_catalog,
    _try_auto_activate_skill,
    apply_request_mode_skeleton,
    build_turn_context_layout,
    ensure_user_message,
    orchestrate_context_layout_budgets,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Async entry-points
# ---------------------------------------------------------------------------

async def _prepare_query_turn_from_prompt_inputs(
    *,
    request: Any,
    deps: Any,
    prompt_inputs: PromptInputs,
    frozen_result: SystemPromptResult,
    history_budget: int,
    request_mode: str | None,
    prior_replacement_state: ReplacementDecisionState | None = None,
    attachment_guidance: str | None,
    message_set: ProjectionMessageSet,
    model: str,
    turn_state: TurnState | None = None,
) -> TurnPreflightResult:
    preparator = QueryTurnPreparator()
    return await preparator.prepare_turn(
        request=request,
        deps=deps,
        prompt_inputs=prompt_inputs,
        frozen_result=frozen_result,
        history_budget=history_budget,
        request_mode=request_mode,
        prior_replacement_state=prior_replacement_state,
        attachment_guidance=attachment_guidance,
        message_set=message_set,
        model=model,
        request_packager=RequestPackager(),
        turn_state=turn_state,
    )


async def prepare_query_turn_from_static_context(
    *,
    request: Any,
    deps: Any,
    static_result: "StaticContextResult",
    attachment_guidance: str | None,
    message_set: ProjectionMessageSet,
    model: str,
    turn_state: TurnState | None = None,
) -> TurnPreflightResult:
    return await _prepare_query_turn_from_prompt_inputs(
        request=request,
        deps=deps,
        prompt_inputs=static_result.prompt_inputs,
        frozen_result=static_result.frozen_result,
        history_budget=static_result.history_budget,
        request_mode=static_result.request_mode,
        prior_replacement_state=static_result.prior_replacement_state,
        attachment_guidance=attachment_guidance,
        message_set=message_set,
        model=model,
        turn_state=turn_state,
    )


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

class QueryTurnPreparator:
    def __init__(self, *, projection_engine: ContextProjectionEngine | None = None) -> None:
        self._projection_engine = projection_engine or ContextProjectionEngine()

    async def build(
        self,
        *,
        request: Any,
        deps: Any,
        prompt_inputs: PromptInputs,
        frozen_result: SystemPromptResult,
        history_budget: int,
        request_mode: str | None,
        attachment_guidance: str | None,
        message_set: ProjectionMessageSet,
        turn_state: TurnState | None = None,
    ) -> DynamicContextResult:
        assembler = deps.context_assembler
        assembled = await assembler.assemble(
            session_id=request.session_id,
            messages=message_set.messages,
            token_budget=history_budget,
            prompt=request.user_message,
            request_mode=request_mode,
            task_spec=getattr(request, "task_spec", None),
        )
        assembled.messages = ensure_user_message(
            assembled.messages,
            fallback_user_message=request.user_message,
        )

        # ⚓ [Phase 0' Decoupled Trajectory] Layer ② assemble_coordinator.assemble() output
        # Publish as core-owned bus event. Core imports ONLY prismclaw.engine.bus +
        # prismclaw.engine.bus_events. Trajectory audit is a separate downstream
        # subscriber; if trajectory code is uninstalled, publish() here is a
        # ~30ns NOP (SessionBus check empty handlers + return). No trajectory.*
        # import, no trajectory-specific try/except per exception.
        try:
            from prismclaw.engine.trajectory.bus import current_bus
            from prismclaw.engine.trajectory.bus_events import AssembleEntryDone
            from prismclaw.engine.utils.summarize import summarize_messages
            from prismclaw.engine.trajectory.bus_context import current_runtime

            bus = current_bus()
            _rt = current_runtime()
            if bus and not bus._is_noop and _rt is not None:
                sid, turn_counter, iteration = _rt.session_id, _rt.turn, _rt.iteration
                _meta_keys: tuple[str, ...] = ()
                try:
                    if assembled_plan := getattr(assembled, "context_layout", None):
                        md = getattr(assembled_plan, "metadata", None)
                        if isinstance(md, dict):
                            _meta_keys = tuple(str(k) for k in list(md.keys())[:16])
                except Exception:
                    _meta_keys = ()
                msg_count, roles, head, tail = summarize_messages(
                    assembled.messages or [],
                )
                bus.publish(AssembleEntryDone(
                    session_id=sid, turn_counter=turn_counter, iteration=iteration,
                    msg_count=msg_count,
                    msg_roles=roles,
                    head=head,
                    tail=tail,
                    entry_meta_keys=_meta_keys,
                ))
        except Exception:
            pass  # never break baseline
        assembled_plan = assembled.context_layout
        assembled_budget_audit = load_budget_audit_trail(assembled_plan.metadata)
        task_profile = assembled.task_profile
        review_requirements = getattr(request, "review_requirements", None)
        per_turn_inputs = replace(
            prompt_inputs,
            runtime_strategy_notes=tuple(
                [
                    *(
                        runtime_notes_for(review_requirements)
                        if review_requirements is not None
                        else []
                    ),
                    *(
                        deps.task_request_policy.runtime_strategy_notes(
                            task_profile,
                            request_mode=request_mode,
                        )
                        if task_profile is not None
                        else []
                    ),
                ]
            ),
        )
        # L1 记忆索引（SDD D3 方案 A）：per-turn 加载，走动态区——静态 shell
        # cache 命中时也反映最新记忆；fast_path 维持零 IO 不加载。
        l1_snapshot_text: str | None = None
        if request_mode != "fast_path":
            try:
                from prismclaw.engine.execution.query._static_context_types import (
                    format_l1_snapshot,
                )

                l1_entries = await deps.context_memory_provider.get_l1_snapshot(
                    request.session_id
                )
                l1_snapshot_text = format_l1_snapshot(l1_entries) if l1_entries else None
            except Exception:
                logger.warning("per-turn l1 snapshot load failed", exc_info=True)
                l1_snapshot_text = None
        per_turn_result = await build_per_turn_suffix(
            per_turn_inputs,
            hooks=deps.hooks,
            user_prompt=request.user_message,
            l1_snapshot=l1_snapshot_text,
        )
        capability_delta_blocks = build_capability_delta_blocks(
            capability_catalog=_require_capability_catalog(deps),
            discovered_tool_ledger=(
                turn_state.discovered_tool_ledger if turn_state is not None else None
            ),
        )
        # --- Fact Sheet: load from tree (if available) ---
        fact_sheet_text: str | None = None
        try:
            tree = getattr(deps, "tree", None) or getattr(
                getattr(deps, "state", None), "tree", None
            )
            if tree is not None:
                from prismclaw.engine.compression.fact_sheet import (
                    load_fact_sheet,
                    render_fact_sheet_text,
                )
                fs = load_fact_sheet(tree)
                if fs:
                    fact_sheet_text = render_fact_sheet_text(fs)
        except Exception:
            pass  # Fact Sheet is best-effort; never block turn preparation

        # --- T14: Skill auto-activation ---
        # Check if the user message matches a skill gesture or a single
        # skill_search result.  If so, inject a RuntimeControlArtifact into
        # the appended_messages so the LLM sees the activated skill context
        # immediately on the next turn.
        auto_activation: RuntimeControlArtifact | None = None
        try:
            capability_catalog = getattr(deps, "capability_catalog", None)
            auto_activation = _try_auto_activate_skill(
                user_message=request.user_message,
                capability_catalog=capability_catalog if isinstance(capability_catalog, CapabilityCatalogService) else None,
                workspace_path=prompt_inputs.workspace_path,
            )
        except Exception:
            auto_activation = None  # best-effort; never block turn preparation

        # When auto-activation succeeds, append a user-facing reminder so the
        # LLM knows the skill was pre-activated automatically (the invocation
        # state itself is established by the run preparer at run start).
        # §4.10 动态层：路由 hints 在两个分支前统一取走并清空——本函数每次
        # LLM 迭代都执行，无论走激活提醒还是推荐提示，hints 只消费一次。
        skill_route_hints = getattr(request, "skill_route_hints", ())
        request.skill_route_hints = ()
        appended_messages_override: list | None = None
        if auto_activation is not None and auto_activation.skill_invocation is not None:
            inv = auto_activation.skill_invocation
            reminder_text = f"[已自动激活技能“{inv.skill_name}”，该技能现已对当前任务生效。]"
            appended_messages_override = [
                *message_set.appended_messages,
                {"role": "user", "content": reminder_text},
            ]
        elif skill_route_hints:
            # 任务级路由推荐（多命中，未自动激活时）——仅提示不激活；
            # 一次性注入（不落树），与激活提醒同通道。
            hint_lines = "\n".join(f"- {h}" for h in skill_route_hints)
            appended_messages_override = [
                *message_set.appended_messages,
                {
                    "role": "user",
                    "content": (
                        "[技能路由] 与当前任务可能相关的技能：\n"
                        f"{hint_lines}\n"
                        "如与当前任务匹配，用 activate_skill 按精确名称激活；不匹配则忽略。"
                    ),
                },
            ]

        # --- T15: Runtime status injection ---
        # Build runtime status blocks from session_tree / session_state and
        # prepend them to per_turn_blocks.  Replaces the deleted
        # jobs/task_status/follow_up_status tools with direct context injection.
        runtime_status_blocks: list[ContextBlock] = []
        try:
            runtime_status_blocks = _build_runtime_status_blocks(deps)
        except Exception:
            runtime_status_blocks = []  # best-effort; never block turn preparation

        turn_context_layout = build_turn_context_layout(
            frozen_blocks=frozen_result.blocks,
            per_turn_blocks=[*per_turn_result.blocks, *capability_delta_blocks, *runtime_status_blocks],
            assembled_plan=assembled_plan,
            attachment_guidance=attachment_guidance,
            extra_system=request.extra_system,
            task_profile=task_profile,
            fact_sheet_text=fact_sheet_text,
        )
        turn_context_layout = apply_request_mode_skeleton(
            turn_context_layout,
            request_mode=request_mode,
        )
        _tools_wire = 0
        _tools_registry = getattr(deps, "tools", None)
        if _tools_registry is not None:
            try:
                _tools_wire = int(_tools_registry.wire_tokens_estimate())
            except Exception:
                _tools_wire = 0
        _zones = resolve_runtime_zones(
            budget=deps.config.context.budget,
            tools_wire=_tools_wire,
        )
        budgeted = orchestrate_context_layout_budgets(
            turn_context_layout,
            system_budget=_zones.system,
            dynamic_budget=_zones.dynamic,
            result_budget=_zones.result,
            # working_dir 必须是 session 根，不是 workspace 根 —— 否则 context_blocks
            # / tool_results 会被 mkdir 到 workspace 根，与重构后 session 级隔离冲突。
            working_dir=(
                str(_Path(prompt_inputs.workspace_path)
                    / "sessions"
                    / prompt_inputs.session_id.replace("#", "-").replace(":", "-"))
                if prompt_inputs.workspace_path and prompt_inputs.session_id
                else prompt_inputs.workspace_path
            ),
            ports=getattr(deps, "ports", None),
        )
        turn_context_layout = budgeted.context_layout
        # G4：每轮区域压力计量（每块 actual/cap），结构化日志。
        try:
            _sys_actual = sum(
                b.estimated_tokens for b in turn_context_layout.zone_blocks("static")
            )
            _dyn_actual = sum(
                b.estimated_tokens for b in turn_context_layout.zone_blocks("dynamic")
            )
            _res_actual = sum(
                b.estimated_tokens for b in turn_context_layout.zone_blocks("result")
            )
            logger.info(
                "zone_pressure system_actual=%d system_cap=%d dynamic_actual=%d "
                "dynamic_cap=%d result_actual=%d result_cap=%d tools_actual=%d tools_cap=%d",
                _sys_actual, _zones.system,
                _dyn_actual, _zones.dynamic,
                _res_actual, _zones.result,
                _tools_wire, _zones.tools,
            )
        except Exception:
            pass
        # Use appended_messages_override from T14 auto-activation when present,
        # otherwise fall back to the original message_set.appended_messages.
        _final_appended = (
            appended_messages_override
            if appended_messages_override is not None
            else list(message_set.appended_messages)
        )
        projection = self._projection_engine.project(
            message_set=ProjectionMessageSet(
                messages=list(assembled.messages),
                meta_messages=list(message_set.meta_messages),
                appended_messages=list(_final_appended),
            ),
            turn_context_layout=turn_context_layout,
        )
        return DynamicContextResult(
            assembled_context_layout=assembled_plan,
            task_profile=task_profile,
            projection=projection,
            budget_audit=merge_budget_audit_trails(
                assembled_budget_audit,
                budgeted.audit_trail,
            ),
            system_prompt_sections=build_system_prompt_section_state(
                turn_context_layout
            ),
        )

    async def prepare_turn(
        self,
        *,
        request: Any,
        deps: Any,
        prompt_inputs: PromptInputs,
        frozen_result: SystemPromptResult,
        history_budget: int,
        request_mode: str | None,
        prior_replacement_state: ReplacementDecisionState | None = None,
        attachment_guidance: str | None,
        message_set: ProjectionMessageSet,
        model: str,
        request_packager: RequestPackager,
        turn_state: TurnState | None = None,
    ) -> TurnPreflightResult:
        dynamic_result = await self.build(
            request=request,
            deps=deps,
            prompt_inputs=prompt_inputs,
            frozen_result=frozen_result,
            history_budget=history_budget,
            request_mode=request_mode,
            attachment_guidance=attachment_guidance,
            message_set=message_set,
            turn_state=turn_state,
        )

        # ⚓ [Phase 0' Decoupled Trajectory] Layer ③ pipeline output.
        # Core imports ONLY core.bus / core.bus_events / summarise helper.
        try:
            from prismclaw.engine.trajectory.bus import current_bus
            from prismclaw.engine.trajectory.bus_events import PipelineOutDone
            from prismclaw.engine.utils.summarize import summarize_messages
            from prismclaw.engine.trajectory.bus_context import current_runtime

            bus = current_bus()
            _rt = current_runtime()
            if bus and not bus._is_noop and _rt is not None:
                sid, turn_counter, iteration = _rt.session_id, _rt.turn, _rt.iteration
                _projected = getattr(dynamic_result.projection, "projected_messages", []) or []
                # Fix A: extract budget_summary from BudgetAuditTrail via its
                # to_metadata_dict() — the previous "iterate as list of dicts"
                # approach silently returned {} because BudgetAuditTrail is a
                # dataclass, not a list. Flatten stage/action/reason counts into
                # the dict[str, int] contract the event requires.
                _budget_summary: dict[str, int] = {}
                try:
                    _ba = getattr(dynamic_result, "budget_audit", None)
                    if _ba is not None:
                        _md = (
                            _ba.to_metadata_dict()
                            if hasattr(_ba, "to_metadata_dict")
                            else {}
                        )
                        if isinstance(_md, dict):
                            _dc = _md.get("decision_count")
                            if isinstance(_dc, int):
                                _budget_summary["decision_count"] = _dc
                            for _kk, _vv in (_md.get("stage_counts") or {}).items():
                                if isinstance(_vv, int):
                                    _budget_summary[f"stage:{str(_kk)[:28]}"] = _vv
                            for _kk, _vv in (_md.get("action_counts") or {}).items():
                                if isinstance(_vv, int):
                                    _budget_summary[f"action:{str(_kk)[:28]}"] = _vv
                            for _kk, _vv in (_md.get("reason_counts") or {}).items():
                                if isinstance(_vv, int):
                                    _budget_summary[f"reason:{str(_kk)[:28]}"] = _vv
                except Exception:
                    _budget_summary = {}
                # Fix B: derive compressors_used from the non-"keep" actions in
                # budget_audit.decisions. The dataclass has no explicit
                # "compressor name" field; action+reason together identify what
                # compression actually happened (drop / truncate / compact_history
                # with their reasons). Sorted for stable DB representation.
                _compressors_used: tuple[str, ...] = ()
                try:
                    _ba2 = getattr(dynamic_result, "budget_audit", None)
                    if _ba2 is not None:
                        _decs = getattr(_ba2, "decisions", None) or []
                        _seen: list[str] = []
                        _seen_set: set[str] = set()
                        for _d in _decs:
                            _act = getattr(_d, "action", None)
                            if not _act or _act == "keep":
                                continue
                            _rs = getattr(_d, "reason", None) or ""
                            _label = f"{_act}:{_rs}" if _rs else str(_act)
                            if _label in _seen_set:
                                continue
                            _seen_set.add(_label)
                            _seen.append(_label)
                        _compressors_used = tuple(sorted(_seen))
                except Exception:
                    _compressors_used = ()
                msg_count, roles, head, tail = summarize_messages(list(_projected))
 # derive compaction_summary — a human-readable
                # preview of what was compacted. We sample the assemble_entry
                # (pre-compaction) vs pipeline_out (post-compaction) head/tail
                # difference into a short string so the panel can show
                # "compact_history:trim dropped 3 messages, kept head [user]…"
                _compaction_summary: str | None = None
                try:
                    if _compressors_used:
                        _asm = getattr(dynamic_result, "assembled_context_layout", None)
                        _pre_count = 0
                        _post_count = len(_projected)
                        if _asm is not None:
                            _pre_count = len(getattr(_asm, "messages", []) or [])
                        _lines = [
                            f"actions: {', '.join(_compressors_used)}",
                            f"pre={_pre_count} → post={_post_count} msgs",
                        ]
                        # sample first user message preview (what triggered compaction)
                        for _m in (_projected or [])[:3]:
                            if isinstance(_m, dict) and _m.get("role") == "user":
                                _c = _m.get("content")
                                if isinstance(_c, str) and _c:
                                    _lines.append(f"trigger: {_c[:200]}")
                                    break
                        _compaction_summary = " | ".join(_lines)[:500]
                except Exception:
                    _compaction_summary = None
                bus.publish(PipelineOutDone(
                    session_id=sid, turn_counter=turn_counter, iteration=iteration,
                    msg_count=msg_count,
                    msg_roles=roles,
                    head=head,
                    tail=tail,
                    budget_summary=_budget_summary,
                    compressors_used=_compressors_used,
                    compaction_summary=_compaction_summary,
                ))
        except Exception:
            pass  # never break baseline

        packed_request = request_packager.pack_projection(
            projection=dynamic_result.projection,
            model=model,
            request_mode=request_mode,
            previous_replacement_state=prior_replacement_state,
            # D2/F5：主链路透传 session_id 作 prompt_cache_key 来源。
            session_id=getattr(request, "session_id", None),
        )

        # ⚓ [Phase 0' Decoupled Trajectory] Layer ④ final messages to LLM.
        try:
            from prismclaw.engine.trajectory.bus import current_bus
            from prismclaw.engine.trajectory.bus_events import PackerFinalDone
            from prismclaw.engine.utils.summarize import summarize_messages
            from prismclaw.engine.trajectory.bus_context import current_runtime
            from prismclaw.engine.compression.compaction import estimate_messages_tokens

            bus = current_bus()
            _rt = current_runtime()
            if bus and not bus._is_noop and _rt is not None:
                sid, turn_counter, iteration = _rt.session_id, _rt.turn, _rt.iteration
                _final_msgs = list(getattr(packed_request, "messages", []) or [])
                # Fix C: PackedLLMRequest has no `estimated_tokens` attribute —
                # `getattr(..., "estimated_tokens", 0)` always returns 0 (which
                # `int(0) or None` collapses to None). Compute tokens directly
                # from the final messages via the same estimator the runner uses
                # for history_tokens (estimate_messages_tokens).
                _final_tokens: int | None = None
                try:
                    _final_tokens = int(estimate_messages_tokens(_final_msgs)) or None
                except Exception:
                    _final_tokens = None
                _sys_chars: int | None = None
                try:
                    _sys_field = getattr(packed_request, "system", None)
                    if isinstance(_sys_field, str) and _sys_field:
                        _sys_chars = len(_sys_field)
                    elif isinstance(_sys_field, list):
                        _total_chars = 0
                        for _b in _sys_field:
                            try:
                                if isinstance(_b, str):
                                    _total_chars += len(_b)
                                    continue
                                _text = getattr(_b, "content", None)
                                if isinstance(_text, str):
                                    _total_chars += len(_text)
                            except Exception:
                                continue
                        _sys_chars = _total_chars or None
                except Exception:
                    _sys_chars = None
                _schema_chars: int | None = None
                _resp_chars: int | None = None
 # actual system prompt + tool schema TEXT (up to 1000 chars).
                # The existing *_chars fields only carry counts — useless for
                # "what did the system prompt actually say". These give the
                # panel real content to display.
                _system_prompt_text: str | None = None
                _system_prompt_full: str | None = None
                _system_prompt_sha: str | None = None
                _tool_schema_text: str | None = None
                try:
                    _sys_field = getattr(packed_request, "system", None)
                    if isinstance(_sys_field, str) and _sys_field:
                        _system_prompt_full = _sys_field
                    elif isinstance(_sys_field, list):
                        _parts: list[str] = []
                        for _b in _sys_field:
                            try:
                                _t = _b if isinstance(_b, str) else getattr(_b, "content", None) or getattr(_b, "text", None) or ""
                                if isinstance(_t, str):
                                    _parts.append(_t)
                            except Exception:
                                continue
                        if _parts:
                            _system_prompt_full = "".join(_parts)
                    if _system_prompt_full:
                        _system_prompt_text = _system_prompt_full[:1000]
                        if len(_system_prompt_full) > 1000:
                            _system_prompt_text = _system_prompt_text + "…"
                        import hashlib as _hashlib
                        _system_prompt_sha = _hashlib.sha1(
                            _system_prompt_full.encode("utf-8", "replace"),
                        ).hexdigest()[:16]
                except Exception:
                    _system_prompt_text = None
                    _system_prompt_full = None
                    _system_prompt_sha = None
                try:
                    _ctx_payload = getattr(packed_request, "context_payload", None)
                    if _ctx_payload:
                        import json as _json
                        _schema_str = _json.dumps(
                            _ctx_payload, ensure_ascii=False, default=str,
                        )
                        _schema_chars = len(_schema_str)
 # also keep the actual schema text (up to 1000 chars)
                        if _schema_str:
                            _tool_schema_text = _schema_str[:1000]
                            if len(_schema_str) > 1000:
                                _tool_schema_text = _tool_schema_text + "…"
                except Exception:
                    _schema_chars = None
                    _tool_schema_text = None
                msg_count, roles, head, tail = summarize_messages(_final_msgs)
                bus.publish(PackerFinalDone(
                    session_id=sid, turn_counter=turn_counter, iteration=iteration,
                    msg_count=msg_count,
                    msg_roles=roles,
                    head=head,
                    tail=tail,
                    estimated_tokens=_final_tokens,
                    system_text_chars=_sys_chars,
                    tool_schema_chars=_schema_chars,
                    response_format_chars=_resp_chars,
                    system_prompt_text=_system_prompt_text,
                    tool_schema_text=_tool_schema_text,
                    system_prompt_full_text=_system_prompt_full,
                    system_prompt_sha1_16=_system_prompt_sha,
                ))
        except Exception:
            pass  # never break baseline
        history_tokens = estimate_messages_tokens(dynamic_result.projection.projected_messages)
        # Legacy bridge injects context_payload as a user message — those tokens
        # are consumed from history_budget but were not counted above.  The
        # structured_extension path sends the payload via extra_body and does
        # NOT consume message tokens, so the overhead is only relevant when
        # the provider will fall back to legacy_bridge mode.
        if packed_request.context_payload_tokens > 0:
            history_tokens += packed_request.context_payload_tokens
        return TurnPreflightResult(
            assembled_context_layout=dynamic_result.assembled_context_layout,
            task_profile=dynamic_result.task_profile,
            projection=dynamic_result.projection,
            packed_request=packed_request,
            request_snapshot=None,
            history_tokens=history_tokens,
            request_mode=request_mode,
            budget_audit=dynamic_result.budget_audit,
            system_prompt_sections=dynamic_result.system_prompt_sections,
        )
