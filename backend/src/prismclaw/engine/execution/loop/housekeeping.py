from __future__ import annotations

import logging
import time

from prismclaw.engine.foundation.telemetry import HotPathTelemetry
from prismclaw.schema.content.streaming import HeartbeatDetail

logger = logging.getLogger(__name__)


def _log_exec_phase(
    *,
    conv_id: str,
    phase: str,
    tool_name: str | None = None,
    iteration: int | None = None,
    elapsed_s: float = 0.0,
) -> None:
    # SDD §10 缺陷修复：消息本体必须携带字段，不能只靠 extra
    # （纯文本 formatter 下 "exec.phase" 是空消息）。
    logger.info(
        "exec.phase phase=%s conv_id=%s tool=%s iter=%s elapsed_s=%.3f",
        phase,
        conv_id,
        tool_name or "-",
        iteration if iteration is not None else "-",
        round(elapsed_s, 3),
    )


def _heartbeat_decision(
    *,
    now: float,
    turn_start: float,
    last_hb_at: float,
    last_chunk_at: float,
    delta_count: int,
    delta_chars: int,
    partial_name: str | None,
    current_phase: str,
    delta_interval_s: float = 2.0,
    time_interval_s: float = 3.0,
    delta_count_threshold: int = 20,
    min_turn_elapsed_s: float = 1.0,
) -> HeartbeatDetail | None:
    if now - turn_start < min_turn_elapsed_s:
        return None
    if delta_count >= 1 and (
        now - last_hb_at >= delta_interval_s or delta_count >= delta_count_threshold
    ):
        return HeartbeatDetail(partial_tool_name=partial_name, chars_so_far=delta_chars)
    if now - last_chunk_at >= time_interval_s:
        return HeartbeatDetail()
    return None


def _emit_iteration_metrics(
    telemetry: HotPathTelemetry,
    *,
    iteration: int,
    iter_started_at: float,
    compression_started_at: float,
    compression_finished_at: float,
    persist_started_at: float,
    persist_finished_at: float,
    pipeline_started_at: float,
    pipeline_finished_at: float,
    llm_started_at: float,
    llm_finished_at: float,
) -> None:
    iter_finished_at = time.monotonic()
    compression_ms = (compression_finished_at - compression_started_at) * 1000.0
    persist_ms = (persist_finished_at - persist_started_at) * 1000.0
    pipeline_ms = (pipeline_finished_at - pipeline_started_at) * 1000.0
    llm_ms = (llm_finished_at - llm_started_at) * 1000.0
    tool_ms = max(0.0, (iter_finished_at - llm_finished_at) * 1000.0)
    total_ms = (iter_finished_at - iter_started_at) * 1000.0
    telemetry.observe(
        "turn.iteration.total_ms",
        total_ms,
        checkpoint="iteration_complete",
        iteration=iteration,
        compression_ms=compression_ms,
        persist_ms=persist_ms,
        pipeline_ms=pipeline_ms,
        llm_ms=llm_ms,
        tool_ms=tool_ms,
    )