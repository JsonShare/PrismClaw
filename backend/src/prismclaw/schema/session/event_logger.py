"""SessionEventLogger — Event factory for Session Event Sourcing.

Constructs SessionEvents with payloads aligned with 会话事件映射.
Pure factory: builds events, never writes.

Turn/step lifecycle events are published via SessionBus (engine/trajectory),
not through this logger. This factory covers message/tool/compaction events
that need session_store persistence.
"""

from __future__ import annotations

import logging
from typing import Any

from prismclaw.schema.session import (
    MessageEntry,
    SessionEntry,
    SessionEvent,
    SessionEventType,
    _event_to_message_entry,
    _parse_entry,
    now_iso,
    now_ms_epoch,
)

logger = logging.getLogger(__name__)


def _mk(
    *,
    seq: int = 0,
    type: SessionEventType,
    session_id: str,
    data: dict[str, Any],
    ignorable: bool = False,
    source_event_seqs: list[int] | None = None,
) -> SessionEvent:
    """Build a SessionEvent with consistent epoch-ms timestamp."""
    return SessionEvent(
        seq=seq,
        type=type,
        session_id=session_id,
        time=now_ms_epoch(),
        data=data,
        ignorable=ignorable,
        source_event_seqs=source_event_seqs or [],
    )


def build_user_message_entry(data: dict[str, Any]) -> dict[str, Any]:
    """Build a MessageEntry dict from a user/message event's data payload.

    Uses typed _event_to_message_entry() internally for consistency.
    """
    entry = _event_to_message_entry(data, role="user")
    if entry:
        return entry.model_dump()
    return {
        "id": data.get("entry_id") or f"msg_user_{data.get('type', '')}",
        "parent_id": None,
        "timestamp": now_iso(),
        "type": "message",
        "role": "user",
        "content": data.get("content", ""),
        "tool_calls": None,
        "tool_call_id": None,
        "partial": False,
    }


def build_assistant_message_entry(data: dict[str, Any]) -> dict[str, Any]:
    """Build a MessageEntry dict from an assistant/message event's data payload.

    Uses typed _event_to_message_entry() internally for consistency.
    """
    entry = _event_to_message_entry(data, role="assistant")
    if entry:
        return entry.model_dump()
    return {
        "id": data.get("entry_id") or f"msg_asst_{data.get('type', '')}",
        "parent_id": None,
        "timestamp": now_iso(),
        "type": "message",
        "role": "assistant",
        "content": data.get("content", ""),
        "tool_calls": None,
        "tool_call_id": None,
        "partial": False,
    }


def build_tool_result_entry(data: dict[str, Any]) -> dict[str, Any]:
    """Build a MessageEntry dict from a tool/result event's data payload.

    Uses typed _event_to_message_entry() internally for consistency.
    """
    entry = _event_to_message_entry(data, role="tool")
    if entry:
        return entry.model_dump()
    return {
        "id": data.get("entry_id") or f"msg_tool_{data.get('type', '')}",
        "parent_id": None,
        "timestamp": now_iso(),
        "type": "message",
        "role": "tool",
        "content": data.get("content", ""),
        "tool_calls": None,
        "tool_call_id": data.get("tool_call_id"),
        "partial": False,
    }


def parse_entry(data: dict[str, Any]) -> SessionEntry | None:
    """Validate and construct a typed SessionEntry from a raw dict.

    Delegates to the typed _parse_entry() in session.py.
    """
    return _parse_entry(data)


