from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from prismclaw.engine.trajectory.bus_events import ConversationNodeAdded

from prismclaw.engine.execution.loop._bootstrap_helpers import (
    _node_seq,
    _tool_args_for_call,
    _user_message_count,
)

# 快照写入 / trajectory 发布 / 会话树 append 的可复用实现。
from prismclaw.engine.execution.loop.deps import AgentRunnerDeps
from prismclaw.engine.toolkit.builtin.tool_helpers import _entry_to_llm_dict
from prismclaw.schema.agent.session import MessageEntry, SessionHeader, TurnRuntimeMetricsSnapshot
from prismclaw.schema.agent.task_runtime import ReviewRequirements, TaskRuntimeState
from prismclaw.schema.agent.task_types import TaskSpec
from prismclaw.schema.session.event_logger import SessionEventLogger
from prismclaw.schema.session.types import (
    REVIEW_REQUIREMENTS_CUSTOM_TYPE,
    SESSION_MEMORY_CUSTOM_TYPE,
    TASK_RUNTIME_CUSTOM_TYPE,
    TASK_SPEC_CUSTOM_TYPE,
    TURN_RUNTIME_METRICS_CUSTOM_TYPE,
    CustomEntry,
    SessionTree,
    generate_entry_id,
    now_iso,
)

_event_logger = SessionEventLogger()


def _derive_session_key(session_id: str) -> str:
    """Derive the ``session_key`` grouping prefix from a *session_id*.

    Delegates to the single shared implementation in
    :mod:`prismclaw.schema.session.ids` (SDD context-governance
    W10: the derivation logic previously existed in three diverging copies —
    the memory subsystem's copy only understood the ``:s:`` convention and
    silently isolated every web-chat conversation from the user's durable
    memories).
    """
    from prismclaw.schema.session.ids import derive_memory_session_key

    return derive_memory_session_key(session_id)


async def ensure_session(
    deps: AgentRunnerDeps,
    *,
    session_id: str,
    workspace_id: str,
    agent_id: str,
) -> SessionTree:
    existing = await deps.session_store.load(session_id)
    if existing is not None:
        return existing
    header = SessionHeader(
        id=session_id,
        workspace_id=workspace_id,
        agent_id=agent_id,
        session_key=_derive_session_key(session_id),
    )
    tree = SessionTree(header=header)
    await deps.session_store.save_header(tree)
    return tree




def _publish_conversation_node(
    tree: SessionTree,
    *,
    entry_id: str,
    role: str,
    kind: str,
    content_preview: str | None,
    seq: int | None = None,
    input_detail: str | None = None,
    output_detail: str | None = None,
) -> None:
    """发布 ConversationNodeAdded。Bus 不可用时静默返回，永不抛出。"""
    try:
        from prismclaw.engine.trajectory.bus import current_bus
        from prismclaw.engine.trajectory.bus_events import ConversationNodeAdded

        bus = current_bus()
        if bus is None or bus._is_noop:
            return
        now_ms = int(time.time() * 1000)
        bus.publish(ConversationNodeAdded(
            session_id=tree.header.id,
            entry_id=entry_id,
            seq=seq if seq is not None else _node_seq(tree, entry_id),
            turn=max(1, _user_message_count(tree)),
            role=role,
            kind=kind,
            content_preview=content_preview,
            input_detail=input_detail,
            output_detail=output_detail,
            started_at_ms=now_ms,
            completed_at_ms=now_ms,
        ))
    except Exception:
        pass


def publish_context_node(
    tree: SessionTree,
    *,
    entry_id: str,
    label: str,
    content: str,
) -> None:
    """Publish a kind='context' ConversationNodeAdded.

    context kind 标记"注入模型上下文的非用户消息"（steering/context
    节点）。PrismClaw 对应事件：技能激活注入 —— 技能 prompt 本体进系统提示
    （由 kind='system' cell 逐轮快照覆盖），这里记录的是激活时点 + 注入的
    控制消息，即"模型上下文何时被谁改变"。
    """
    _publish_conversation_node(
        tree,
        entry_id=entry_id,
        role="user",
        kind="context",
        # 无 tree 节点（transient 注入）→ 排在当前末尾，与 compacted 同语义
        seq=len(tree.order),
        content_preview=f"{label}: {content[:500]}" if len(content) > 500 else (f"{label}: {content}" if content else label),
        input_detail=content or None,
    )


