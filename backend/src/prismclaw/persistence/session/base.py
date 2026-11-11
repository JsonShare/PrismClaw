from __future__ import annotations

import json
import secrets
from typing import Callable

from prismclaw.schema.session.types import SessionEntry
from prismclaw.schema.storage.storage_types import SessionStore as SessionStore

_METRIC_COMMIT_CALLS = "persistence.session_store.commit_calls"
_METRIC_COMMIT_SIZE = "persistence.session_store.commit_size"
_METRIC_COMMIT_ERRORS = "persistence.session_store.commit_errors"
_METRIC_COMMIT_LATENCY = "persistence.session_store.commit_latency_ms"


def emit_commit_metrics(
    size: int,
    elapsed_ms: float,
    *,
    error: bool = False,
    metrics_emit: Callable[..., None] | None = None,
) -> None:
    """Metrics helper for commit_events implementations.

    Args:
        metrics_emit: Callable with signature (name: str, value: float, *, kind: str) -> None.
            If None, metrics are silently skipped (no-op).
    """
    if metrics_emit is None:
        return

    if error:
        metrics_emit(_METRIC_COMMIT_ERRORS, 1.0, kind="counter")
        return
    metrics_emit(_METRIC_COMMIT_CALLS, 1.0, kind="counter")
    if size > 0:
        metrics_emit(_METRIC_COMMIT_SIZE, float(size), kind="histogram")
    metrics_emit(_METRIC_COMMIT_LATENCY, float(elapsed_ms), kind="histogram")


def serialize_entry(entry: SessionEntry) -> str:
    """Serialize a SessionEntry to JSON string for Redis storage."""
    if hasattr(entry, "model_dump_json"):
        return entry.model_dump_json()
    return json.dumps(entry)


def _generate_session_id(session_key: str) -> str:
    suffix = secrets.token_hex(8)
    return f"{session_key}:s:{suffix}"


__all__ = ["SessionStore", "emit_commit_metrics", "serialize_entry", "_generate_session_id"]
