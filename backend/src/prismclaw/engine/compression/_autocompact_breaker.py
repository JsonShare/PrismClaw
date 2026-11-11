from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from prismclaw.engine.foundation.metrics import emit

if TYPE_CHECKING:
    # 仅类型注解所需，runtime 不依赖 execution。
    from prismclaw.engine.execution.checkpoint import CompressionRuntimeState


# ──────────────────────────── Circuit Breaker ────────────────────────────


@dataclass
class AutocompactBreaker:
    """Per-session consecutive-failure counter for autocompact.

    Mirrors Claude's
    ``MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3`` from
    ``autoCompact.ts``.  When the counter reaches the threshold, the
    coordinator raises :class:`CircuitBreakerException` instead of
    burning more API calls in a doomed ``prompt_too_long`` loop.

    The breaker state is intentionally simple — a single counter per
    instance.  Lifecycle is owned by the runner (one breaker per
    session) so the counter survives across turns.
    """

    consecutive_failures: int = 0

    def get_consecutive_failures(self) -> int:
        return int(self.consecutive_failures)

    def increment(self) -> int:
        self.consecutive_failures += 1
        return self.consecutive_failures

    def reset(self) -> None:
        self.consecutive_failures = 0

    def is_open(self, threshold: int) -> bool:
        return self.consecutive_failures >= max(1, int(threshold))

    def to_dict(self) -> dict[str, int]:
        return {"consecutive_failures": self.consecutive_failures}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AutocompactBreaker":
        if not isinstance(data, dict):
            return cls()
        return cls(
            consecutive_failures=int(data.get("consecutive_failures", 0) or 0),
        )


def _resolve_breaker(
    deps: Any,
    compression_runtime: CompressionRuntimeState,
) -> AutocompactBreaker:
    """Return the per-session autocompact breaker, lazily defaulting
    to a fresh in-memory instance for tests / non-runnner callers.

    The breaker remains an in-memory helper, but its counter is
    hydrated from ``CompressionRuntimeState`` so the persisted snapshot
    stays the single owner across reloads / resume / replay.
    """
    breaker = getattr(deps, "autocompact_breaker", None)
    if isinstance(breaker, AutocompactBreaker):
        return breaker
    try:
        initial = int(compression_runtime.consecutive_failures or 0)
    except Exception:
        initial = 0
    breaker = AutocompactBreaker(consecutive_failures=initial)
    try:
        deps.autocompact_breaker = breaker  # type: ignore[attr-defined]
    except Exception:
        pass
    return breaker


def _persist_breaker_state(
    compression_runtime: CompressionRuntimeState,
    breaker: AutocompactBreaker,
) -> None:
    """Flush the breaker counter to ``CompressionRuntimeState``.

    Best-effort: any failure to write back is swallowed so a
    transient persistence error does not break the compaction
    flow.
    """
    try:
        compression_runtime.consecutive_failures = breaker.get_consecutive_failures()
    except Exception:
        pass


def _persist_summary_text(
    compression_runtime: CompressionRuntimeState,
    text: str,
) -> None:
    """Flush the last successful summary text to ``CompressionRuntimeState``.

    The session-memory bypass in ``try_compaction`` can reuse this
    persisted text to re-anchor a reloaded session without calling
    the LLM again.
    """
    try:
        compression_runtime.last_successful_summary_text = (text or "")[:16_000]
    except Exception:
        pass


_METRIC_AUTOCOMPACT_TRIGGER = "autocompact.trigger"
_METRIC_AUTOCOMPACT_SUCCESS = "autocompact.summary_success"
_METRIC_AUTOCOMPACT_FAILURE = "autocompact.summary_failure"
_METRIC_AUTOCOMPACT_GAUGE = "autocompact.consecutive_failures"
_METRIC_AUTOCOMPACT_OPEN = "autocompact.circuit_breaker_open"

# PTL retry constants mirror Claude's compact.ts:451-475.
# MAX_PTL_RETRIES is the number of additional attempts after the
# first call, so the compact API can be invoked up to 4 times total.
_MAX_PTL_RETRIES = 3
_PROMPT_TOO_LONG_REASON = "prompt_too_long"


def _emit_breaker_metric(
    name: str,
    *,
    error_type: str | None = None,
    breaker: AutocompactBreaker,
) -> None:
    try:
        if name == _METRIC_AUTOCOMPACT_GAUGE:
            emit(
                name,
                float(breaker.get_consecutive_failures()),
                kind="gauge",
            )
        elif error_type is not None:
            emit(name, 1.0, kind="counter", labels={"error_type": error_type})
        else:
            emit(name, 1.0, kind="counter")
    except Exception:
        # Metrics are observability-only; never block the run path.
        return
