from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProfilerEvent:
    checkpoint: str
    elapsed_ms: float
    metadata: dict[str, Any] = field(default_factory=dict)


class TurnProfiler:
    """Step A1 skeleton profiler for turn-level checkpoint instrumentation."""

    def __init__(
        self,
        *,
        session_id: str,
        request_id: str | None = None,
        logger_: logging.Logger | None = None,
        enabled: bool = True,
    ) -> None:
        self.session_id = session_id
        self.request_id = request_id or ""
        self._logger = logger_ or logger
        self._enabled = enabled
        self._started_at = time.monotonic()
        self._events: list[ProfilerEvent] = []

    def mark(self, checkpoint: str, /, **metadata: Any) -> ProfilerEvent | None:
        if not self._enabled:
            return None
        event = ProfilerEvent(
            checkpoint=checkpoint,
            elapsed_ms=max(0.0, (time.monotonic() - self._started_at) * 1000.0),
            metadata=dict(metadata),
        )
        self._events.append(event)
        self._logger.info(
            "turn_checkpoint session_id=%s request_id=%s checkpoint=%s elapsed_ms=%.1f metadata=%r",
            self.session_id,
            self.request_id or "-",
            checkpoint,
            event.elapsed_ms,
            event.metadata,
        )
        return event

    def snapshot(self) -> tuple[ProfilerEvent, ...]:
        return tuple(self._events)

    def log_summary(self, *, status: str | None = None) -> None:
        if not self._enabled:
            return
        checkpoints = [event.checkpoint for event in self._events]
        self._logger.info(
            "turn_profiler_summary session_id=%s request_id=%s status=%s checkpoints=%r total_elapsed_ms=%.1f",
            self.session_id,
            self.request_id or "-",
            status or "",
            checkpoints,
            max(0.0, (time.monotonic() - self._started_at) * 1000.0),
        )
