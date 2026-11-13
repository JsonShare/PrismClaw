from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import time
from pathlib import Path
from typing import Any

from prismclaw.app.web.chat.chat_protocol import (
    ConversationWebRuntimeProjection,
    SessionQueue,
    _get_session_queue,
)
from prismclaw.app.web.protocol.messages import (
    SERVER_RUN_SNAPSHOT,
    SERVER_TASK_RUNTIME,
    SERVER_TASK_TIMELINE,
)
from prismclaw.app.web.connection import ConnectionState, send_event
from prismclaw.schema.agent.types import AgentDeps
from prismclaw.schema.agent import DecisionLedgerEvent
from prismclaw.runtime.facade.trajectory import (
    TaskExecutionProjector,
    TimelinePatch,
)
from prismclaw.schema.agent.task_runtime import ProbeSignal, RecoveryProbeResult
from prismclaw.schema.session.types import (
    CustomEntry,
    RUN_PROJECTION_CUSTOM_TYPE,
    RunProjectionPayload,
    generate_entry_id,
    now_iso,
)
from prismclaw.schema.agent.session import MessageEntry
from prismclaw.schema.session.event_logger import SessionEventLogger
from prismclaw.persistence.session.redis import SessionLockError

logger = logging.getLogger(__name__)
_event_logger = SessionEventLogger()

_REASONING_PUSH_INTERVAL_S = 0.12

_EXECUTION_TIMELINE_ENTRY_TYPE = "execution_timeline"

# 软着陆错误码：错误帧本身没有额外信息量（纯超时 / 预算耗尽），
# 省略 error 帧；正文本身就是连贯答案，不追加任何系统说明。
_SOFT_LANDING_CODES = frozenset({"timeout", "budget_seconds", "budget_iterations"})


def _extract_tool_result_text(event: Any) -> str:
    if not event or not getattr(event, "result", None):
        return ""
    result = event.result
    content = result.content if hasattr(result, "content") else None
    if not content:
        return ""
    parts: list[str] = []
    for block in content:
        t = getattr(block, "text", None)
        if t is not None:
            parts.append(t)
        elif isinstance(block, dict) and "text" in block:
            parts.append(block["text"])
    return "\n".join(parts)


def _sanitize_workspace_id(workspace_id: str | None) -> str:
    from prismclaw.app.web.workspace_ids import clean_workspace_id
    return clean_workspace_id(workspace_id)


def _resolve_tool_workspace_path(
    workspace_base: Path,
    *,
    user_id: str,
    workspace_id: str,
) -> Path:
    user_root = workspace_base / f"web_{user_id}"
    user_root.mkdir(parents=True, exist_ok=True)
    base = (user_root / "default") if workspace_id == "default" else (user_root / workspace_id)
    base.mkdir(parents=True, exist_ok=True)
    from prismclaw.app.web._routes_helpers import ensure_managed_workspace_dirs

    ensure_managed_workspace_dirs(base)
    return base.resolve()


def _call_sync_tree_accessor(tree: Any, name: str) -> Any | None:
    accessor = getattr(tree, name, None)
    if not callable(accessor):
        return None
    if inspect.iscoroutinefunction(accessor):
        return None
    try:
        value = accessor()
    except Exception:
        logger.warning("web.task_runtime.accessor_failed: %s", name, exc_info=True)
        return None
    if inspect.isawaitable(value):
        if inspect.iscoroutine(value):
            value.close()
        return None
    return value


def _model_dump_if_supported(value: Any) -> dict[str, Any] | None:
    if value is None or not hasattr(value, "model_dump"):
        return None
    try:
        payload = value.model_dump(mode="json")
    except Exception:
        logger.warning("web.task_runtime.model_dump_failed", exc_info=True)
        return None
    return payload if isinstance(payload, dict) else None


def _serialize_runtime_subagent_items(
    projection: ConversationWebRuntimeProjection | None,
) -> list[dict[str, Any]]:
    if projection is None:
        return []
    return list(projection.subagent_items.values())


