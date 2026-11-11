from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, is_dataclass
from typing import Any

from prismclaw.engine.execution.query.state import (
    QueryLoopState,
    QueryLoopTracking,
    QueryRuntimeContext,
    QueryView,
    QueryViewDiagnostics,
)
from prismclaw.engine.context.projection_models import ProjectionMessageSet
from prismclaw.schema.agent.session import SystemReminderProjection

_UNSET = object()


def build_query_loop_state(
    *,
    source_messages: ProjectionMessageSet,
    messages_for_query: ProjectionMessageSet | None = None,
    runtime_context: QueryRuntimeContext | None = None,
    tool_loop_state: Any | None = None,
    compression_state: Any | None = None,
    attachment_state: Any | None = None,
) -> QueryLoopState:
    source_snapshot = _clone_message_set(source_messages)
    query_snapshot = _clone_message_set(messages_for_query or source_messages)

    source_digest = _stable_digest(_message_set_payload(source_snapshot))
    messages_digest = _stable_digest(_message_set_payload(query_snapshot))
    revision_id = _stable_digest(
        {
            "source_digest": source_digest,
            "messages_digest": messages_digest,
        }
    )

    diagnostics = QueryViewDiagnostics(
        source_digest=source_digest,
        messages_digest=messages_digest,
        source_message_count=len(source_snapshot.messages),
        query_message_count=len(query_snapshot.messages),
    )
    query_view = QueryView(
        source_messages=source_snapshot,
        messages_for_query=query_snapshot,
        compression_state=compression_state,
        attachment_state=attachment_state,
        revision_id=revision_id,
        diagnostics=diagnostics,
    )
    tracking = QueryLoopTracking(tool_loop_state=tool_loop_state)
    runtime = runtime_context or QueryRuntimeContext()
    loop_revision_id = _stable_digest(
        {
            "query_view_revision_id": revision_id,
            "query_source": runtime.query_source,
        }
    )
    return QueryLoopState(
        query_view=query_view,
        runtime_context=runtime,
        tracking=tracking,
        loop_revision_id=loop_revision_id,
    )


def rebuild_query_loop_state(
    query_loop_state: QueryLoopState,
    *,
    source_messages: ProjectionMessageSet | object = _UNSET,
    messages_for_query: ProjectionMessageSet | object = _UNSET,
    runtime_context: QueryRuntimeContext | object = _UNSET,
    tool_loop_state: Any | object = _UNSET,
    compression_state: Any = _UNSET,
    attachment_state: Any = _UNSET,
) -> QueryLoopState:
    effective_source_messages = (
        query_loop_state.query_view.source_messages
        if source_messages is _UNSET
        else source_messages
    )
    effective_messages_for_query = (
        query_loop_state.query_view.messages_for_query
        if messages_for_query is _UNSET
        else messages_for_query
    )
    effective_runtime_context = (
        query_loop_state.runtime_context
        if runtime_context is _UNSET
        else runtime_context
    )
    effective_tool_loop_state = (
        query_loop_state.tracking.tool_loop_state
        if tool_loop_state is _UNSET
        else tool_loop_state
    )
    effective_compression_state = (
        query_loop_state.query_view.compression_state
        if compression_state is _UNSET
        else compression_state
    )
    effective_attachment_state = (
        query_loop_state.query_view.attachment_state
        if attachment_state is _UNSET
        else attachment_state
    )
    return build_query_loop_state(
        source_messages=effective_source_messages,
        messages_for_query=effective_messages_for_query,
        runtime_context=effective_runtime_context,
        tool_loop_state=effective_tool_loop_state,
        compression_state=effective_compression_state,
        attachment_state=effective_attachment_state,
    )


def _clone_message_set(message_set: ProjectionMessageSet) -> ProjectionMessageSet:
    return ProjectionMessageSet(
        messages=copy.deepcopy(message_set.messages),
        meta_messages=copy.deepcopy(message_set.meta_messages),
        appended_messages=copy.deepcopy(message_set.appended_messages),
    )


def _message_set_payload(message_set: ProjectionMessageSet) -> dict[str, Any]:
    return {
        "messages": copy.deepcopy(message_set.messages),
        "meta_messages": copy.deepcopy(message_set.meta_messages),
        "appended_messages": copy.deepcopy(message_set.appended_messages),
    }


def _stable_digest(payload: Any) -> str:
    normalized = json.dumps(
        _normalize_digest_payload(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalize_digest_payload(payload: Any) -> Any:
    if payload is None or isinstance(payload, str | int | float | bool):
        return payload
    if isinstance(payload, dict):
        return {
            str(key): _normalize_digest_payload(value)
            for key, value in sorted(payload.items(), key=lambda item: str(item[0]))
        }
    if isinstance(payload, list | tuple):
        return [_normalize_digest_payload(item) for item in payload]
    if isinstance(payload, set | frozenset):
        normalized_items = [_normalize_digest_payload(item) for item in payload]
        return sorted(
            normalized_items,
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=True),
        )
    if isinstance(payload, SystemReminderProjection):
        return {
            "__projection_type__": "system_reminder",
            **asdict(payload),
        }
    model_dump = getattr(payload, "model_dump", None)
    if callable(model_dump):
        return _normalize_digest_payload(model_dump(mode="python"))
    if is_dataclass(payload):
        return _normalize_digest_payload(asdict(payload))
    if hasattr(payload, "__dict__"):
        return {
            "__object_type__": payload.__class__.__qualname__,
            "attrs": _normalize_digest_payload(vars(payload)),
        }
    raise TypeError(f"Unsupported query runtime digest payload: {type(payload)!r}")
