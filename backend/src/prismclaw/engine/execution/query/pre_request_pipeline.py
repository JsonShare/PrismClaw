from __future__ import annotations

import copy
import json
import logging
import time as _time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prismclaw.engine.compression.compaction import estimate_tokens
from prismclaw.engine.compression.digest import build_prepared_turn_digest, build_tool_pool_id
from prismclaw.engine.compression.file_reinjection import (
    build_file_reinjection_messages,
)
from prismclaw.engine.context.microcompact import (
    batch_cleanup_threshold,
    batch_microcompact_tick,
    microcompact_query_messages,
    resolve_microcompact_mode,
)
from prismclaw.engine.execution.checkpoint import RequestAssemblySnapshot
from prismclaw.engine.execution.query._turn_helpers import TurnPreflightResult
from prismclaw.engine.execution.query.builder import rebuild_query_loop_state
from prismclaw.engine.execution.query.context_preparation import build_static_context_for_runner
from prismclaw.engine.execution.query.state import QueryLoopState
from prismclaw.engine.execution.query.turn_planner import TurnPlanner
from prismclaw.engine.execution.request.model_fallback import resolve_model_max_output
from prismclaw.engine.state.turn_state import ToolGovernanceState, TurnState
from prismclaw.schema.agent.session import SystemReminderProjection
from prismclaw.schema.llm.request import PackedLLMRequest
from prismclaw.schema.session.types import SessionTree
from prismclaw.schema.shared.utils import count_image_parts

if TYPE_CHECKING:
    import asyncio

    from prismclaw.engine.compression.orchestrator import (
        CompressionOrchestrator,
        CompressionPreparation,
    )
    from prismclaw.engine.execution.loop.deps import AgentRunnerDeps
    from prismclaw.engine.execution.query.turn_planner import TurnPlan
    from prismclaw.engine.execution.request.system_prompt import PromptInputs
    from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
    from prismclaw.schema.agent.types import RunRequest
    from prismclaw.schema.services.skills import SkillInvocationState
    from prismclaw.schema.session.cache_types import CompressionRuntimeState

logger = logging.getLogger(__name__)


def _discovered_in_order(turn_state: TurnState) -> list[str]:
    """按发现顺序返回已发现工具名（append_only 的尾部追加顺序来源）。"""
    ledger = getattr(turn_state, "discovered_tool_ledger", None)
    if ledger is None:
        return []
    records = sorted(
        getattr(ledger, "records", []),
        key=lambda record: int(getattr(record, "discovery_order", 0) or 0),
    )
    return [
        str(record.canonical_name) for record in records if getattr(record, "canonical_name", "")
    ]


def order_visible_tools_append_only(
    *,
    sorted_visible_tools: list[Any],
    discovered_ordered_names: list[str],
) -> list[Any]:
    """append_only 顺序：核心可见工具保持传入 registry 排序，已发现工具追加到末尾。

    ``sorted_visible_tools`` 必须是 ``ToolRegistry.list_tools(...)`` 的产物
    （已按 (is_mcp, name) 排序）。本函数只做「核心在前、发现追加在后」的分区，
    不重排核心前缀。
    """
    discovered = set(discovered_ordered_names)
    core = [tool for tool in sorted_visible_tools if getattr(tool, "name", None) not in discovered]
    by_name = {getattr(tool, "name", ""): tool for tool in sorted_visible_tools}
    tail = [by_name[name] for name in discovered_ordered_names if name in by_name]
    return [*core, *tail]


def _build_request_snapshot(
    *,
    request_id: str | None,
    query_loop_state: QueryLoopState,
    effective_model: str,
    packed_request: PackedLLMRequest,
    tools_for_llm: list[Any],
) -> RequestAssemblySnapshot:
    return RequestAssemblySnapshot(
        request_id=str(request_id or ""),
        query_loop_revision_id=query_loop_state.loop_revision_id,
        model=effective_model,
        messages_for_api=list(packed_request.messages),
        system=packed_request.system,
        context_payload=packed_request.context_payload,
        tool_schemas_fingerprint=build_tool_pool_id(tools_for_llm) if tools_for_llm else None,
        cache_strategy=dict(packed_request.cache_strategy or {}),
    )


