"""Observability primitives for Phase 0 metrics.

Provides in-memory Counter / Gauge / Histogram dataclasses plus a global
``emit`` interface. Designed to be:

* O(1) ``emit`` cost (target < 1ms per emit)
* label-aware (each metric name gets a frozendict label-set)
* introspection-friendly (``snapshot()`` returns a flat dict for tests)
* pure stdlib (no external metric backend required)

This module does NOT touch any existing EventBus. The two are independent:
EventBus streams structured events to sinks; Metrics records numeric samples
for tests + dashboards. The agent modules may choose to emit both.

Naming convention (v3 §11.1):
    {namespace}.{component}.{metric_name}[_{unit}]
    e.g. ``compression.orchestrator.circuit_breaker.failures``
          ``streaming.tool.first_completion_ms``

All names must be lowercase with ``.`` as the separator; values for time
histograms must use ``_ms`` / ``_seconds`` suffix to be self-describing.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Mapping

MetricLabels = Mapping[str, str]


def _freeze_labels(labels: MetricLabels | None) -> tuple[tuple[str, str], ...]:
    if not labels:
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


@dataclass
class Counter:
    """Monotonic counter. ``inc()`` adds 1; ``add(n)`` adds ``n`` (>=0)."""

    name: str
    _values: dict[tuple[tuple[str, str], ...], float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def inc(
        self,
        labels: MetricLabels | None = None,
        *,
        amount: float = 1.0,
    ) -> None:
        if amount < 0:
            raise ValueError(f"Counter {self.name!r} cannot decrement")
        key = _freeze_labels(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def add(self, amount: float, labels: MetricLabels | None = None) -> None:
        """Alias for ``inc(labels=..., amount=...)`` with positional amount."""
        self.inc(labels, amount=amount)

    def value(self, labels: MetricLabels | None = None) -> float:
        return self._values.get(_freeze_labels(labels), 0.0)

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            return {_format_key(self.name, key): v for key, v in self._values.items()}


@dataclass
class Gauge:
    """Point-in-time gauge. ``set(value)`` overwrites the current value."""

    name: str
    _values: dict[tuple[tuple[str, str], ...], float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def set(self, value: float, labels: MetricLabels | None = None) -> None:
        key = _freeze_labels(labels)
        with self._lock:
            self._values[key] = float(value)

    def value(self, labels: MetricLabels | None = None) -> float:
        return self._values.get(_freeze_labels(labels), 0.0)

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            return {_format_key(self.name, key): v for key, v in self._values.items()}


@dataclass
class Histogram:
    """Bounded-sample histogram with sum/count/min/max plus percentile sketch.

    Uses a simple streaming reservoir of the last ``max_samples`` observations
    (default 1024) so percentile queries stay bounded. For high-cardinality
    histograms, the caller should choose a smaller ``max_samples``.
    """

    name: str
    max_samples: int = 1024
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _series: dict[tuple[tuple[str, str], ...], list[float]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def observe(self, value: float, labels: MetricLabels | None = None) -> None:
        key = _freeze_labels(labels)
        with self._lock:
            series = self._series[key]
            series.append(float(value))
            if len(series) > self.max_samples:
                # drop oldest to keep memory bounded
                del series[: len(series) - self.max_samples]

    def snapshot(self) -> dict[str, dict[str, float]]:
        with self._lock:
            out: dict[str, dict[str, float]] = {}
            for key, series in self._series.items():
                if not series:
                    continue
                sorted_series = sorted(series)
                n = len(sorted_series)
                label_str = ",".join(f"{k}={v}" for k, v in key)
                full = f"{self.name}{{{label_str}}}" if label_str else self.name
                out[full] = {
                    "count": float(n),
                    "sum": float(sum(series)),
                    "min": float(sorted_series[0]),
                    "max": float(sorted_series[-1]),
                    "p50": _percentile(sorted_series, 0.5),
                    "p95": _percentile(sorted_series, 0.95),
                    "p99": _percentile(sorted_series, 0.99),
                }
            return out


def _format_key(name: str, key: tuple[tuple[str, str], ...]) -> str:
    """Render a metric name + label-key tuple as a flat snapshot key.

    Empty label set returns the bare name (no trailing ``{}``) so tests
    can assert with ``name in snapshot`` directly.
    """
    if not key:
        return name
    return f"{name}{{{','.join(f'{k}={v}' for k, v in key)}}}"


def _percentile(sorted_samples: list[float], p: float) -> float:
    if not sorted_samples:
        return 0.0
    if p <= 0:
        return sorted_samples[0]
    if p >= 1:
        return sorted_samples[-1]
    # Nearest-rank percentile (no interpolation) — sufficient for tests
    idx = max(0, min(len(sorted_samples) - 1, int(p * len(sorted_samples))))
    return float(sorted_samples[idx])


class _MetricsRegistry:
    """Thread-safe in-memory metric registry.

    ``emit`` records a single sample for any registered metric; metrics
    are auto-registered on first observation so callers don't need to
    declare them up-front.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, Counter] = {}
        self._gauges: dict[str, Gauge] = {}
        self._histograms: dict[str, Histogram] = {}
        # Per-name observed max_samples for histograms (set on first observation)
        self._hist_max_samples: dict[str, int] = {}

    # --- registration / lookup -----------------------------------------

    def counter(self, name: str) -> Counter:
        with self._lock:
            existing = self._counters.get(name)
            if existing is not None:
                return existing
            created = Counter(name=name)
            self._counters[name] = created
            return created

    def gauge(self, name: str) -> Gauge:
        with self._lock:
            existing = self._gauges.get(name)
            if existing is not None:
                return existing
            created = Gauge(name=name)
            self._gauges[name] = created
            return created

    def histogram(
        self,
        name: str,
        *,
        max_samples: int = 1024,
    ) -> Histogram:
        with self._lock:
            existing = self._histograms.get(name)
            if existing is not None:
                return existing
            created = Histogram(name=name, max_samples=max_samples)
            self._histograms[name] = created
            self._hist_max_samples[name] = max_samples
            return created

    # --- emit (the single hot-path entry point) -----------------------

    def emit(
        self,
        name: str,
        value: float,
        *,
        kind: str = "counter",
        labels: MetricLabels | None = None,
    ) -> None:
        """Record a metric sample.

        ``kind`` is one of ``counter`` / ``gauge`` / ``histogram``. The
        metric is auto-registered on first emit. The call is designed to
        be cheap (target < 1ms) so it can sit on the main execution path
        without adding measurable overhead.
        """
        k = kind.lower()
        if k == "counter":
            self.counter(name).inc(labels, amount=value)
        elif k == "gauge":
            self.gauge(name).set(value, labels)
        elif k == "histogram":
            self.histogram(name).observe(value, labels)
        else:
            raise ValueError(
                f"unknown metric kind {kind!r} for {name!r}; "
                f"expected counter/gauge/histogram"
            )

    # --- snapshot -----------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            out: dict[str, Any] = {"counters": {}, "gauges": {}, "histograms": {}}
            for c in self._counters.values():
                out["counters"].update(c.snapshot())
            for g in self._gauges.values():
                out["gauges"].update(g.snapshot())
            for h in self._histograms.values():
                out["histograms"].update(h.snapshot())
            return out

    def reset(self) -> None:
        """Drop all recorded samples. Test-only convenience."""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()
            self._hist_max_samples.clear()

    def known_names(self) -> tuple[str, ...]:
        with self._lock:
            names: set[str] = set()
            names.update(self._counters)
            names.update(self._gauges)
            names.update(self._histograms)
            return tuple(sorted(names))


