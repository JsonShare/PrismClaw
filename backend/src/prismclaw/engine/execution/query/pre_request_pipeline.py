from dataclasses import dataclass
from typing import Any, List, Optional
import copy
import json
import logging
import time as _time

from prismclaw.engine.execution.query.state import QueryLoopState
from prismclaw.engine.execution.query.builder import rebuild_query_loop_state
from prismclaw.schema.llm.request import PackedLLMRequest
from prismclaw.engine.execution.query.turn_preparation import TurnPreflightResult
from prismclaw.engine.toolkit.registry.governance import resolve_registry_tool_governance
from prismclaw.engine.execution.checkpoint import RequestAssemblySnapshot
from prismclaw.engine.state.turn_state import ToolGovernanceState, TurnState
from prismclaw.schema.session.types import SessionTree
from prismclaw.schema.agent.session import ProjectionInputMessage, SystemReminderProjection
from prismclaw.engine.compression.digest import build_prepared_turn_digest, build_tool_pool_id
from prismclaw.engine.execution.query.context_preparation import build_static_context_for_runner
from prismclaw.engine.context.microcompact import (
    batch_cleanup_threshold,
    batch_microcompact_tick,
    microcompact_query_messages,
    resolve_microcompact_mode,
)
from prismclaw.engine.compression.compaction import estimate_tokens
from prismclaw.engine.compression.context_zones import resolve_runtime_zones
from prismclaw.engine.execution.request.model_fallback import resolve_model_max_output
from prismclaw.schema.shared.utils import count_image_parts
from prismclaw.engine.compression.file_reinjection import (
    build_file_reinjection_messages,
)

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
        str(record.canonical_name)
        for record in records
        if getattr(record, "canonical_name", "")
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
    core = [
        tool
        for tool in sorted_visible_tools
        if getattr(tool, "name", None) not in discovered
    ]
    by_name = {getattr(tool, "name", ""): tool for tool in sorted_visible_tools}
    tail = [by_name[name] for name in discovered_ordered_names if name in by_name]
    return [*core, *tail]


def _resolve_workspace_mode(*, request: Any, tool_ctx: Any) -> str:
    request_mode = getattr(request, "mode_override", None)
    if isinstance(request_mode, str) and request_mode:
        return request_mode
    ctx_mode = getattr(tool_ctx, "mode", None)
    if isinstance(ctx_mode, str) and ctx_mode:
        return ctx_mode
    return "work"
def _build_request_snapshot(
    *,
    request_id: str | None,
    query_loop_state: QueryLoopState,
    effective_model: str,
    request_mode: str | None,
    packed_request: PackedLLMRequest,
    tools_for_llm: list[Any],
) -> RequestAssemblySnapshot:
    return RequestAssemblySnapshot(
        request_id=str(request_id or ""),
        query_loop_revision_id=query_loop_state.loop_revision_id,
        model=effective_model,
        request_mode=request_mode,
        messages_for_api=list(packed_request.messages),
        system=packed_request.system,
        context_payload=packed_request.context_payload,
        tool_schemas_fingerprint=build_tool_pool_id(tools_for_llm) if tools_for_llm else None,
        cache_strategy=dict(packed_request.cache_strategy or {}),
    )


def _collect_active_tool_call_ids(messages: list[ProjectionInputMessage]) -> set[str]:
    active_tool_call_ids: set[str] = set()
    for msg in messages:
        if isinstance(msg, SystemReminderProjection):
            continue
        if msg.get("role") == "tool" and "tool_call_id" in msg:
            active_tool_call_ids.add(msg["tool_call_id"])
        if msg.get("role") == "assistant" and "tool_calls" in msg:
            for tool_call in msg["tool_calls"]:
                if "id" in tool_call:
                    active_tool_call_ids.add(tool_call["id"])
    return active_tool_call_ids


