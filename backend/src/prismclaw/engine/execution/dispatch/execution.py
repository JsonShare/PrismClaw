from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prismclaw.engine.execution.handoff.contract import normalize_handoff_request_payload
from prismclaw.engine.compression.result_compaction import (
    ResultCompactionTurn,
    compact_tool_results_for_turn_async,
)
from prismclaw.schema.session import REPLACEMENT_DECISION_CUSTOM_TYPE
from prismclaw.schema.content import replacement_decision_from_artifact
from prismclaw.engine.execution.dispatch.execution_entry import execute_tool_calls
from prismclaw.engine.execution.output.tool_result_rendering import tool_result_to_llm_content
from prismclaw.schema.agent.data_types import TextBlock
from prismclaw.schema.agent.session import MessageEntry
from prismclaw.schema.content.compaction import CompactionArtifact
from prismclaw.schema.content.content_types import ToolResult
from prismclaw.schema.content.streaming import ToolCallEnd
from prismclaw.schema.session.projection import SessionProjectionEngine
from prismclaw.schema.session.types import CustomEntry, EXTRACT_EVIDENCE_CUSTOM_TYPE, HANDOFF_REQUEST_CUSTOM_TYPE, RESULT_COMPACTION_REFERENCE_CUSTOM_TYPE, SEARCH_EVIDENCE_CUSTOM_TYPE, generate_entry_id, now_iso

logger = logging.getLogger(__name__)


def _session_working_dir(tool_ctx: Any) -> str | None:
    """tool_ctx 是 ToolContext（有 workspace_path + session_id）。

    context_blocks / tool_results 持久化路径：
    有 session_id → <ws>/sessions/<safe_sid>/  （session 私有，不污染 workspace 根）
    无 session_id → workspace_path              （降级兼容）

    ⚠️ subagent session_id 格式是 ``parent#sub:uuid`` — 有 ``#`` 和 ``:``，
    这两个字符不能出现在文件路径段里。这里做一次 safe sanitize：
    ``#`` 和 ``:`` 替换成 ``-``，让 subagent 也能落盘。

    目录不存在时自动 mkdir(parents=True) —— 先上传文件再咨询的场景下
    session 目录可能还没被 chat_runner 创建，这里兜底确保存在。
    """
    ws = getattr(tool_ctx, "workspace_path", None)
    sid = getattr(tool_ctx, "session_id", "")
    if not ws or not sid:
        return str(ws) if ws else None
    safe_sid = sid.replace("#", "-").replace(":", "-")
    session_root = Path(ws) / "sessions" / safe_sid
    session_root.mkdir(parents=True, exist_ok=True)
    return str(session_root)


def _handoff_request_projection(result: ToolResult) -> dict[str, Any] | None:
    artifact = result.persistence_artifact
    if artifact is None or artifact.handoff_request is None:
        return None
    return normalize_handoff_request_payload(
        artifact.handoff_request.model_dump(mode="python", exclude_none=True)
    )


def _tool_result_compaction_artifact(result: ToolResult) -> CompactionArtifact | None:
    artifact = result.ui_artifact
    if artifact is None or artifact.artifact_type != "result_compaction_preview":
        return None
    preview = artifact.result_compaction_preview
    if not isinstance(preview, dict):
        return None
    try:
        return CompactionArtifact.model_validate(preview)
    except Exception:
        return None


@dataclass
class HandoffDispatchPlan:
    tool_calls: list[dict[str, Any]]
    resolved_dispatch_extras_by_call_id: dict[str, Any]
    cap_exceeded_call_ids: dict[str, str]
    cap_exceeded_info_by_call: dict[str, Any]


@dataclass
class ToolExecutionArtifacts:
    tool_calls: list[dict[str, Any]]
    results: list[ToolResult]
    compaction_turn: ResultCompactionTurn | None = None


@dataclass
class ToolResultPersistenceProjection:
    entries: list[Any]
    events: list[ToolCallEnd]