def _extract_latest_recovery_probe_payload(tree: Any) -> dict[str, Any] | None:
    decision_events = _call_sync_tree_accessor(tree, "get_decision_ledger_events")
    if not isinstance(decision_events, list):
        return None
    for event in reversed(decision_events):
        if not isinstance(event, DecisionLedgerEvent):
            continue
        if event.event_type != "recovery_probe_completed":
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        raw_signals = payload.get("signals")
        signals: list[ProbeSignal] = []
        if isinstance(raw_signals, list):
            for signal in raw_signals:
                if not isinstance(signal, dict):
                    continue
                try:
                    signals.append(ProbeSignal.model_validate(signal))
                except Exception:
                    logger.warning("web.task_runtime.recovery_probe_signal_invalid", exc_info=True)
        checked_paths = payload.get("checked_paths")
        missing_paths = payload.get("missing_paths")
        try:
            result = RecoveryProbeResult(
                probe_id=str(payload.get("request_id") or payload.get("probe_id") or event.event_id),
                workspace_root=(
                    str(payload.get("workspace_root"))
                    if isinstance(payload.get("workspace_root"), str)
                    else None
                ),
                baseline_source=str(payload.get("baseline_source") or "none"),
                status=str(payload.get("status") or "ready"),
                checked_paths=[str(item) for item in checked_paths] if isinstance(checked_paths, list) else [],
                missing_paths=[str(item) for item in missing_paths] if isinstance(missing_paths, list) else [],
                signals=signals,
                summary=event.summary,
            )
        except Exception:
            logger.warning("web.task_runtime.recovery_probe_invalid", exc_info=True)
            return None
        return _model_dump_if_supported(result)
    return None


def _build_task_runtime_projection_payload(
    tree: Any,
    *,
    web_runtime: ConversationWebRuntimeProjection | None = None,
) -> dict[str, Any] | None:
    task_runtime = _call_sync_tree_accessor(tree, "get_latest_task_runtime")
    task_payload: dict[str, Any] | None = None
    if task_runtime is not None:
        task_payload = _model_dump_if_supported(task_runtime)

    latest_review = _call_sync_tree_accessor(tree, "get_latest_review_decision")
    task_spec = _call_sync_tree_accessor(tree, "get_latest_task_spec")
    evidence_items = _call_sync_tree_accessor(tree, "get_evidence_ledger")
    if not isinstance(evidence_items, list):
        evidence_items = []
    recent_evidence = evidence_items[-6:]
    recent_evidence_payload = [
        payload
        for evidence in recent_evidence
        if (payload := _model_dump_if_supported(evidence)) is not None
    ]
    preflight_probe = getattr(task_spec, "preflight_probe", None) if task_spec is not None else None
    recovery_probe = _extract_latest_recovery_probe_payload(tree)

    # Build runtime-derived fields even when task_runtime is not yet set.
    # This ensures tool_calls / subagent_trace are streamed to the frontend
    # in real time, independent of the TaskRuntimeState lifecycle.
    tool_calls = web_runtime.tool_calls if web_runtime is not None else []
    subagent_trace = _serialize_runtime_subagent_items(web_runtime)
    loading_active = web_runtime.loading_active if web_runtime is not None else False
    approval_pending_tool_call_ids = sorted(
        web_runtime.approval_pending_tool_call_ids if web_runtime is not None else []
    )

    # Short-circuit: if nothing meaningful to send, return None to avoid
    # unnecessary websocket traffic.
    # But still send when loading_active is True to keep frontend state in sync
    # (ensures activeTaskRuntime is initialized on the frontend side).
    if (
        task_payload is None
        and not tool_calls
        and not subagent_trace
        and not loading_active
        and not approval_pending_tool_call_ids
    ):
        return None

    return {
        "task": task_payload,
        "latest_review": _model_dump_if_supported(latest_review),
        "recent_evidence": recent_evidence_payload,
        "preflight_probe": _model_dump_if_supported(preflight_probe),
        "recovery_probe": recovery_probe,
        "loading_active": loading_active,
        "subagent_trace": subagent_trace,
        "approval_pending_tool_call_ids": approval_pending_tool_call_ids,
        "tool_calls": tool_calls,
    }


