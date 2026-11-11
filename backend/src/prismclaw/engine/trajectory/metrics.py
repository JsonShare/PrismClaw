# backend/src/prismclaw/core/trajectory/metrics.py
"""
Trajectory-native in-memory metrics (no external SDK lock-in).

Stable API surface (swap the backend under these names later if needed):

  record_stage_duration(stage, sec)  :: per-stage duration histogram
  inc_write(mode)                    :: monotonic counters
  StageTimer(stage)                  :: context-manager convenience

When metrics disabled all public functions become literal `return None`
— no allocation, no locking.

NOTE: the Redis-era write modes (OK_ZSET / HSET_SKIP_OK / DRY_SKIPPED)
and the record-size histogram (raw/zstd bytes) were removed together with
the Redis persistence backend — PostgreSQL flush does not produce those
events.
"""
from __future__ import annotations

import enum
import threading
import time


_ENABLED = True


class Stage(str, enum.Enum):
    PROJECTOR_INPUT = "projector_input"
    PROJECTOR = "projector"
    ASSEMBLE_ENTRY = "assemble_entry"
    PIPELINE_OUT = "pipeline_out"
    PACKER_FINAL = "packer_final"
    SNAPSHOT = "snapshot"
    FLUSH = "flush"
    # Response-side apply handlers' duration
    LLM_RESPONSE_APPLY = "llm_response_apply"
    LLM_FAILURE_APPLY = "llm_failure_apply"


class WriteMode(str, enum.Enum):
    LLM_RESPONSE_RECEIVED = "llm_response_received"
    LLM_REQUEST_FAILED = "llm_request_failed"


def set_metrics_enabled(flag: bool) -> None:
    global _ENABLED
    _ENABLED = bool(flag)


# -------------- default backend (atomic in-memory counters + histograms) ------
_LOCK = threading.Lock()
# stage -> list[float] (kept bounded: rolling window last 4096 samples per stage)
_STAGE_HIST: dict[str, list[float]] = {}
# WriteMode -> int
_WRITE_CTR: dict[str, int] = {}

_MAX_SAMPLES_PER_KEY = 4096


def reset_all_for_tests() -> None:
    """Only for pytest. Not part of public stable API."""
    with _LOCK:
        _STAGE_HIST.clear()
        _WRITE_CTR.clear()


def _stage_names() -> set[str]:
    return {s.value for s in Stage}


def stage_hist_snapshot() -> dict[str, tuple[float, float, int, float]]:
    """Returns {key: (min, max, count, avg)} for external dashboards."""
    with _LOCK:
        out: dict[str, tuple[float, float, int, float]] = {}
        for k, arr in _STAGE_HIST.items():
            if not arr:
                continue
            total = sum(arr)
            cnt = len(arr)
            out[k] = (min(arr), max(arr), cnt, total / cnt)
    return out


def write_counter_snapshot() -> dict[str, int]:
    with _LOCK:
        return dict(_WRITE_CTR)


# ---------- public stable API ----------

def record_stage_duration(stage: str, duration_sec: float) -> None:
    if not _ENABLED:
        return
    if stage not in _stage_names():
        return
    with _LOCK:
        buf = _STAGE_HIST.get(stage)
        if buf is None:
            buf = []
            _STAGE_HIST[stage] = buf
        buf.append(duration_sec)
        if len(buf) > _MAX_SAMPLES_PER_KEY:
            # roll: drop oldest 1/4 to keep amortised insert O(1)
            del buf[:_MAX_SAMPLES_PER_KEY // 4]


def inc_write(mode: str) -> None:
    if not _ENABLED:
        return
    with _LOCK:
        _WRITE_CTR[mode] = _WRITE_CTR.get(mode, 0) + 1


class StageTimer:
    """Context manager convenience — records duration into record_stage_duration.

    Usage:
        with StageTimer(Stage.PACKER_FINAL):
            ... do slow work ...
    """

    __slots__ = ("stage", "_t0")

    def __init__(self, stage: str) -> None:
        self.stage = stage

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        dt = time.perf_counter() - self._t0
        record_stage_duration(self.stage, dt)
        return False  # don't suppress


__all__ = [
    "Stage", "WriteMode",
    "record_stage_duration", "inc_write", "StageTimer",
    "set_metrics_enabled",
    # debugging / test helpers
    "reset_all_for_tests",
    "stage_hist_snapshot", "write_counter_snapshot",
]