def publish_compaction_node(
    tree: SessionTree,
    *,
    entry_id: str,
    summary: str,
) -> None:
    """Publish a kind='compacted' ConversationNodeAdded for a CompactionEntry.

    CompactionEntry is committed directly by the compaction pipeline
    (history_compaction.py autocompact / builtin.py manual /compact) — it
    never flows through _append, so this is the compacted-cell publish
    point.

    NOTE: the compaction path commits the entry to the session store but
    does NOT register it in the in-memory tree.order, so seq falls back to
    len(tree.order) — "appended at the end", which matches the compaction
    position semantics (the cell's unique key is entry_id, not seq).
    """
    _publish_conversation_node(
        tree,
        entry_id=entry_id,
        role="system",
        kind="compacted",
        content_preview=(summary[:500] + "…") if len(summary) > 500 else (summary or None),
        output_detail=summary or None,
    )



# ---------------------------------------------------------------------------
# 会话树写入：append / trajectory 发布
# ---------------------------------------------------------------------------

async def append_to_tree(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    entry: MessageEntry | CustomEntry,
) -> None:
    """纯树操作：append entry + commit_events + context ingest。"""
    if isinstance(entry, MessageEntry):
        events = _event_logger.message_entry(tree.header.id, entry, entry.id)
        tree.header.last_interaction_at = now_iso()
        events.append(_event_logger.request_header(tree.header.id, {"last_interaction_at": tree.header.last_interaction_at}))
        await deps.context_ingestor.ingest(tree.header.id, _entry_to_llm_dict(entry))
    else:
        events = [_event_logger.entry_updated(tree.header.id, entry, leaf_id=entry.id)]
    # Keep the in-memory tree in sync with what we just committed.
    if entry.id not in tree.entries:
        tree.append(entry)
    await deps.session_store.commit_events(events)