async def plan_handoff_dispatch(
    *,
    deps: Any,
    request: Any,
    tool_calls: list[dict[str, Any]],
    ) -> HandoffDispatchPlan:
    resolved_dispatch_extras_by_call_id: dict[str, Any] = {}
    cap_exceeded_call_ids: dict[str, str] = {}
    cap_exceeded_info_by_call: dict[str, Any] = {}

    if deps.handoff_supervisor is not None:
        deps.handoff_supervisor.increment_turn(request.session_id)
        sub_agent_calls = [
            tool_call
            for tool_call in tool_calls
            if (((tool_call or {}).get("function") or {}).get("name") or "") == "sub_agent"
        ]
        if sub_agent_calls:
            resolved, cap_exceeded = await deps.handoff_supervisor.resolve_dispatch_extras(
                session_id=request.session_id,
                sub_agent_calls=sub_agent_calls,
            )
            resolved_dispatch_extras_by_call_id = dict(resolved)
            for call_id, cap_info in cap_exceeded.items():
                terminated_at_hop = (
                    cap_info.effective_hop_count if cap_info.effective_hop_count > 0 else 1
                )
                cap_exceeded_call_ids[call_id] = (
                    deps.handoff_supervisor.build_cap_exceeded_error_text(
                        cap_info,
                        terminated_at_hop=terminated_at_hop,
                    )
                )
                cap_exceeded_info_by_call[call_id] = cap_info

    remaining_tool_calls = [
        tool_call
        for tool_call in tool_calls
        if tool_call.get("id", "") not in cap_exceeded_call_ids
    ]
    return HandoffDispatchPlan(
        tool_calls=remaining_tool_calls,
        resolved_dispatch_extras_by_call_id=resolved_dispatch_extras_by_call_id,
        cap_exceeded_call_ids=cap_exceeded_call_ids,
        cap_exceeded_info_by_call=cap_exceeded_info_by_call,
    )


def _dispatch_extras_transport_dict(dispatch_extras: Any) -> dict[str, Any]:
    per_call: dict[str, Any] = {
        "handoff_chain_id": dispatch_extras.handoff_chain_id,
        "handoff_hop_index": dispatch_extras.handoff_hop_index,
        "effective_hop_index": dispatch_extras.effective_hop_index,
    }
    from_subagent = getattr(dispatch_extras, "from_subagent", None)
    if from_subagent is not None:
        per_call["pending_handoff_source_subagent"] = from_subagent
    source_skill_origin = getattr(dispatch_extras, "source_skill_origin", None)
    if source_skill_origin is not None:
        per_call["pending_handoff_source_skill_origin"] = source_skill_origin
    source_preload_skills = getattr(dispatch_extras, "source_preload_skills", None)
    if source_preload_skills:
        per_call["pending_handoff_source_preload_skills"] = list(source_preload_skills)
    if getattr(dispatch_extras, "source_is_fork", False):
        per_call["pending_handoff_source_is_fork"] = True
    merged_from_chains = getattr(dispatch_extras, "merged_from_chains", None)
    if merged_from_chains is not None:
        per_call["merged_from_chains"] = merged_from_chains
    return per_call


async def apply_handoff_dispatch_plan(
    *,
    tool_ctx: Any,
    handoff_plan: HandoffDispatchPlan,
    subagent_progress_sink: Any,
    emit_cap_exceeded_progress: Any,
) -> None:
    if handoff_plan.resolved_dispatch_extras_by_call_id:
        tool_ctx.pending_handoff_dispatch_extras = {
            call_id: _dispatch_extras_transport_dict(dispatch_extras)
            for call_id, dispatch_extras in handoff_plan.resolved_dispatch_extras_by_call_id.items()
        }

    if handoff_plan.cap_exceeded_info_by_call and subagent_progress_sink is not None:
        try:
            await emit_cap_exceeded_progress(
                sink=subagent_progress_sink,
                cap_info_by_call=handoff_plan.cap_exceeded_info_by_call,
            )
        except Exception:
            logger.warning("handoff.cap_exceeded.synthetic_emit_failed", exc_info=True)


async def execute_tool_calls_with_handoff(
    *,
    deps: Any,
    request: Any,
    tool_calls: list[dict[str, Any]],
    tool_ctx: Any,
    cap_exceeded_call_ids: dict[str, str],
    finish_reason: str | None = None,
) -> ToolExecutionArtifacts:
    # 截断保护（对齐 Pi P5 防御性编程）
    if finish_reason == "length" and tool_calls:
        from prismclaw.engine.execution.dispatch.truncation import fail_truncated_tool_calls
        return fail_truncated_tool_calls(
            tool_calls=tool_calls,
            session_id=getattr(request, "session_id", "?"),
        )

    results: list[ToolResult] = await execute_tool_calls(
        deps.tools,
        tool_calls,
        tool_ctx,
        default_tool_timeout_s=deps.config.timeouts.tool_seconds,
    )
    effective_tool_calls = list(tool_calls)

    if cap_exceeded_call_ids:
        synthetic_results = [
            ToolResult(
                tool_call_id=cid,
                content=[TextBlock(text=msg)],
                is_error=True,
            )
            for cid, msg in cap_exceeded_call_ids.items()
        ]
        results = synthetic_results + results
        effective_tool_calls = [
            {
                "id": cid,
                "type": "function",
                "function": {"name": "sub_agent", "arguments": "{}"},
            }
            for cid in cap_exceeded_call_ids
        ] + effective_tool_calls
    if deps.handoff_supervisor is not None:
        try:
            await deps.handoff_supervisor.insert_from_tool_results(
                session_id=request.session_id,
                results=results,
            )
        except Exception:
            logger.warning(
                "handoff_supervisor.insert_failed session_id=%s",
                request.session_id,
                exc_info=True,
            )
    tool_ctx.pending_handoff_dispatch_extras = {}

    return ToolExecutionArtifacts(
        tool_calls=effective_tool_calls,
        results=results,
        compaction_turn=None,
    )


