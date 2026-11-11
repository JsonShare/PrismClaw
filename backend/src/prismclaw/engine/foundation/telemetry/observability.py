"""Structured observability layer for agent operations.

Provides:
1. Structured event system with PII safety guarantees
2. Event bus with queue + sink pattern (events cached before sink attaches)
3. Convenience emitters for hot-path operations (API calls, tool execution, compaction)
4. OTel span integration support
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from prismclaw.schema.session.cache_types import PromptCacheRuntimeState

logger = logging.getLogger(__name__)


class EventCategory(str, Enum):
    API_QUERY = "api_query"
    API_ERROR = "api_error"
    API_SUCCESS = "api_success"
    TOOL_EXECUTION = "tool_execution"
    TOOL_BATCH = "tool_batch"
    COMPACTION = "compaction"
    ERROR_RECOVERY = "error_recovery"
    SESSION_LIFECYCLE = "session_lifecycle"
    CACHE_EVENT = "cache_event"


@dataclass
class StructuredEvent:
    """A structured analytics event."""
    event_name: str
    category: EventCategory
    metadata: dict[str, Any] = field(default_factory=dict)
    pii_verified: bool = False  # Caller asserts no PII in metadata values


class EventSink(Protocol):
    """Protocol for event consumers."""
    def on_event(self, event: StructuredEvent) -> None: ...


class _QueuedEvent:
    __slots__ = ("event",)

    def __init__(self, event: StructuredEvent) -> None:
        self.event = event
class EventBus:
    """Structured event bus with queue + sink pattern.

    Events emitted before a sink is attached are queued and drained
    once attach_sink() is called. This ensures no events are lost
    during initialization.
    """

    def __init__(self) -> None:
        self._queue: list[StructuredEvent] = []
        self._sinks: list[EventSink] = []
        self._attached = False

    def emit(self, event: StructuredEvent) -> None:
        """Emit an event. Queued if no sink is attached yet."""
        if not self._attached:
            self._queue.append(event)
            return
        for sink in self._sinks:
            try:
                sink.on_event(event)
            except Exception:
                logger.debug("Event sink error for %s", event.event_name, exc_info=True)

    def attach_sink(self, sink: EventSink) -> None:
        """Attach a sink and drain queued events."""
        self._sinks.append(sink)
        if not self._attached:
            self._attached = True
            queued = self._queue
            self._queue = []
            for event in queued:
                try:
                    sink.on_event(event)
                except Exception:
                    logger.debug("Event sink drain error for %s", event.event_name, exc_info=True)

    def reset(self) -> None:
        """Reset the bus (for testing)."""
        self._queue.clear()
        self._sinks.clear()
        self._attached = False


# Global singleton
_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Get the global event bus instance."""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus


def _emit(event_name: str, category: EventCategory, **metadata: Any) -> None:
    """Internal helper to emit a structured event."""
    get_event_bus().emit(StructuredEvent(
        event_name=event_name,
        category=category,
        metadata=metadata,
        pii_verified=True,
    ))


# ─── Convenience emitters ────────────────────────────────────────────

def emit_api_query(
    *,
    model: str,
    message_count: int,
    tool_count: int,
    provider: str = "",
    request_id: str = "",
    session_id: str = "",
    iteration: int = 0,
) -> None:
    """Emit an API query event (before the request is sent)."""
    _emit(
        "api_query",
        EventCategory.API_QUERY,
        model=model,
        message_count=message_count,
        tool_count=tool_count,
        provider=provider,
        request_id=request_id,
        session_id=session_id,
        iteration=iteration,
    )


def emit_api_success(
    *,
    model: str,
    duration_ms: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    provider: str = "",
    request_id: str = "",
    finish_reason: str = "",
) -> None:
    """Emit an API success event (after a successful response)."""
    cache_hit_rate = 0.0
    total_input = input_tokens + cache_creation_tokens + cache_read_tokens
    if total_input > 0:
        cache_hit_rate = cache_read_tokens / total_input
    _emit(
        "api_success",
        EventCategory.API_SUCCESS,
        model=model,
        duration_ms=round(duration_ms, 1),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_hit_rate=round(cache_hit_rate, 4),
        provider=provider,
        request_id=request_id,
        finish_reason=finish_reason,
    )


def emit_api_error(
    *,
    error_code: str,
    error_message: str,
    model: str = "",
    duration_ms: float = 0,
    attempt: int = 0,
    provider: str = "",
    request_id: str = "",
) -> None:
    """Emit an API error event."""
    _emit(
        "api_error",
        EventCategory.API_ERROR,
        error_code=error_code,
        error_message_length=len(error_message),
        model=model,
        duration_ms=round(duration_ms, 1),
        attempt=attempt,
        provider=provider,
        request_id=request_id,
    )