def _build_task_runtime_idempotency_key(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# Tree 缓存：同一 turn 中多次调用 _push_task_runtime_event 时，
# 避免重复 load 同一棵 tree。TTL 短（200ms），仅在同一 iteration 内复用。
_TREE_CACHE_TTL = 0.2
_tree_cache: dict[str, tuple[float, Any]] = {}


def _get_cached_tree(session_id: str) -> Any | None:
    entry = _tree_cache.get(session_id)
    if entry is not None and (time.time() - entry[0]) < _TREE_CACHE_TTL:
        return entry[1]
    return None


def _set_cached_tree(session_id: str, tree: Any) -> None:
    _tree_cache[session_id] = (time.time(), tree)


def invalidate_tree_cache(session_id: str) -> None:
    _tree_cache.pop(session_id, None)


async def _push_task_runtime_event(
    state: ConnectionState,
    *,
    deps: AgentDeps,
    conversation_id: str,
    session_id: str,
) -> None:
    tree = _get_cached_tree(session_id)
    if tree is None:
        try:
            tree = await deps.session_store.load(session_id)
        except Exception:
            logger.warning("web.task_runtime.load_failed", exc_info=True)
            return
        if tree is None:
            return
        _set_cached_tree(session_id, tree)
    session_queue = _get_session_queue(state)
    web_runtime = session_queue.get_or_create_web_runtime_projection(conversation_id)
    try:
        payload = _build_task_runtime_projection_payload(tree, web_runtime=web_runtime)
    except Exception:
        logger.warning("web.task_runtime.projection_failed", exc_info=True)
        return
    if payload is None:
        return
    event_seq = web_runtime.next_event_seq()
    await send_event(
        state,
        SERVER_TASK_RUNTIME,
        conversation_id,
        {
            "event_seq": event_seq,
            "idempotency_key": _build_task_runtime_idempotency_key(payload),
            "projection": payload,
        },
    )
    if web_runtime.timeline_snapshot is not None:
        snapshot_nodes = web_runtime.timeline_snapshot.get("nodes")
        if snapshot_nodes:
            snap_seq = web_runtime.next_event_seq()
            await send_event(
                state,
                SERVER_TASK_TIMELINE,
                conversation_id,
                {
                    "conversationId": conversation_id,
                    "eventSeq": snap_seq,
                    "nodes": snapshot_nodes,
                    "messageId": web_runtime.timeline_snapshot.get("messageId"),
                },
            )


async def _push_timeline_event(
    state: ConnectionState,
    *,
    conversation_id: str,
    session_id: str,
    snapshot: dict[str, Any] | None = None,
    patch: TimelinePatch | list[TimelinePatch] | None = None,
) -> None:
    session_queue = _get_session_queue(state)
    web_runtime = session_queue.get_or_create_web_runtime_projection(conversation_id)
    event_seq = web_runtime.next_event_seq()
    frame: dict[str, Any] = {"conversationId": conversation_id, "eventSeq": event_seq}
    if snapshot is not None:
        snapshot_nodes = snapshot.get("nodes")
        if snapshot_nodes:
            frame["nodes"] = snapshot_nodes
        frame["messageId"] = snapshot.get("messageId")
    if patch is not None:
        patches = patch if isinstance(patch, list) else [patch]
        frame["patch"] = [p.model_dump(by_alias=True) for p in patches]
    await send_event(state, SERVER_TASK_TIMELINE, conversation_id, frame)


def _build_execution_timeline_payload(
    projector: TaskExecutionProjector,
    message_id: str,
) -> dict[str, Any]:
    """构建执行过程终态的持久化 payload：**完整保存，不做任何截断/降级**。

    持久化是历史回放的唯一可信源（刷新页面后的 timeline 由此恢复），
    执行过程（reasoning 全文、工具参数与输出明细）一旦截断
    即永久丢失。帧大小防护只允许存在于实时 wire 路径，落库路径保证不丢内容。
    """
    return {
        "message_id": message_id,
        "version": 1,
        "nodes": [n.model_dump(by_alias=True) for n in projector.to_nodes()],
    }


async def _persist_execution_timeline(
    deps: AgentDeps,
    *,
    session_id: str,
    message_id: str,
    projector: TaskExecutionProjector,
) -> None:
    if not message_id or not projector.has_content():
        return
    payload = _build_execution_timeline_payload(projector, message_id)
    try:
        tree = await deps.session_store.load(session_id)
    except Exception:
        logger.warning("web.timeline.persist.load_failed", exc_info=True)
        return
    if tree is None:
        return
    if message_id not in tree.entries:
        return
    entry = CustomEntry(
        id=generate_entry_id(tree.all_entry_ids()),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        custom_type=_EXECUTION_TIMELINE_ENTRY_TYPE,
        data=payload,
    )
    try:
        event = _event_logger.entry_updated(session_id, entry, leaf_id=entry.id)
        await deps.session_store.commit_events([event])
        invalidate_tree_cache(session_id)
    except Exception:
        logger.warning("web.timeline.persist.append_failed", exc_info=True)


async def _persist_partial_assistant_message(
    deps: AgentDeps,
    *,
    session_id: str,
    projector: TaskExecutionProjector,
    transcript_text: str,
) -> str | None:
    """异常终止 run 的兜底落库：合成 partial assistant 消息并返回其 entry id。

    正常 Done 路径的 assistant 消息由 agent 骨架落树（assistant_message_id
    非空）；但异常终止（ErrorEvent / runner 崩溃）时骨架不落终态消息，
    assistant_message_id 恒为 None → _persist_execution_timeline 因
    「message_id 不在 tree.entries」被跳过，执行过程（reasoning + 工具
    调用明细）刷新后永久丢失。本函数按系统既有存储逻辑（MessageEntry +
    entry_updated + commit_events，与 _persist_execution_timeline 同构）
    补一条 partial 消息作为 timeline 挂靠点：

      - content = 已流式推送的转录文本（可能为空，空文本仍承载 timeline）；
      - partial=True 标记中断语义，_build_display_messages 的可见性过滤
        （has_visible_content）会保留它——正文为空时也必须有这条消息，
        否则 timeline 挂在无人引用的 message_id 上，历史回放永不显示。

    幂等性：同一 run 的异常路径只调用一次（ErrorEvent 分支 / except 分支
    互斥），无重入风险。返回 None = 落库失败（树加载/提交异常），调用方
    应跳过 timeline 持久化而不是崩溃。
    """
    if not projector.has_content() and not (transcript_text or "").strip():
        return None
    try:
        tree = await deps.session_store.load(session_id)
    except Exception:
        logger.warning("web.partial_message.persist.load_failed", exc_info=True)
        return None
    if tree is None:
        return None
    entry = MessageEntry(
        id=generate_entry_id(tree.all_entry_ids()),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        role="assistant",
        content=(transcript_text or "").strip(),
        partial=True,
    )
    try:
        event = _event_logger.entry_updated(session_id, entry, leaf_id=entry.id)
        await deps.session_store.commit_events([event])
        invalidate_tree_cache(session_id)
        return entry.id
    except Exception:
        logger.warning("web.partial_message.persist.append_failed", exc_info=True)
        return None


def _build_run_projection_payload(
    tree: Any,
    session_queue: Any,
    conversation_id: str,
) -> RunProjectionPayload | None:
    """从内存投影 + 树构建 RunProjection payload（纯构造：不落库、不推送）。

    供 GET /run（内存直读）、周期落盘（_persist_run_projection）与 WS 周期
    快照（_push_run_snapshot）共用，保证三条路径 payload 口径一致。投影不
    存在（run 已结束 / 未开始）时返回 None。
    """
    projection = session_queue.get_web_runtime_projection(conversation_id)
    if projection is None:
        return None
    phase, _ = session_queue.get_phase()
    phase_at_ms = session_queue.get_phase_epoch_ms() if phase is not None else None
    task_runtime = _build_task_runtime_projection_payload(tree, web_runtime=projection)
    timeline_nodes = (
        projection.timeline_snapshot.get("nodes")
        if projection.timeline_snapshot
        else []
    )
    if not isinstance(timeline_nodes, list):
        timeline_nodes = []
    return RunProjectionPayload(
        conversation_id=conversation_id,
        state="active",
        streaming_text=projection.streaming_text,
        timeline_nodes=timeline_nodes,
        task_runtime=task_runtime if task_runtime is not None else {},
        phase=phase,
        phase_at=phase_at_ms,
        clarification=projection.pending_clarification,
        updated_at=int(time.time() * 1000),
    )


async def _persist_run_projection(
    deps: AgentDeps,
    *,
    session_id: str,
    conversation_id: str,
    session_queue: Any,
) -> None:
    """把后端运行态投影周期落盘为 run_projection custom entry（审计留档）。

    每次 flush 写一条全新 custom entry，供崩溃后离线取证（内存权威源随
    进程消失，树里 ≤5s 滞后的快照是唯一记录）。用 sha1 签名去重写：
    payload 与上一次写入相同则跳过，避免空闲期无谓落库。
    """
    tree = _get_cached_tree(session_id)
    if tree is None:
        try:
            tree = await deps.session_store.load(session_id)
        except Exception:
            logger.warning("web.run_projection.load_failed", exc_info=True)
            return
        if tree is None:
            return
        _set_cached_tree(session_id, tree)

    payload = _build_run_projection_payload(
        tree, session_queue, conversation_id
    )
    if payload is None:
        return
    projection = session_queue.get_web_runtime_projection(conversation_id)

    data = payload.model_dump(mode="json")
    sig = hashlib.sha1(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if projection is not None and getattr(projection, "_run_projection_sig", "") == sig:
        return
    if projection is not None:
        projection._run_projection_sig = sig

    entry = CustomEntry(
        id=generate_entry_id(tree.all_entry_ids()),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        custom_type=RUN_PROJECTION_CUSTOM_TYPE,
        data=data,
    )
    try:
        event = _event_logger.entry_updated(session_id, entry, leaf_id=entry.id)
        await deps.session_store.commit_events([event])
        invalidate_tree_cache(session_id)
    except SessionLockError:
        # commit_events 已带短退避重试；重试仍失败 = 该 session 正被其他
        # 写方（跨 worker / 长时间写）持锁。run_projection 落盘是「尽力而为」
        # 的可丢持久化（下一周期会再写），静默跳过即可，
        # 无需告警堆栈、更不该干扰主循环。
        logger.debug("web.run_projection.commit_locked_skip session=%s", session_id)
    except Exception:
        logger.warning("web.run_projection.commit_failed", exc_info=True)


async def _push_run_snapshot(
    state: ConnectionState,
    deps: AgentDeps,
    *,
    session_id: str,
    conversation_id: str,
    session_queue: Any,
) -> None:
    """周期向活跃连接下发一帧全量 run.snapshot（P3）。

    前端据此整体替换 streaming 运行态（hydrateRun），取代脆弱的序号增量同步。
    复用 _build_run_projection_payload，保证与持久化 / GET /run 口径一致。
    """
    try:
        tree = _get_cached_tree(session_id)
        if tree is None:
            tree = await deps.session_store.load(session_id)
            if tree is None:
                return
            _set_cached_tree(session_id, tree)
        payload = _build_run_projection_payload(tree, session_queue, conversation_id)
        if payload is None:
            return
        await send_event(
            state,
            SERVER_RUN_SNAPSHOT,
            conversation_id,
            payload.model_dump(mode="json"),
        )
    except Exception:
        logger.warning("web.run_snapshot.failed", exc_info=True)


def _spawn_background(
    coro: Any,
    *,
    label: str,
    session_queue: SessionQueue | None = None,
) -> None:
    try:
        task = asyncio.create_task(coro)
    except Exception:
        logger.warning("web.parallel_task.spawn_failed: %s", label, exc_info=True)
        return

    def _done(t: asyncio.Task) -> None:
        try:
            t.result()
        except Exception:
            logger.warning("web.parallel_task.failed: %s", label, exc_info=True)

    try:
        task.add_done_callback(_done)
    except Exception:
        pass
    if session_queue is not None:
        try:
            session_queue._track_detached(task)
        except Exception:
            pass


def _get_runner_deps(state: ConnectionState) -> AgentDeps:
    return state.ws.app.state.web_deps.runner_deps


async def _get_workspace_runner_deps(
    state: ConnectionState,
    *,
    workspace_id: str,
    wait_for_ready: bool = False,
) -> AgentDeps:
    app = state.ws.app
    web_deps = app.state.web_deps
    workspace_base: Path = web_deps.workspace_base
    workspace_root = _resolve_tool_workspace_path(
        workspace_base,
        user_id=str(state.user_id),
        workspace_id=workspace_id,
    )
    started_at = time.monotonic()
    get_ws_deps_fn = web_deps.workspace_runner_deps_fn
    get_ws_deps_state_fn = web_deps.workspace_runner_deps_state_fn
    scoped = await get_ws_deps_fn(
        app,
        user_id=str(state.user_id),
        workspace_root=workspace_root,
        wait_for_ready=wait_for_ready,
    ) if get_ws_deps_fn is not None else None
    cache_state = get_ws_deps_state_fn(
        app,
        user_id=str(state.user_id),
        workspace_root=workspace_root,
    ) if get_ws_deps_state_fn is not None else "unknown"
    hotpath = getattr(web_deps, "hotpath_telemetry", None)
    if scoped is None:
        if hotpath is not None:
            hotpath.observe(
                "workspace_runner_deps.resolve_ms",
                (time.monotonic() - started_at) * 1000.0,
                labels={"state": cache_state, "wait": wait_for_ready},
            )
        return web_deps.runner_deps
    if hotpath is not None:
        hotpath.observe(
            "workspace_runner_deps.resolve_ms",
            (time.monotonic() - started_at) * 1000.0,
            labels={"state": cache_state, "wait": wait_for_ready},
        )
    return scoped