async def postprocess_tool_results(
    *,
    deps: Any,
    tool_ctx: Any,
    execution_artifacts: ToolExecutionArtifacts,
) -> ToolExecutionArtifacts:
    _apply_error_fatigue(
        execution_artifacts.tool_calls,
        execution_artifacts.results,
        tool_ctx,
    )

    turn_state = tool_ctx.turn_state if tool_ctx else None
    compaction_turn = await compact_tool_results_for_turn_async(
        tool_calls=execution_artifacts.tool_calls,
        results=execution_artifacts.results,
        deps=deps,
        frozen_replacement_state=turn_state.replacement_state if turn_state else None,
        working_dir=_session_working_dir(tool_ctx),
    )

    if turn_state and compaction_turn.replacement_state:
        for cid, decision in compaction_turn.replacement_state.latest_by_source_id().items():
            if decision.replacement_text is not None:
                turn_state.replacement_state[cid] = decision.replacement_text

    return ToolExecutionArtifacts(
        tool_calls=execution_artifacts.tool_calls,
        results=[artifact.result for artifact in compaction_turn.artifacts],
        compaction_turn=compaction_turn,
    )


def project_tool_result_entries(
    *,
    tree: Any,
    tool_calls: list[dict[str, Any]],
    results: list[ToolResult],
    compaction_turn: ResultCompactionTurn | None = None,
    current_step_id: str | None = None,
) -> ToolResultPersistenceProjection:
    events: list[ToolCallEnd] = []
    entries: list[Any] = []
    replacement_decisions = (
        compaction_turn.replacement_state.latest_by_source_id()
        if compaction_turn is not None
        else {}
    )
    entry_ids = set(tree.entries.keys())
    parent_id = tree.leaf_id
    for call, result in zip(tool_calls, results, strict=False):
        tool_entry = MessageEntry(
            id=generate_entry_id(entry_ids),
            parent_id=parent_id,
            timestamp=now_iso(),
            role="tool",
            content=tool_result_to_llm_content(result),
            tool_call_id=call.get("id", ""),
            is_error=result.is_error,
            tool_name=(call.get("function") or {}).get("name"),
        )
        entry_ids.add(tool_entry.id)
        entries.append(tool_entry)
        parent_id = tool_entry.id
        handoff_projection = _handoff_request_projection(result)
        if handoff_projection is not None:
            handoff_projection["tool_call_id"] = result.tool_call_id
            handoff_entry = CustomEntry(
                id=generate_entry_id(entry_ids),
                parent_id=tool_entry.id,
                timestamp=now_iso(),
                custom_type=HANDOFF_REQUEST_CUSTOM_TYPE,
                data=handoff_projection,
            )
            entry_ids.add(handoff_entry.id)
            entries.append(handoff_entry)
            parent_id = handoff_entry.id
        reference_artifact = result.reference_artifact
        search_evidence = (
            reference_artifact.search_evidence
            if reference_artifact is not None
            else None
        )
        if isinstance(search_evidence, dict):
            evidence_entry = CustomEntry(
                id=generate_entry_id(entry_ids),
                parent_id=tool_entry.id,
                timestamp=now_iso(),
                custom_type=SEARCH_EVIDENCE_CUSTOM_TYPE,
                data={
                    "tool_call_id": call.get("id", ""),
                    **search_evidence,
                },
            )
            entry_ids.add(evidence_entry.id)
            entries.append(evidence_entry)
            parent_id = evidence_entry.id
        if (
            reference_artifact is not None
            and (
                reference_artifact.extract_result_count is not None
                or reference_artifact.extract_source is not None
                or reference_artifact.extract_cached is not None
            )
        ):
            extract_entry = CustomEntry(
                id=generate_entry_id(entry_ids),
                parent_id=tool_entry.id,
                timestamp=now_iso(),
                custom_type=EXTRACT_EVIDENCE_CUSTOM_TYPE,
                data={
                    "tool_call_id": call.get("id", ""),
                    "extract_result_count": reference_artifact.extract_result_count,
                    "extract_source": reference_artifact.extract_source,
                    "extract_cached": reference_artifact.extract_cached,
                },
            )
            entry_ids.add(extract_entry.id)
            entries.append(extract_entry)
            parent_id = extract_entry.id
        artifact = _tool_result_compaction_artifact(result)
        if artifact is not None:
            if artifact.scope == "tool_result" and not artifact.source_id and result.tool_call_id:
                artifact = artifact.model_copy(
                    update={
                        "source_id": result.tool_call_id,
                        "tool_call_id": result.tool_call_id,
                    }
                )
            reference_entry = CustomEntry(
                id=generate_entry_id(entry_ids),
                parent_id=tool_entry.id,
                timestamp=now_iso(),
                custom_type=RESULT_COMPACTION_REFERENCE_CUSTOM_TYPE,
                data={
                    "session_id": getattr(getattr(tree, "header", None), "id", None),
                    "tool_entry_id": tool_entry.id,
                    **artifact.to_metadata_dict(),
                },
            )
            entry_ids.add(reference_entry.id)
            entries.append(reference_entry)
            parent_id = reference_entry.id
            replacement_decision = replacement_decisions.get(result.tool_call_id or call.get("id", ""))
            if replacement_decision is None:
                replacement_decision = replacement_decision_from_artifact(artifact)
            if replacement_decision is not None:
                existing_state = SessionProjectionEngine(tree).replacement.get_state()
                existing = (
                    existing_state.latest_by_source_id().get(replacement_decision.source_id)
                    if existing_state is not None
                    else None
                )
                if existing is None or existing.stable_payload() != replacement_decision.stable_payload():
                    replacement_entry = CustomEntry(
                        id=generate_entry_id(entry_ids),
                        parent_id=tool_entry.id,
                        timestamp=now_iso(),
                        custom_type=REPLACEMENT_DECISION_CUSTOM_TYPE,
                        data=replacement_decision.model_dump(mode="json"),
                    )
                    entry_ids.add(replacement_entry.id)
                    entries.append(replacement_entry)
                    parent_id = replacement_entry.id
        events.append(ToolCallEnd(tool_call_id=call.get("id", ""), result=result, step_id=current_step_id))
    return ToolResultPersistenceProjection(entries=entries, events=events)