def _build_conversation_node(
    tree: SessionTree,
    entry: MessageEntry,
) -> ConversationNodeAdded:
    """纯函数：从 tree + entry 构建 ConversationNodeAdded 事件。"""
    from prismclaw.engine.trajectory.bus_events import ConversationNodeAdded
    _seq = _node_seq(tree, entry.id)
    _user_count = _user_message_count(tree)
    _turn = _user_count if entry.role == "user" else max(1, _user_count)
    _role = entry.role or ""
    _kind = {
        "user": "user",
        "assistant": "message",
        "tool": "tool",
        "system": "system",
    }.get(_role, "system")

    # Iteration, LLM metrics from trajectory runtime ctx
    _iteration: int | None = None
    _metrics_model: str | None = None
    _metrics_finish: str | None = None
    _metrics_usage: dict[str, Any] = {}
    _metrics_ttft: float | None = None
    _metrics_decoding: float | None = None
    _started_ms: int
    _completed_ms: int
    try:
        from prismclaw.engine.trajectory.bus_context import current_runtime
        _rt = current_runtime()
    except Exception:
        _rt = None
    _now_ms = int(time.time() * 1000)
    if _rt is not None and _rt.session_id == tree.header.id:
        if _role in ("assistant", "tool"):
            _iteration = _rt.iteration
        if _role == "assistant" and _rt.llm_metrics is not None:
            _m = _rt.llm_metrics
            _metrics_model = _m.model
            _metrics_finish = _m.finish_reason
            _metrics_usage = {
                "usage_input_tokens": _m.usage_input_tokens,
                "usage_output_tokens": _m.usage_output_tokens,
                "usage_cache_read_tokens": _m.usage_cache_read_tokens,
                "usage_cache_write_tokens": _m.usage_cache_write_tokens,
                "usage_reasoning_tokens": _m.usage_reasoning_tokens,
            }
            _metrics_ttft = _m.ttft_ms
            _metrics_decoding = _m.decoding_ms
            _started_ms = _m.started_at_ms or _now_ms
            _completed_ms = _m.completed_at_ms or _now_ms
        elif _role == "tool" and entry.tool_call_id:
            _started_ms = _rt.tool_call_starts.pop(entry.tool_call_id, _now_ms)
            _completed_ms = _now_ms
        else:
            _started_ms = _now_ms
            _completed_ms = _now_ms
    else:
        _started_ms = _now_ms
        _completed_ms = _now_ms

    # content_preview (up to 500 chars) + full *_detail fields
    _content_preview: str | None = None
    _full_text: str | None = None
    _full_thinking: str | None = None
    _content = entry.content
    if isinstance(_content, str):
        _full_text = _content if _content else None
        _content_preview = _content[:500]
        if len(_content) > 500:
            _content_preview = _content_preview + "…"
    elif isinstance(_content, list):
        _parts: list[str] = []
        _thinking_parts: list[str] = []
        for _b in _content:
            try:
                if isinstance(_b, str):
                    _parts.append(_b)
                    continue
                _btype = getattr(_b, "type", "") or ""
                _t = getattr(_b, "text", None) or ""
                if not isinstance(_t, str):
                    continue
                if _btype in ("thinking", "reasoning"):
                    _thinking_parts.append(_t)
                else:
                    _parts.append(_t)
            except Exception:
                continue
        _joined = "".join(_parts)
        if _joined:
            _full_text = _joined
            _content_preview = _joined[:500]
            if len(_joined) > 500:
                _content_preview = _content_preview + "…"
        if _thinking_parts:
            _full_thinking = "".join(_thinking_parts) or None

    # role-driven *_detail assignment
    _input_detail: str | None = None
    _output_detail: str | None = None
    _thinking_detail: str | None = None
    _is_error: bool = entry.is_error
    if _role == "user":
        _input_detail = _full_text
    elif _role == "assistant":
        _output_detail = _full_text
        _thinking_detail = _full_thinking
        if _thinking_detail is None and _rt is not None:
            _thinking_detail = _rt.assistant_reasoning
    elif _role == "tool":
        _input_detail = (
            _tool_args_for_call(tree, entry.tool_call_id)
            or entry.tool_call_id
        )
        _output_detail = _full_text
    elif _role == "system":
        _input_detail = _full_text

    # tool_calls_summary (assistant only)
    _tc_summary: tuple[str, ...] = ()
    _tcs = entry.tool_calls
    if _tcs:
        import json as _tc_json
        _summaries: list[str] = []
        for _tc in _tcs:
            try:
                _tc_dict = _tc if isinstance(_tc, dict) else _tc.model_dump()
                _tc_name = str(_tc_dict.get("name") or _tc_dict.get("function", {}).get("name") or "?")
                _tc_args = _tc_dict.get("arguments") or _tc_dict.get("function", {}).get("arguments") or {}
                if isinstance(_tc_args, str):
                    _tc_args_str = _tc_args
                else:
                    _tc_args_str = _tc_json.dumps(_tc_args, ensure_ascii=False, default=str)
                _summaries.append(f"{_tc_name}({_tc_args_str[:200]})")
            except Exception:
                _summaries.append("?")
        _tc_summary = tuple(_summaries)

    return ConversationNodeAdded(
        session_id=tree.header.id,
        entry_id=entry.id,
        seq=_seq,
        turn=_turn,
        role=_role,
        kind=_kind,
        content_preview=_content_preview,
        tool_calls_summary=_tc_summary,
        tool_call_id=entry.tool_call_id,
        input_detail=_input_detail,
        output_detail=_output_detail,
        thinking_detail=_thinking_detail,
        is_error=_is_error,
        schema_detail=None,
        started_at_ms=_started_ms,
        completed_at_ms=_completed_ms,
        iteration=_iteration,
        error=None,
        model=_metrics_model,
        finish_reason=_metrics_finish,
        usage_input_tokens=_metrics_usage.get("usage_input_tokens"),
        usage_output_tokens=_metrics_usage.get("usage_output_tokens"),
        usage_cache_read_tokens=_metrics_usage.get("usage_cache_read_tokens"),
        usage_cache_write_tokens=_metrics_usage.get("usage_cache_write_tokens"),
        usage_reasoning_tokens=_metrics_usage.get("usage_reasoning_tokens"),
        ttft_ms=_metrics_ttft,
        decoding_ms=_metrics_decoding,
        tool_name=entry.tool_name,
    )


