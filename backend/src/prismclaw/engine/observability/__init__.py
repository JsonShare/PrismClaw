"""Observability: metrics + feature flags for Phase 0.

Public surface:

* ``prismclaw.engine.observability.metrics`` — Counter / Gauge / Histogram,
  global ``emit()``, ``snapshot()``, ``reset()``.
* ``prismclaw.engine.observability.feature_flags`` — ``is_enabled()``,
  ``set_static_flag()``, and the three Phase 0 flag constants.
"""

from __future__ import annotations

from prismclaw.engine.observability.feature_flags import (
    FLAG_SCHEDULER_V2,
    FLAG_STREAMING_V2,
    clear_static_flags,
    is_enabled,
    set_static_flag,
    set_static_flags_from_mapping,
    snapshot_static_flags,
)
from prismclaw.engine.foundation.metrics import (
    Counter,
    Gauge,
    Histogram,
    MetricLabels,
    emit,
    emit_guarded,
    get_metrics,
    reset,
    snapshot,
)

__all__ = [
    # metrics
    "Counter",
    "Gauge",
    "Histogram",
    "MetricLabels",
    "emit",
    "emit_guarded",
    "get_metrics",
    "reset",
    "snapshot",
    # feature flags
    "FLAG_SCHEDULER_V2",
    "FLAG_STREAMING_V2",
    "clear_static_flags",
    "is_enabled",
    "set_static_flag",
    "set_static_flags_from_mapping",
    "snapshot_static_flags",
]