def emit_tool_execution(
    *,
    tool_name: str,
    tool_call_id: str,
    duration_ms: float,
    success: bool,
    is_error: bool = False,
    result_chars: int = 0,
    session_id: str = "",
    iteration: int = 0,
) -> None:
    """Emit a tool execution event."""
    _emit(
        "tool_execution",
        EventCategory.TOOL_EXECUTION,
        tool_name=tool_name,
        duration_ms=round(duration_ms, 1),
        success=success,
        is_error=is_error,
        result_chars=result_chars,
        session_id=session_id,
        iteration=iteration,
    )


def emit_tool_batch(
    *,
    tool_count: int,
    total_duration_ms: float,
    success_count: int,
    error_count: int,
    denied_count: int,
    session_id: str = "",
    iteration: int = 0,
) -> None:
    """Emit a tool batch execution summary event."""
    _emit(
        "tool_batch",
        EventCategory.TOOL_BATCH,
        tool_count=tool_count,
        total_duration_ms=round(total_duration_ms, 1),
        success_count=success_count,
        error_count=error_count,
        denied_count=denied_count,
        session_id=session_id,
        iteration=iteration,
    )


def emit_compaction(
    *,
    action: str,
    tokens_before: int = 0,
    tokens_after: int = 0,
    duration_ms: float = 0,
    session_id: str = "",
    reason_code: str = "",
) -> None:
    """Emit a compaction event."""
    reduction_pct = 0.0
    if tokens_before > 0:
        reduction_pct = (tokens_before - tokens_after) / tokens_before
    _emit(
        "compaction",
        EventCategory.COMPACTION,
        action=action,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        reduction_pct=round(reduction_pct, 4),
        duration_ms=round(duration_ms, 1),
        session_id=session_id,
        reason_code=reason_code,
    )


def emit_error_recovery(
    *,
    error_type: str,
    recovery_action: str,
    success: bool,
    attempt: int = 1,
    session_id: str = "",
) -> None:
    """Emit an error recovery event."""
    _emit(
        "error_recovery",
        EventCategory.ERROR_RECOVERY,
        error_type=error_type,
        recovery_action=recovery_action,
        success=success,
        attempt=attempt,
        session_id=session_id,
    )


def emit_cache_event(
    *,
    event_type: str,
    cache_hit_rate: float = 0,
    frozen_replacements: int = 0,
    session_id: str = "",
) -> None:
    """Emit a cache-related event."""
    _emit(
        "cache_event",
        EventCategory.CACHE_EVENT,
        event_type=event_type,
        cache_hit_rate=round(cache_hit_rate, 4),
        frozen_replacements=frozen_replacements,
        session_id=session_id,
    )


# ─── Cache break detection ───────────────────────────────────────────

class CacheBreakDetector:
    """Stateless cache-break detector driven by PromptCacheRuntimeState.

    The detector itself no longer owns cross-request memory. Previous
    request facts live on ``PromptCacheRuntimeState`` and are updated
    only through ``record_request``.
    """

    def __init__(
        self,
        *,
        drop_threshold: float = 0.3,
        min_input_tokens: int = 1000,
    ) -> None:
        self._drop_threshold = drop_threshold
        self._min_input_tokens = min_input_tokens

    def record_request(
        self,
        *,
        runtime_state: PromptCacheRuntimeState,
        cache_read_tokens: int = 0,
        input_tokens: int = 0,
        session_id: str = "",
    ) -> bool:
        """Record a request's cache metrics. Returns True if a cache break was detected."""
        is_break = False
        cache_hit_rate = (cache_read_tokens / input_tokens) if input_tokens > 0 else 0.0
        last_hit_rate = self._hit_rate(
            runtime_state.prev_cache_read_tokens,
            runtime_state.prev_input_tokens,
        )

        if (
            last_hit_rate is not None
            and (runtime_state.prev_input_tokens or 0) >= self._min_input_tokens
            and input_tokens >= self._min_input_tokens
        ):
            drop = last_hit_rate - cache_hit_rate
            if drop >= self._drop_threshold:
                is_break = True
                logger.warning(
                    "cache_break_detected: hit_rate dropped %.1f%% → %.1f%% (drop=%.1f%%)",
                    last_hit_rate * 100,
                    cache_hit_rate * 100,
                    drop * 100,
                )
                emit_cache_event(
                    event_type="cache_break_detected",
                    cache_hit_rate=cache_hit_rate,
                    session_id=session_id,
                )

        runtime_state.prev_cache_read_tokens = max(0, int(cache_read_tokens))
        runtime_state.prev_input_tokens = max(0, int(input_tokens))
        return is_break

    @staticmethod
    def _hit_rate(
        cache_read_tokens: int | None,
        input_tokens: int | None,
    ) -> float | None:
        if cache_read_tokens is None or input_tokens is None or input_tokens <= 0:
            return None
        return cache_read_tokens / input_tokens


# ─── Logging sink (default) ──────────────────────────────────────────

class LoggingSink:
    """Default sink that logs events via Python logging."""

    def on_event(self, event: StructuredEvent) -> None:
        meta_str = " ".join(f"{k}={v}" for k, v in sorted(event.metadata.items()))
        logger.info("event=%s %s", event.event_name, meta_str)
