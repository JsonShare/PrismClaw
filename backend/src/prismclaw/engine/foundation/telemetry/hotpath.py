from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Iterator, Mapping

from prismclaw.engine.foundation.telemetry.profiler import TurnProfiler
from prismclaw.engine.foundation.metrics import emit


def _normalize_labels(labels: Mapping[str, Any] | None) -> dict[str, str] | None:
    if not labels:
        return None
    normalized: dict[str, str] = {}
    for key, value in labels.items():
        if value is None:
            continue
        if isinstance(value, bool):
            normalized[str(key)] = "true" if value else "false"
            continue
        normalized[str(key)] = str(value)
    return normalized or None


class HotPathTelemetry:
    """Unified owner for hot-path timing metrics and optional turn checkpoints."""

    def __init__(self, *, profiler: TurnProfiler | None = None) -> None:
        self._profiler = profiler

    def observe(
        self,
        metric_name: str,
        elapsed_ms: float,
        *,
        checkpoint: str | None = None,
        labels: Mapping[str, Any] | None = None,
        **metadata: Any,
    ) -> float:
        normalized_labels = _normalize_labels(labels)
        clamped_elapsed_ms = max(0.0, float(elapsed_ms))
        emit(
            metric_name,
            clamped_elapsed_ms,
            kind="histogram",
            labels=normalized_labels,
        )
        if checkpoint is not None and self._profiler is not None:
            checkpoint_metadata = dict(metadata)
            checkpoint_metadata["metric_name"] = metric_name
            checkpoint_metadata["elapsed_ms"] = clamped_elapsed_ms
            if normalized_labels is not None:
                checkpoint_metadata["labels"] = normalized_labels
            self._profiler.mark(checkpoint, **checkpoint_metadata)
        return clamped_elapsed_ms

    @contextmanager
    def measure(
        self,
        metric_name: str,
        *,
        checkpoint: str | None = None,
        labels: Mapping[str, Any] | None = None,
        **metadata: Any,
    ) -> Iterator[None]:
        started_at = time.monotonic()
        try:
            yield
        finally:
            self.observe(
                metric_name,
                (time.monotonic() - started_at) * 1000.0,
                checkpoint=checkpoint,
                labels=labels,
                **metadata,
            )