def _collect_active_tool_call_ids(
    messages: Iterable[Mapping[str, Any] | SystemReminderProjection],
) -> set[str]:
    active_tool_call_ids: set[str] = set()
    for msg in messages:
        if isinstance(msg, SystemReminderProjection):
            continue
        if msg.get("role") == "tool":
            tool_call_id = msg.get("tool_call_id")
            if isinstance(tool_call_id, str):
                active_tool_call_ids.add(tool_call_id)
        if msg.get("role") == "assistant":
            tool_calls = msg.get("tool_calls")
            if isinstance(tool_calls, list):
                for tool_call in tool_calls:
                    if isinstance(tool_call, dict):
                        call_id = tool_call.get("id")
                        if isinstance(call_id, str):
                            active_tool_call_ids.add(call_id)
    return active_tool_call_ids


def format_turn_plan_log(plan: TurnPlan) -> str:
    """单行 `TurnPlan` 摘要（G4）。

    抽成纯函数只为可测：字段清单就是「plan 的全部决策」，
    新增字段必须同步进来，否则观测会与真实决策脱节。
    """
    return (
        f"turn_plan complexity={plan.complexity} "
        f"visible_tools={len(plan.toolset.visible_tools_for_llm)} "
        f"searchable={len(plan.toolset.searchable_tools)} "
        f"skip_compaction={plan.skip_history_compaction} "
        f"l1={plan.include_l1_memory} "
        f"headless={plan.headless}"
    )


@dataclass
class PreparedTurn:
    tools_for_llm: list[Any]
    turn_state_delta: TurnState
    audit_trail: list[dict[str, Any]]
    prepared_turn_digest: str
    query_loop_state: QueryLoopState
    prompt_inputs: PromptInputs
    history_budget: int
    preflight: TurnPreflightResult | None = None
    compression_preparation: CompressionPreparation | None = None
    # SDD R-A2: measured tool wire cost (already deducted from history_budget),
    # forwarded to the drift sentinel in log_turn_token_usage.
    tools_wire_tokens: int = 0