async def persist_projected_tool_result_entries(
    *,
    append_entry: Any,
    projection: ToolResultPersistenceProjection,
) -> list[ToolCallEnd]:
    for entry in projection.entries:
        await append_entry(entry)
    return projection.events


def _apply_error_fatigue(
    tool_calls: list[dict[str, Any]],
    results: list[ToolResult],
    tool_ctx: Any,
) -> None:
    if not tool_ctx.error_fatigue_tracker:
        tool_ctx.error_fatigue_tracker = {}

    fatigue_triggered = False
    tracker = tool_ctx.error_fatigue_tracker
    for call, result in zip(tool_calls, results, strict=False):
        tool_name = ((call or {}).get("function") or {}).get("name", "unknown")
        if result.is_error:
            error_text = "".join(
                block.text for block in result.content if isinstance(block, TextBlock)
            )
            fingerprint = (tool_name, hash(error_text[:100]))
            tracker[fingerprint] = tracker.get(fingerprint, 0) + 1
            if tracker[fingerprint] >= 3 and not fatigue_triggered:
                # V3 Refactoring: Instead of destroying the original error output,
                # we append a gentle Critic Subagent Advisory. This preserves the 
                # model's autonomy and the actual error trace for debugging.
                result.content.append(
                    TextBlock(
                        text=(
                            "\n\n<subagent_advisory type=\"critic\">\n"
                            "观察：我注意到我们已连续 3 次遇到完全相同的错误，"
                            "可能陷入了循环。建议暂停当前方案，"
                            "重新检查项目状态或依赖，并尝试不同的策略。\n"
                            "</subagent_advisory>"
                        )
                    )
                )
                fatigue_triggered = True
        else:
            keys_to_remove = [key for key in tracker.keys() if key[0] == tool_name]
            for key in keys_to_remove:
                del tracker[key]