@dataclass
class PreparedTurn:
    tools_for_llm: List[Any]
    turn_state_delta: TurnState
    audit_trail: List[dict]
    prepared_turn_digest: str
    query_loop_state: QueryLoopState
    prompt_inputs: Any
    history_budget: int
    preflight: TurnPreflightResult | None = None
    compression_preparation: Optional[Any] = None
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
        request: Any,
        deps: Any,
        tree: SessionTree,
        turn_state: TurnState,
        tool_workspace_path: Any,
        effective_model: str,
        tool_ctx: Any,
        skill_invocation: Any,
        attachment_guidance: Optional[str],
        query_loop_state: QueryLoopState,
        compression_runtime: Any,
        abort_event: Any,
        compression_orchestrator: Any,
    ) -> PreparedTurn:
        _deepcopy_start = _time.monotonic()
        new_turn_state = copy.deepcopy(turn_state)
        _deepcopy_ms = (_time.monotonic() - _deepcopy_start) * 1000
        if _deepcopy_ms > 1.0:
            logger.debug("pre_request_deepcopy %.1fms", _deepcopy_ms)
        audit_trail: List[dict] = []
        mode = _resolve_workspace_mode(request=request, tool_ctx=tool_ctx)

        # Static shell ownership now lives on deps.static_shell_cache.
        # The pipeline must resolve the current plan each run so plugin/session
        # generation changes cannot be masked by instance-local state.
        static_plan = await build_static_context_for_runner(
            request=request,
            deps=deps,
            tree=tree,
            tool_workspace_path=tool_workspace_path,
            effective_model=effective_model,
            mode=mode,
            skill_invocation=skill_invocation,
            discovered_tools=turn_state.discovered_tool_names(),
            subagent_depth=tool_ctx.subagent_depth,
        )
        
        # 2. Tool Visibility & Assembly
        subagent_depth = tool_ctx.subagent_depth if tool_ctx else 0

        # 能否暂停等人回答，取决于是否持有会话队列（当前仅 Web 主循环具备）。
        # CLI / 测试等无队列场景属无头执行，需交互用户的工具不下发给模型。
        # 定时任务复用 Web 主链路，持有队列，因此与普通会话同等待遇。
        headless = getattr(deps, "session_queue", None) is None

        resolved_toolset = resolve_registry_tool_governance(
            deps.tools,
            skill_invocation=skill_invocation,
            discovered_tools=turn_state.discovered_tool_names(),
            mode=mode,
            subagent_depth=subagent_depth,
            headless=headless,
        )
        visible_tool_names = set(resolved_toolset.visible_tools_for_llm)

        # Force-freeze tools when the tool-loop policy has flagged them
        # as runaway loops. Removing them
        # from the visible set ensures the model literally cannot call
        # them again, even if it ignores the guidance injection.
        _tls = query_loop_state.tracking.tool_loop_state
        _callable_tools = set(resolved_toolset.callable_tools)
        if _tls is not None and _tls.frozen_tools:
            for frozen_name in _tls.frozen_tools:
                visible_tool_names.discard(frozen_name)
                _callable_tools.discard(frozen_name)

        # 工具数组顺序收口（批 4b-2）：append_only 保护前缀缓存——核心可见工具
        # 沿用 registry 稳定排序，本轮已发现工具只追加到末尾，绝不重排/中间插入。
        _sorted_tools = deps.tools.list_tools(
            visible_tool_names,
            subagent_depth=subagent_depth,
        )
        _tool_policy = (
            getattr(getattr(deps, "config", None), "tool_mutation_policy", None)
            or "append_only"
        )
        if _tool_policy == "unbounded":
            tools_for_llm = [
                deps.tools._to_openai_function(tool) for tool in _sorted_tools
            ]
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
            tools_for_llm = [
                deps.tools._to_openai_function(tool) for tool in _ordered
            ]
            _tail_appended = [n for n in _discovered if n in visible_tool_names]
            if _tail_appended:
                logger.info(
                    "tools_order cache_invalidated=tail_append visible=%d tail_appended=%d",
                    len(tools_for_llm),
                    len(_tail_appended),
                )
        tools_for_llm = tools_for_llm or []
        declared_tool_names = set(deps.tools.names())
        visible_name_set = set(resolved_toolset.visible_tools_for_llm)
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
            visible_tools=resolved_toolset.visible_tools_for_llm,
            callable_tools=list(_callable_tools),
            gated_tools=resolved_toolset.denied_tools,
            discovered_tool_names=turn_state.discovered_tool_names(),
            active_skill_name=getattr(skill_invocation, "skill_name", None),
            restricted=(
                mode == "chat"
                or visible_name_set != declared_tool_names
                or _callable_tools != declared_tool_names
            ),
        )
        new_turn_state.canonical_decision_state.tool_governance_state = (
            governance_state
        )
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
        if _mc_ws and _mc_sid:
            from pathlib import Path as _P
            _safe_sid = str(_mc_sid).replace("#", "-").replace(":", "-")
            mc_working_dir = str(_P(str(_mc_ws)) / "sessions" / _safe_sid)
        else:
            mc_working_dir = str(_mc_ws) if _mc_ws else None

        _mc_mode = resolve_microcompact_mode()
        microcompact_result = None
        if _mc_mode == "batch":
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
                    counter=getattr(
                        compression_runtime, "tool_result_chars_since_cleanup", 0
                    ),
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

        # 4. State Pruning (Lifecycle management for Replacement State)
        # We prune the replacement state to avoid infinite memory growth.
        # We only keep replacements for tool_call_ids that are actually present in the current LLM request.
        if new_turn_state.replacement_state:
            request_messages = preflight.packed_request.messages if preflight is not None else []
            removed_ids = new_turn_state.replacement_state.retain_only(
                _collect_active_tool_call_ids(request_messages)
            )
            for removed_id in removed_ids:
                audit_trail.append({"action": "pruned_replacement_state", "tool_call_id": removed_id})

        packed_request = preflight.packed_request if preflight is not None else None
        # 批 4b-2：为 stream_fn 填充 input_budget（ratio=A_eff 已扣图片；
        # absolute=window−image_tokens，仅影响钳制与日志，不改 history 预算计算）。
        if packed_request is not None:
            _budget_cfg = deps.config.context.budget
            _image_tokens = 0
            try:
                _tpr = int(
                    getattr(getattr(deps.config, "vision", None), "tokens_per_image", 0)
                    or 0
                )
                _cap = int(
                    getattr(getattr(deps.config, "vision", None), "max_images_inline", 0)
                    or 0
                )
                _img_n = count_image_parts(packed_request.messages)
                _image_tokens = min(_img_n, _cap) * _tpr
            except Exception:
                _image_tokens = 0
            if getattr(_budget_cfg, "mode", "absolute") == "ratio":
                _tools_wire = 0
                try:
                    _tools_wire = (
                        int(deps.tools.wire_tokens_estimate())
                        if getattr(deps, "tools", None)
                        else 0
                    )
                except Exception:
                    _tools_wire = 0
                # 配置错误（窗口低于分区地板）响亮抛出，不做静默回退——
                # 静默回退会把 input_budget 填成≈整窗，ratio 预算名存实亡。
                _input_budget = resolve_runtime_zones(
                    budget=_budget_cfg,
                    window=getattr(_budget_cfg, "window_tokens", None),
                    model_max_output=resolve_model_max_output(effective_model),
                    tools_wire=_tools_wire,
                    image_tokens=_image_tokens,
                ).input_budget
            else:
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
                request_mode=preflight.request_mode,
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