async def publish_trajectory_node(
    tree: SessionTree,
    entry: MessageEntry,
) -> None:
    """ConversationNodeAdded 发布——独立于树操作，best-effort。"""
    try:
        import logging as _pub_log

        from prismclaw.engine.trajectory.bus import current_bus
        _pub_logger = _pub_log.getLogger(__name__)

        _bus = current_bus()
        if _bus is not None and not _bus._is_noop:
            _bus.publish(_build_conversation_node(tree, entry))
    except Exception as _pub_exc:
        _pub_logger.warning(
            "publish_trajectory_node FAILED: entry_id=%s role=%s exc=%s",
            getattr(entry, "id", "?"), getattr(entry, "role", "?"), _pub_exc,
        )


async def _append(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    entry: MessageEntry | CustomEntry,
) -> None:
    """组合入口：append_to_tree + publish_trajectory_node。"""
    await append_to_tree(deps, tree, entry)

    if isinstance(entry, MessageEntry):
        await publish_trajectory_node(tree, entry)


async def _persist_custom_snapshot(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    *,
    custom_type: str,
    data: dict[str, Any],
    current: Any = None,
) -> None:
    """写入一条 CustomEntry 快照；current 与 data 相同时跳过。

    统一走 append_to_tree，保证内存树与持久化一致，使 get_latest_* 去重生效。
    """
    if current is not None and current == data:
        return
    entry = CustomEntry(
        id=generate_entry_id(set(tree.entries.keys())),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        custom_type=custom_type,
        data=data,
    )
    await append_to_tree(deps, tree, entry)


def _dump_model(value: Any) -> dict[str, Any] | None:
    return value.model_dump(mode="json") if value is not None else None


async def _persist_turn_runtime_metrics(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    *,
    request_mode: str | None,
    dynamic_payload: int,
    input_tokens: int,
    cache_read_tokens: int,
    result_payload_reduction: int,
) -> None:
    snapshot = TurnRuntimeMetricsSnapshot(
        request_mode=request_mode or "",
        dynamic_payload=max(0, dynamic_payload),
        input_tokens=max(0, input_tokens),
        cache_read_tokens=max(0, cache_read_tokens),
        result_payload_reduction=max(0, result_payload_reduction),
    )
    await _persist_custom_snapshot(
        deps,
        tree,
        custom_type=TURN_RUNTIME_METRICS_CUSTOM_TYPE,
        data=dict(snapshot),
    )


async def _persist_session_memory_snapshot(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    *,
    session_id: str,
) -> None:
    getter = getattr(deps.context_engine, "get_session_state", None)
    if getter is None:
        return
    state = getter(session_id)
    await _persist_custom_snapshot(
        deps,
        tree,
        custom_type=SESSION_MEMORY_CUSTOM_TYPE,
        data=state.model_dump(mode="json"),
        current=_dump_model(tree.get_latest_session_memory_state()),
    )





async def _persist_task_runtime_snapshot(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    *,
    task_runtime: TaskRuntimeState,
) -> None:
    await _persist_custom_snapshot(
        deps,
        tree,
        custom_type=TASK_RUNTIME_CUSTOM_TYPE,
        data=task_runtime.model_dump(mode="json"),
        current=_dump_model(tree.get_latest_task_runtime()),
    )


async def _persist_task_spec_snapshot(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    *,
    task_spec: TaskSpec,
) -> None:
    await _persist_custom_snapshot(
        deps,
        tree,
        custom_type=TASK_SPEC_CUSTOM_TYPE,
        data=task_spec.model_dump(mode="json"),
        current=_dump_model(tree.get_latest_task_spec()),
    )


async def _persist_review_requirements_snapshot(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    *,
    review_requirements: ReviewRequirements,
) -> None:
    await _persist_custom_snapshot(
        deps,
        tree,
        custom_type=REVIEW_REQUIREMENTS_CUSTOM_TYPE,
        data=review_requirements.model_dump(mode="json"),
        current=_dump_model(tree.get_latest_review_requirements()),
    )