class PreRequestPipeline:
    """
    Pipeline for constructing the request to the LLM.
    This pipeline MUST be functional: it should NOT mutate the provided `tree` or global state.
    It takes immutable snapshots as input and returns a `PreparedTurn`.
    """

    async def run(
        self,
        *,
        request: RunRequest,
        deps: AgentRunnerDeps,
        tree: SessionTree,
        turn_state: TurnState,
        tool_workspace_path: str | None,
        effective_model: str,
        tool_ctx: ToolContext,
        skill_invocation: SkillInvocationState | None,
        attachment_guidance: str | None,
        query_loop_state: QueryLoopState,
        compression_runtime: CompressionRuntimeState,
        abort_event: asyncio.Event,
        compression_orchestrator: CompressionOrchestrator,
    ) -> PreparedTurn:
        _deepcopy_start = _time.monotonic()
        new_turn_state = copy.deepcopy(turn_state)
        _deepcopy_ms = (_time.monotonic() - _deepcopy_start) * 1000
        if _deepcopy_ms > 1.0:
            logger.debug("pre_request_deepcopy %.1fms", _deepcopy_ms)
        audit_trail: list[dict[str, Any]] = []

        # ① 唯一决策点：governance / 熔断剔除 / simple 开关 / 无头 都在这里
        # 算一次，后续静态壳与工具面共同消费，不再各自收窄（R3）。
        _tls = query_loop_state.tracking.tool_loop_state
        _frozen_tools = frozenset(_tls.frozen_tools) if _tls is not None else frozenset()
        plan = TurnPlanner().plan(
            request=request,
            deps=deps,
            turn_state=turn_state,
            tool_ctx=tool_ctx,
            skill_invocation=skill_invocation,
            frozen_tools=_frozen_tools,
        )
        logger.info(format_turn_plan_log(plan))
        skip_history_compaction = plan.skip_history_compaction
        subagent_depth = tool_ctx.subagent_depth if tool_ctx else 0

        # ② 工具面与静态壳共用 plan 的同一份终集（已含熔断剔除）。
        # 任何二次收窄都会让 `<tools>` 与实发工具面分叉（D14）。
        visible_tool_names = set(plan.toolset.visible_tools_for_llm)
        _callable_tools = set(plan.toolset.callable_tools)

        # ③ 静态壳：只消费 workspace 事实 + model + 本轮终集，不受复杂度
        # 影响（R1）。
        static_plan = await build_static_context_for_runner(
            request=request,
            deps=deps,
            tree=tree,
            tool_workspace_path=tool_workspace_path,
            effective_model=effective_model,
            skill_invocation=skill_invocation,
            discovered_tools=turn_state.discovered_tool_names(),
            subagent_depth=subagent_depth,
            visible_tool_names=visible_tool_names,
        )

        # 减法：simple 路径仅跳过重管道（microcompact / reinjection），
        # 不裁剪工具集——关键词打分不足以判断任务是否真的不需要写工具，
        # 误判会让模型拿不到 write/edit 而无法完成任务。

        # 工具数组顺序收口（批 4b-2）：append_only 保护前缀缓存——核心可见工具
        # 沿用 registry 稳定排序，本轮已发现工具只追加到末尾，绝不重排/中间插入。
        _sorted_tools = deps.tools.list_tools(
            visible_tool_names,
            subagent_depth=subagent_depth,
        )
        _tool_policy = (
            getattr(getattr(deps, "config", None), "tool_mutation_policy", None) or "append_only"
        )
        if _tool_policy == "unbounded":
            tools_for_llm = [deps.tools._to_openai_function(tool) for tool in _sorted_tools]
        else:
            if _tool_policy == "frozen":
                logger.warning(
                    "tool_mutation_policy=frozen is incompatible with dynamic "
                    "discovery; degrading to append_only"
                )
            _discovered = _discovered_in_order(turn_state)
            _ordered = order_visible_tools_append_only(
                sorted_visible_tools=_sorted_tools,
                discovered_ordered_names=_discovered,
            )
            tools_for_llm = [deps.tools._to_openai_function(tool) for tool in _ordered]
            _tail_appended = [n for n in _discovered if n in visible_tool_names]
            if _tail_appended:
                logger.info(
                    "tools_order cache_invalidated=tail_append visible=%d tail_appended=%d",
                    len(tools_for_llm),
                    len(_tail_appended),
                )
        tools_for_llm = tools_for_llm or []
        declared_tool_names = set(deps.tools.names())
        # 必须使用过滤后的可见集，否则 governance 审计里仍声称写工具可见，
        # 与实发 tools_for_llm 不一致。
        visible_name_set = set(visible_tool_names)
        # P0-①（工具注入量观测）：visible 子集的真实 wire 字节
        # （json.dumps 即请求体实发字符），与 declared 数一起给出
        # 注册→注入衰减明细。区别于 registry.wire_tokens_estimate()
        # 的全注册表 token 上界估算，此值为治理过滤后的实发口径。
        if tools_for_llm:
            logger.info(
                "tools_wire visible=%d declared=%d wire_bytes=%d",
                len(tools_for_llm),
                len(declared_tool_names),
                len(json.dumps(tools_for_llm, ensure_ascii=False).encode("utf-8")),
            )
        governance_state = ToolGovernanceState.from_contract(
            declared_tools=declared_tool_names,
            visible_tools=visible_tool_names,
            callable_tools=list(_callable_tools),
            gated_tools=plan.toolset.denied_tools,
            discovered_tool_names=turn_state.discovered_tool_names(),
            active_skill_name=getattr(skill_invocation, "skill_name", None),
            restricted=(
                visible_name_set != declared_tool_names or _callable_tools != declared_tool_names
            ),
        )
        new_turn_state.canonical_decision_state.tool_governance_state = governance_state
        new_turn_state.capability_decision_bundle.tool_governance = governance_state
        new_turn_state.capability_decision_bundle.decision_digest = ""
        new_turn_state.tool_pool_id = build_tool_pool_id(tools_for_llm) if tools_for_llm else ""

        # 3. Context Assembly
        source_message_set = query_loop_state.query_view.source_messages

        # Layer 3: microcompact — read-time projection on messages_for_query.
        # Clears old tool_result content while preserving the pair structure.
        # When working_dir is available, cleared results are archived to disk
        # and replaced with <persisted-output> references (readable via Read tool).
        effective_message_set = source_message_set
        # working_dir 必须是 session 根（不是 workspace 根）—— 否则 microcompact
        # 会把 tool_results mkdir 到 workspace 根，与 session 级隔离冲突。
        _mc_ws = tool_workspace_path
        _mc_sid = getattr(tool_ctx, "session_id", "") if tool_ctx else ""
        mc_working_dir: str | None = None
        if _mc_ws and _mc_sid:
            _safe_sid = str(_mc_sid).replace("#", "-").replace(":", "-")
            mc_working_dir = str(Path(str(_mc_ws)) / "sessions" / _safe_sid)
        else:
            mc_working_dir = str(_mc_ws) if _mc_ws else None

        _mc_mode = resolve_microcompact_mode()
        microcompact_result = None
        # 减法：simple 路径且无工具结果时，microcompact 无事可做，直接跳过。
        _has_tool_results = any(
            isinstance(m, dict) and m.get("role") == "tool" for m in source_message_set.messages
        )
        _skip_microcompact = skip_history_compaction and not _has_tool_results
        if _skip_microcompact:
            pass
        elif _mc_mode == "batch":
            # batch：不每轮滚动改写；只累加 R 区字符，超预算才批量清理一次。
            _mc_threshold = 0
            try:
                _mc_threshold = batch_cleanup_threshold(
                    budget=deps.config.context.budget,
                    tool_results=deps.config.context.compaction.tool_results,
                    window=getattr(deps.config.context.budget, "window_tokens", None),
                    model_max_output=resolve_model_max_output(effective_model),
                    tools_wire=(
                        int(deps.tools.wire_tokens_estimate())
                        if getattr(deps, "tools", None)
                        else 0
                    ),
                )
            except Exception:
                _mc_threshold = 0
            try:
                _new_counter, _new_total, microcompact_result = batch_microcompact_tick(
                    messages=list(source_message_set.messages),
                    counter=getattr(compression_runtime, "tool_result_chars_since_cleanup", 0),
                    prev_total=getattr(compression_runtime, "tool_result_chars_probe", 0),
                    threshold=_mc_threshold,
                    working_dir=mc_working_dir,
                )
                compression_runtime.tool_result_chars_since_cleanup = _new_counter
                compression_runtime.tool_result_chars_probe = _new_total
            except Exception:
                microcompact_result = None
        else:
            # incremental：每轮滚动改写（现状语义不变）。
            microcompact_result = microcompact_query_messages(
                list(source_message_set.messages),
                working_dir=mc_working_dir,
            )

        if microcompact_result is not None and microcompact_result.changed:
            effective_message_set = source_message_set.replace_messages(
                microcompact_result.messages
            )

        # Layer 6: file re-injection — only after a compaction has occurred;
        # ephemeral (query-time rebuild, never persisted into the tree).
        # 减法：simple 路径跳过（无压缩发生，没有需要重注入的文件内容）。
        reinjected: list[Any] = []
        if not skip_history_compaction:
            reinjected = build_file_reinjection_messages(
                tree=tree,
                estimate_tokens=estimate_tokens,
            )
        if reinjected:
            effective_message_set = effective_message_set.replace_messages(
                [*effective_message_set.messages, *reinjected]
            )

        compression_preparation = await compression_orchestrator.prepare_turn(
            request=request,
            deps=deps,
            tree=tree,
            static_result=static_plan,
            include_l1_memory=plan.include_l1_memory,
            attachment_guidance=attachment_guidance,
            query_loop_state=rebuild_query_loop_state(
                query_loop_state,
                messages_for_query=effective_message_set,
            ),
            compression_runtime=compression_runtime,
            model=effective_model,
            abort_event=abort_event,
            turn_state=turn_state,
            model_max_output=resolve_model_max_output(effective_model),
        )

        preflight: TurnPreflightResult | None = None
        if compression_preparation.preflight and compression_preparation.preflight.packed_request:
            preflight = compression_preparation.preflight

        # 4. State Pruning:替换状态只保留当前请求仍在使用的 tool_call_id，防止无界增长。
        if new_turn_state.replacement_state:
            request_messages = preflight.packed_request.messages if preflight is not None else []
            removed_ids = new_turn_state.replacement_state.retain_only(
                _collect_active_tool_call_ids(request_messages)
            )
            for removed_id in removed_ids:
                audit_trail.append(
                    {"action": "pruned_replacement_state", "tool_call_id": removed_id}
                )

        packed_request = preflight.packed_request if preflight is not None else None
        # 批 4b-2：为 client 填充 input_budget
        # （absolute=window−image_tokens，仅影响钳制与日志，不改 history 预算计算）。
        if packed_request is not None:
            _budget_cfg = deps.config.context.budget
            _image_tokens = 0
            try:
                _tpr = int(
                    getattr(getattr(deps.config, "vision", None), "tokens_per_image", 0) or 0
                )
                _cap = int(
                    getattr(getattr(deps.config, "vision", None), "max_images_inline", 0) or 0
                )
                _img_n = count_image_parts(packed_request.messages)
                _image_tokens = min(_img_n, _cap) * _tpr
            except Exception:
                _image_tokens = 0
            _input_budget = max(
                0, int(getattr(_budget_cfg, "window_tokens", 0) or 0) - _image_tokens
            )
            packed_request.input_budget = _input_budget
        digest = build_prepared_turn_digest(
            messages_for_llm=packed_request.messages if packed_request is not None else [],
            system_blocks=packed_request.system if packed_request is not None else [],
            tools_for_llm=tools_for_llm,
            context_payload=packed_request.context_payload if packed_request is not None else None,
        )
        prepared_query_loop_state = rebuild_query_loop_state(
            query_loop_state,
            messages_for_query=effective_message_set,
            compression_state=compression_preparation,
        )
        if preflight is not None:
            preflight.request_snapshot = _build_request_snapshot(
                request_id=getattr(request, "request_id", None),
                query_loop_state=prepared_query_loop_state,
                effective_model=effective_model,
                packed_request=preflight.packed_request,
                tools_for_llm=tools_for_llm,
            )

        _twt = (
            static_plan.shell_snapshot.tools_wire_tokens
            if static_plan.shell_snapshot is not None
            else 0
        )
        return PreparedTurn(
            tools_for_llm=tools_for_llm,
            turn_state_delta=new_turn_state,
            audit_trail=audit_trail,
            prepared_turn_digest=digest,
            query_loop_state=prepared_query_loop_state,
            prompt_inputs=static_plan.prompt_inputs,
            history_budget=static_plan.history_budget,
            preflight=preflight,
            compression_preparation=compression_preparation,
            tools_wire_tokens=_twt,
        )