# --- global singleton ---------------------------------------------------

_registry: _MetricsRegistry | None = None
_registry_lock = threading.Lock()


def get_metrics() -> _MetricsRegistry:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = _MetricsRegistry()
    return _registry


def emit(
    name: str,
    value: float,
    *,
    kind: str = "counter",
    labels: MetricLabels | None = None,
) -> None:
    """Convenience wrapper around the global metrics registry.

    Suitable for direct calls on the hot path; for tighter loops, fetch
    a Counter/Gauge/Histogram once and call its typed method.
    """
    get_metrics().emit(name, value, kind=kind, labels=labels)


def snapshot() -> dict[str, Any]:
    """Return a flat snapshot of every recorded metric. Test-friendly."""
    return get_metrics().snapshot()


def reset() -> None:
    """Reset the global registry. Test-only."""
    get_metrics().reset()


# --- guarded emit helpers (zero-cost when disabled) ---------------------

_GUARDED_PREFIX = "__skip__"


def emit_guarded(
    name: str,
    value: float,
    *,
    kind: str = "counter",
    labels: MetricLabels | None = None,
    guard: bool = False,
) -> None:
    """Emit only when ``guard`` is True.

    Used to wrap a metric in a feature flag check without paying the
    emit cost in the disabled path. This is NOT a generic skip-flag —
    the caller must pass ``guard`` explicitly so the dependency is
    visible at the call site.
    """
    if not guard:
        return
    emit(name, value, kind=kind, labels=labels)


__all__ = [
    "Counter",
    "Gauge",
    "Histogram",
    "MetricLabels",
    "emit",
    "emit_guarded",
    "get_metrics",
    "reset",
    "snapshot",
]