class SessionEventLogger:
    """Factory for constructing SessionEvent instances.

    Message/tool/compaction events only. Turn/step lifecycle events are
    published via SessionBus (engine/trajectory), not this logger.
    """

    # ── Model-visible Message Surface ──────────────────────────

    def user_message(
        self,
        session_id: str,
        content: str,
        entry_id: str = "",
        *,
        parent_id: str | None = None,
        enriched_content: object = None,
        timestamp: str | None = None,
    ) -> SessionEvent:
        data: dict[str, Any] = {
            "type": "user/message",
            "content": content,
            "entry_id": entry_id,
            "parent_id": parent_id,
        }
        if enriched_content is not None:
            data["enriched_content"] = enriched_content
        if timestamp:
            data["timestamp"] = timestamp
        return _mk(
            type=SessionEventType.USER_MESSAGE,
            session_id=session_id,
            data=data,
        )

    def assistant_chunk(
        self, session_id: str, chunk: dict[str, Any]
    ) -> SessionEvent:
        return _mk(
            type=SessionEventType.ASSISTANT_CHUNK,
            session_id=session_id,
            data={"type": "assistant/chunk", "chunk": chunk},
            ignorable=True,
        )

    def assistant_message(
        self,
        session_id: str,
        content: str,
        entry_id: str = "",
        usage: dict[str, Any] | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        source_event_seqs: list[int] | None = None,
        *,
        parent_id: str | None = None,
        enriched_content: object = None,
        timestamp: str | None = None,
    ) -> SessionEvent:
        """Build an ASSISTANT_MESSAGE event.

        tool_calls is inlined into the event data so the redis loader can
        reconstruct the assistant entry's tool_calls without a separate
        sidechannel. Field is optional; older loaders that ignore it
        still function (with degraded SOP extraction capability).
        """
        data: dict[str, Any] = {
            "type": "assistant/message",
            "content": content,
            "entry_id": entry_id,
            "usage": usage,
            "tool_calls": tool_calls,
            "parent_id": parent_id,
        }
        if enriched_content is not None:
            data["enriched_content"] = enriched_content
        if timestamp:
            data["timestamp"] = timestamp
        return _mk(
            type=SessionEventType.ASSISTANT_MESSAGE,
            session_id=session_id,
            data=data,
            source_event_seqs=source_event_seqs,
        )

    def tool_result(
        self,
        session_id: str,
        tool_call_id: str,
        content: str,
        entry_id: str = "",
        error: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
        *,
        parent_id: str | None = None,
        enriched_content: object = None,
        is_error: bool = False,
        tool_name: str | None = None,
        timestamp: str | None = None,
    ) -> SessionEvent:
        data: dict[str, Any] = {
            "type": "tool/result",
            "tool_call_id": tool_call_id,
            "content": content,
            "entry_id": entry_id,
            "error": error,
            "meta": meta,
            "parent_id": parent_id,
            "is_error": is_error,
        }
        if tool_name:
            data["tool_name"] = tool_name
        if enriched_content is not None:
            data["enriched_content"] = enriched_content
        if timestamp:
            data["timestamp"] = timestamp
        return _mk(
            type=SessionEventType.TOOL_RESULT,
            session_id=session_id,
            data=data,
        )

    # ── System Events ──────────────────────────────────────────

    def request_header(
        self,
        session_id: str,
        header: dict[str, Any],
        reason: str = "update",
    ) -> SessionEvent:
        return _mk(
            type=SessionEventType.REQUEST_HEADER,
            session_id=session_id,
            data={"type": "request/header", "header": header, "reason": reason},
            ignorable=True,
        )

    def request_context(
        self, session_id: str, context: dict[str, Any]
    ) -> SessionEvent:
        return _mk(
            type=SessionEventType.REQUEST_CONTEXT,
            session_id=session_id,
            data={"type": "request/context", "context": context},
            ignorable=True,
        )

    def todo_write(
        self, session_id: str, todos: list[dict[str, Any]]
    ) -> SessionEvent:
        return _mk(
            type=SessionEventType.TODO_WRITE,
            session_id=session_id,
            data={"type": "todo/write", "todos": todos},
            ignorable=True,
        )

    def session_end_seed(self, session_id: str) -> SessionEvent:
        return _mk(
            type=SessionEventType.SESSION_END_SEED,
            session_id=session_id,
            data={"type": "session/end-seed"},
            ignorable=True,
        )

    # ── Session Lifecycle ──────────────────────────────────────

    def session_created(
        self, session_id: str, header: dict[str, Any]
    ) -> SessionEvent:
        return _mk(
            type=SessionEventType.SESSION_CREATED,
            session_id=session_id,
            data={"type": "session_created", "header": header},
        )

    def session_deleted(self, session_id: str) -> SessionEvent:
        return _mk(
            type=SessionEventType.SESSION_DELETED,
            session_id=session_id,
            data={"type": "session_deleted"},
        )

    # ── High-level converters: SessionEntry → events ──────

    def message_entry(
        self,
        session_id: str,
        entry: SessionEntry,
        leaf_id: str,
    ) -> list[SessionEvent]:
        """Convert a MessageEntry into the appropriate event(s).

        - role=user → USER_MESSAGE
        - role=assistant → ASSISTANT_MESSAGE (tool_calls inlined;
          see assistant_message())
        - role=tool → TOOL_RESULT
        """
        events: list[SessionEvent] = []

        if not isinstance(entry, MessageEntry):
            return events

        # Content 可以是纯文本 str 或 list[ContentBlock]（pydantic models）。
        # 不能对 list[ContentBlock] 直接 str()——那会产生 Python repr
        #  "[ImageBlock(type='image', ...), TextBlock(...)]" 这种不可回滚的
        # 脏数据，持久化后刷新前端就看到一坨 repr 文本。改为把 block 列表
        # 序列化为 dict 列表（event.data 是 dict[str, Any]，可以承载 list），
        # model_dump_json 会自动把它序列化 JSON 数组，恢复时 MessageEntry
        # 能通过 pydantic discriminator 还原成 ContentBlock。
        raw_content = entry.content
        if isinstance(raw_content, str):
            content: Any = raw_content
        elif isinstance(raw_content, list):
            content = [
                b.model_dump(mode="json") if hasattr(b, "model_dump") else b
                for b in raw_content
            ]
        else:
            content = str(raw_content) if raw_content is not None else ""

        if entry.role == "user":
            events.append(self.user_message(
                session_id,
                content=content,
                entry_id=entry.id,
                parent_id=entry.parent_id,
                enriched_content=entry.enriched_content,
                timestamp=entry.timestamp,
            ))

        elif entry.role == "assistant":
            events.append(self.assistant_message(
                session_id,
                content=content,
                entry_id=entry.id,
                tool_calls=entry.tool_calls,
                parent_id=entry.parent_id,
                enriched_content=entry.enriched_content,
                timestamp=entry.timestamp,
            ))

        elif entry.role == "tool":
            events.append(self.tool_result(
                session_id,
                tool_call_id=entry.tool_call_id or "",
                content=content,
                entry_id=entry.id,
                parent_id=entry.parent_id,
                enriched_content=entry.enriched_content,
                is_error=entry.is_error,
                tool_name=entry.tool_name,
                timestamp=entry.timestamp,
            ))

        return events

    def batch_added(
        self,
        session_id: str,
        entries: list[SessionEntry],
        leaf_id: str,
    ) -> list[SessionEvent]:
        """Convert a batch of SessionEntry into the appropriate events."""
        events: list[SessionEvent] = []
        for entry in entries:
            events.extend(self.message_entry(session_id, entry, leaf_id))
        return events

    # ── Extensions: non-message entry updates (compaction / replacement) ──

    def entry_updated(
        self,
        session_id: str,
        entry: SessionEntry,
        leaf_id: str | None = None,
    ) -> SessionEvent:
        """Build an entry_updated event for compaction / microcompaction / replacement."""
        data: dict[str, Any] = {"type": "entry_updated", "entry": entry.model_dump()}
        if leaf_id is not None:
            data["leaf_id"] = leaf_id
        return _mk(
            type=SessionEventType.ENTRY_UPDATED,
            session_id=session_id,
            data=data,
        )

    def batch_entry_updated(
        self,
        session_id: str,
        entries: list[SessionEntry],
        leaf_id: str | None = None,
    ) -> list[SessionEvent]:
        """Build entry_updated events for batch non-message updates."""
        return [
            self.entry_updated(session_id, entry, leaf_id)
            for entry in entries
        ]
