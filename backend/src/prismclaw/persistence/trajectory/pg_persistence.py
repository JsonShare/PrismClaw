"""Trajectory audit → PostgreSQL persistence.

Trajectory audit persisted as PostgreSQL rows:
  - pc_trajectory_session: One row per session (aggregate metadata)
  - pc_trajectory_record: One row per LLM iteration (full RequestAuditRecord)

Uses the project-wide shared AsyncEngine (see `prismclaw.infra.db`)
and follows the same raw-SQL-with-text() pattern as userdb.

Key interfaces:
  flush_state_to_pg(state, session_factory, *, only_if_dirty) -> bool
  load_state_from_pg(session_id, session_factory) -> TrajectoryProjectionState
  read_snapshot_v2_pg(session_id, session_factory, *, limit, ascending) -> TrajectorySnapshot
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prismclaw.persistence.sql_runner import apply_core_schema
from prismclaw.persistence.trajectory import sql_models
from prismclaw.schema.trajectory import (
    RequestAuditRecord,
    TrajectoryCell,
    TrajectorySnapshot,
    TrajectorySnapshotMeta,
)

if TYPE_CHECKING:
    from prismclaw.schema.trajectory import TrajectoryProjectionState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema init (idempotent, called once at app startup)
# ---------------------------------------------------------------------------

async def init_pg_schema(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """创建轨迹表；DDL 见 deploy/sql/init_postgres.sql。"""
    await apply_core_schema(session_factory)


# ---------------------------------------------------------------------------
# RequestAuditRecord → PostgreSQL row
# ---------------------------------------------------------------------------

def _to_jsonb(obj: Any) -> str:
    """Serialize a Python list/dict to a JSON string for PostgreSQL JSONB columns.

    asyncpg cannot directly serialize Python objects to PostgreSQL JSONB —
    we must pass a string or use json.dumps(). This helper handles both
    list and dict types with safe defaults.
    """
    if obj is None:
        return "null"
    if isinstance(obj, str):
        # Already a string — check if it's valid JSON
        try:
            json.loads(obj)
            return obj
        except (json.JSONDecodeError, TypeError):
            # Not valid JSON, wrap as JSON string
            return json.dumps(obj)
    return json.dumps(obj, ensure_ascii=False, default=str)


def _record_to_params(record: RequestAuditRecord) -> dict[str, Any]:
    """Convert a RequestAuditRecord to a dict matching :param_name placeholders.

    Keys MUST match sql_models.INSERT_RECORD_UPSERT
    :param_name format exactly (52 fields).

    IMPORTANT: JSONB columns receive JSON strings (via _to_jsonb),
    not Python objects — asyncpg cannot auto-serialize list/dict to JSONB.
    """
    # Packer final system prompt: TEXT only when it has content
    system_prompt_text = record.packer_final_system_prompt_text
    if system_prompt_text and len(system_prompt_text) > 131072:
        system_prompt_text = system_prompt_text[:131072] + "...[truncated]"

    return {
        # identity
        "session_id": record.session_id,
        "iteration": record.iteration,
        "turn": record.turn,
        "started_at_ms": record.started_at_ms,
        "completed_at_ms": record.completed_at_ms,
        "status": record.status,
        # Layer 0.5 (JSONB columns → JSON strings)
        "projector_input_order_count": record.projector_input_order_count,
        "projector_input_entry_types": _to_jsonb(list(record.projector_input_entry_types)),
        "projector_input_head_seq": _to_jsonb(list(record.projector_input_head_seq)),
        "projector_input_tail_seq": _to_jsonb(list(record.projector_input_tail_seq)),
        "projector_input_tail_types": _to_jsonb(list(record.projector_input_tail_types)),
        # Layer ①
        "projector_msg_count": record.projector_msg_count,
        "projector_msg_roles": _to_jsonb(list(record.projector_msg_roles)),
        "projector_head": _to_jsonb(list(record.projector_head)),
        "projector_tail": _to_jsonb(list(record.projector_tail)),
        # Fix F: appended msg count (messages injected downstream via
        # appended_messages_override). Stored as INTEGER NOT NULL DEFAULT 0.
        "projector_appended_msg_count": record.projector_appended_msg_count,
 # actual content previews
        "projector_user_message_preview": record.projector_user_message_preview,
        "projector_tool_results_preview": _to_jsonb(list(record.projector_tool_results_preview)),
        "projector_meta": _to_jsonb(record.projector_meta),
        # Layer ②
        "assemble_entry_msg_count": record.assemble_entry_msg_count,
        "assemble_entry_roles": _to_jsonb(list(record.assemble_entry_roles)),
        "assemble_entry_head": _to_jsonb(list(record.assemble_entry_head)),
        "assemble_entry_tail": _to_jsonb(list(record.assemble_entry_tail)),
        "assemble_entry_meta_keys": _to_jsonb(list(record.assemble_entry_meta_keys)),
        "assemble_entry_meta_values_hash": record.assemble_entry_meta_values_hash,
        # Layer ③
        "pipeline_out_msg_count": record.pipeline_out_msg_count,
        "pipeline_out_roles": _to_jsonb(list(record.pipeline_out_roles)),
        "pipeline_out_head": _to_jsonb(list(record.pipeline_out_head)),
        "pipeline_out_tail": _to_jsonb(list(record.pipeline_out_tail)),
        "pipeline_out_budget_summary": _to_jsonb(record.pipeline_out_budget_summary),
        "pipeline_out_compressors_used": _to_jsonb(list(record.pipeline_out_compressors_used)),
        "pipeline_out_compaction_happened": record.pipeline_out_compaction_happened,
        "pipeline_out_compaction_summary": record.pipeline_out_compaction_summary,
        # Layer ④
        "packer_final_msg_count": record.packer_final_msg_count,
        "packer_final_roles": _to_jsonb(list(record.packer_final_roles)),
        "packer_final_head": _to_jsonb(list(record.packer_final_head)),
        "packer_final_tail": _to_jsonb(list(record.packer_final_tail)),
        "packer_final_tokens": record.packer_final_tokens,
        "packer_final_model": record.packer_final_model,
        "packer_final_system_prompt_len": record.packer_final_system_prompt_len,
        "packer_final_system_prompt_sha1_16": record.packer_final_system_prompt_sha1_16,
        "packer_final_system_prompt_text": system_prompt_text,
        "packer_final_system_prompt_budget": record.packer_final_system_prompt_budget,
        "packer_final_tool_schema_total_chars": record.packer_final_tool_schema_total_chars,
        "packer_final_response_format_text": record.packer_final_response_format_text,
        "packer_final_tool_schema_text": record.packer_final_tool_schema_text,
        # LLM response
        "llm_provider": record.llm_provider,
        "llm_model": record.llm_model,
        "finish_reason": record.finish_reason,
        "usage_input_tokens": record.usage_input_tokens,
        "usage_output_tokens": record.usage_output_tokens,
        "usage_cache_read_tokens": record.usage_cache_read_tokens,
        "usage_cache_write_tokens": record.usage_cache_write_tokens,
        "usage_reasoning_tokens": record.usage_reasoning_tokens,
        "ttft_ms": record.ttft_ms,
        "total_ms": record.total_ms,
        "assistant_text_preview": record.assistant_text_preview,
        "tool_calls_summary": _to_jsonb(list(record.tool_calls_summary)),
        # Error
        "error": record.error,
        "error_stack": record.error_stack,
    }


def _parse_jsonb(val: Any) -> Any:
    """Parse a JSONB column value back to Python object.

    asyncpg may return JSONB as:
    - Python list/dict (if asyncpg codec is registered)
    - JSON string (if using raw text() queries without codec)
    This helper handles both cases safely.
    """
    if val is None:
        return None
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val
    return val


def _row_to_record(row: dict[str, Any]) -> RequestAuditRecord:
    """Convert a PostgreSQL row dict back to a RequestAuditRecord."""
    started_ms = int(row["started_at_ms"] or 0)
    completed_ms = row["completed_at_ms"]
    if completed_ms is not None and completed_ms < started_ms:
        # wall-clock 回拨可能造成倒序；拒收整条会丢失审计节点，读取时归零。
        completed_ms = started_ms
    return RequestAuditRecord(
        schema_version=1,
        session_id=row["session_id"],
        iteration=row["iteration"],
        turn=row["turn"],
        started_at_ms=started_ms,
        completed_at_ms=completed_ms,
        status=row["status"],
        # Layer 0.5 (JSONB → parse to list)
        projector_input_order_count=row["projector_input_order_count"],
        projector_input_entry_types=tuple(_parse_jsonb(row["projector_input_entry_types"]) or []),
        projector_input_head_seq=tuple(_parse_jsonb(row["projector_input_head_seq"]) or []),
        projector_input_tail_seq=tuple(_parse_jsonb(row["projector_input_tail_seq"]) or []),
        projector_input_tail_types=tuple(_parse_jsonb(row["projector_input_tail_types"]) or []),
        # Layer ①
        projector_msg_count=row["projector_msg_count"],
        projector_msg_roles=tuple(_parse_jsonb(row["projector_msg_roles"]) or []),
        projector_head=tuple(_parse_jsonb(row["projector_head"]) or []),
        projector_tail=tuple(_parse_jsonb(row["projector_tail"]) or []),
        projector_appended_msg_count=row["projector_appended_msg_count"] or 0,
        projector_user_message_preview=row["projector_user_message_preview"],
        projector_tool_results_preview=tuple(_parse_jsonb(row["projector_tool_results_preview"]) or []),
        projector_meta=_parse_jsonb(row["projector_meta"]) or {},
        # Layer ②
        assemble_entry_msg_count=row["assemble_entry_msg_count"],
        assemble_entry_roles=tuple(_parse_jsonb(row["assemble_entry_roles"]) or []),
        assemble_entry_head=tuple(_parse_jsonb(row["assemble_entry_head"]) or []),
        assemble_entry_tail=tuple(_parse_jsonb(row["assemble_entry_tail"]) or []),
        assemble_entry_meta_keys=tuple(_parse_jsonb(row["assemble_entry_meta_keys"]) or []),
        assemble_entry_meta_values_hash=row["assemble_entry_meta_values_hash"],
        # Layer ③
        pipeline_out_msg_count=row["pipeline_out_msg_count"],
        pipeline_out_roles=tuple(_parse_jsonb(row["pipeline_out_roles"]) or []),
        pipeline_out_head=tuple(_parse_jsonb(row["pipeline_out_head"]) or []),
        pipeline_out_tail=tuple(_parse_jsonb(row["pipeline_out_tail"]) or []),
        pipeline_out_budget_summary=_parse_jsonb(row["pipeline_out_budget_summary"]) or {},
        pipeline_out_compressors_used=tuple(_parse_jsonb(row["pipeline_out_compressors_used"]) or []),
        pipeline_out_compaction_happened=row["pipeline_out_compaction_happened"],
        pipeline_out_compaction_summary=row["pipeline_out_compaction_summary"],
        # Layer ④
        packer_final_msg_count=row["packer_final_msg_count"],
        packer_final_roles=tuple(_parse_jsonb(row["packer_final_roles"]) or []),
        packer_final_head=tuple(_parse_jsonb(row["packer_final_head"]) or []),
        packer_final_tail=tuple(_parse_jsonb(row["packer_final_tail"]) or []),
        packer_final_tokens=row["packer_final_tokens"],
        packer_final_model=row["packer_final_model"],
        packer_final_system_prompt_len=row["packer_final_system_prompt_len"],
        packer_final_system_prompt_sha1_16=row["packer_final_system_prompt_sha1_16"],
        packer_final_system_prompt_text=row["packer_final_system_prompt_text"],
        packer_final_system_prompt_budget=row["packer_final_system_prompt_budget"],
        packer_final_tool_schema_total_chars=row["packer_final_tool_schema_total_chars"],
        packer_final_response_format_text=row["packer_final_response_format_text"],
        packer_final_tool_schema_text=row["packer_final_tool_schema_text"],
        # LLM response
        llm_provider=row["llm_provider"],
        llm_model=row["llm_model"],
        finish_reason=row["finish_reason"],
        usage_input_tokens=row["usage_input_tokens"],
        usage_output_tokens=row["usage_output_tokens"],
        usage_cache_read_tokens=row["usage_cache_read_tokens"],
        usage_cache_write_tokens=row["usage_cache_write_tokens"],
        usage_reasoning_tokens=row["usage_reasoning_tokens"],
        ttft_ms=row["ttft_ms"],
        total_ms=row["total_ms"],
        assistant_text_preview=row["assistant_text_preview"],
        tool_calls_summary=tuple(_parse_jsonb(row["tool_calls_summary"]) or []),
        # Error
        error=row["error"],
        error_stack=row["error_stack"],
    )


# ---------------------------------------------------------------------------
# Flush (state → PostgreSQL)
# ---------------------------------------------------------------------------

async def flush_state_to_pg(
    state: TrajectoryProjectionState,
    session_factory: async_sessionmaker[AsyncSession] | None,
    *,
    only_if_dirty: bool = True,
) -> bool:
    """Persist state → PostgreSQL. Returns True if actually written.

    Incremental strategy (optimized for async batch flushing):
      1. Query existing (iteration, started_at_ms) keys for this session
      2. UPSERT only records that are new or changed via ON CONFLICT
      3. Update session metadata (always upsert, lightweight)

    This avoids O(n) DELETE+INSERT on every flush and scales better
    with growing session history.
    """
    if session_factory is None:
        logger.debug("trajectory flush: no session_factory, skipping")
        return False
    if only_if_dirty and not state.is_dirty():
        return False

    sid = state.session_id
    if not sid:
        return False

    try:
        async with session_factory() as session, session.begin():
            # Step 1: UPSERT session metadata FIRST (must exist before
            # inserting records due to foreign key constraint)
            await session.execute(
                text(sql_models.UPSERT_SESSION_META),
                {
                    "session_id": sid,
                    "record_count": len(state.records),
                    "turn_count_estimate": len(set(
                        r.turn for r in state.records if r.turn is not None
                    )),
                    "gap_count": state.gap_count,
                    "gap_ratio": 0.0,
                    "orphan_count": state.orphan_count,
                    "gap_warning": state.gap_count > 0,
                    "zstd_compressed_record_count": 0,
                    "last_updated_ms": int(time.time() * 1000),
                },
            )

            # Step 2: Get existing keys to identify new vs updated records
            existing_keys: set[tuple[int, int]] = set()
            if state.records:
                keys_result = await session.execute(
                    text(sql_models.SELECT_EXISTING_RECORD_KEYS),
                    {"session_id": sid},
                )
                for row in keys_result:
                    existing_keys.add((row[0], row[1]))

            # Step 3: UPSERT records (new + updated). Both paths use the
            # ON CONFLICT upsert — the existing_keys probe only drives
            # the inserted/updated counters, so a cross-process writer
            # can no longer blow up the whole flush batch on a unique
            # constraint violation.
            inserted_count = 0
            updated_count = 0
            for record in state.records:
                key = (record.iteration, record.started_at_ms)
                params = _record_to_params(record)
                await session.execute(
                    text(sql_models.INSERT_RECORD_UPSERT),
                    params,
                )
                if key not in existing_keys:
                    inserted_count += 1
                else:
                    updated_count += 1

        state.mark_clean()
        logger.info(
            "trajectory flush_to_pg: sid=%s total=%d inserted=%d updated=%d",
            sid, len(state.records), inserted_count, updated_count,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "trajectory flush_state_to_pg failed sid=%s: %s", sid, exc,
        )
        return False


# ---------------------------------------------------------------------------
# Load (PostgreSQL → state)
# ---------------------------------------------------------------------------

async def load_state_from_pg(
    session_id: str,
    session_factory: async_sessionmaker[AsyncSession] | None,
    *,
    state_initializer: Callable[[str], TrajectoryProjectionState] | None = None,
) -> TrajectoryProjectionState:
    """Load session state from PostgreSQL.

    Returns state_initializer(session_id) if no data or session_factory is None.
    NEVER raises.
    """
    if state_initializer is None:
        raise ValueError("state_initializer is required — callers must provide init_state from engine.trajectory.projection")

    if session_factory is None:
        return state_initializer(session_id)

    try:
        async with session_factory() as session:
            # Load all records
            result = await session.execute(
                text(sql_models.SELECT_RECORDS_BY_SESSION),
                {"session_id": session_id, "limit": 1000, "offset": 0},
            )
            rows = [dict(row) for row in result.mappings()]

            if not rows:
                return state_initializer(session_id)

            # Convert rows to records
            records = []
            for row in rows:
                try:
                    records.append(_row_to_record(row))
                except Exception as exc:
                    logger.warning(
                        "trajectory load_state_from_pg: failed to parse record "
                        "id=%s: %s", row.get("id", "?"), exc,
                    )

            # Load session meta
            meta_result = await session.execute(
                text(sql_models.SELECT_SESSION_META),
                {"session_id": session_id},
            )
            meta_row = dict(meta_result.mappings().first() or {})

            # Build state
            state = state_initializer(session_id)
            state.records = records
            state.turn_counter = meta_row.get("last_turn") or max(
                (r.turn for r in records if r.turn is not None),
                default=None,
            )
            state.last_iter = max(
                (r.iteration for r in records),
                default=None,
            )
            state.gap_count = meta_row.get("gap_count", 0)
            state.orphan_count = meta_row.get("orphan_count", 0)
            state.mark_clean()
            return state

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "trajectory load_state_from_pg failed sid=%s: %s",
            session_id, exc,
        )
        return state_initializer(session_id)


# ---------------------------------------------------------------------------
# Read snapshot (PostgreSQL → TrajectorySnapshot)
# ---------------------------------------------------------------------------

def _empty_snapshot(
    session_id: str,
    parse_warning: str | None = None,
) -> TrajectorySnapshot:
    """Build an empty TrajectorySnapshot with optional _parse_warning hint."""
    snap = TrajectorySnapshot(
        session_id=session_id,
        records=[],
        meta=TrajectorySnapshotMeta(
            record_count=0, turn_count_estimate=0,
            gap_count=0, gap_ratio=0.0, orphan_count=0,
            gap_warning=False, zstd_compressed_record_count=0,
        ),
    )
    object.__setattr__(snap, "_parse_warning", parse_warning)
    return snap


async def read_snapshot_v2_pg(
    session_id: str,
    session_factory: async_sessionmaker[AsyncSession] | None,
    *,
    limit: int | None = None,
    ascending: bool = True,
    state_initializer: Callable[[str], Any] | None = None,
    view_fn: Callable[..., TrajectorySnapshot] | None = None,
) -> TrajectorySnapshot:
    """Read trajectory snapshot from PostgreSQL.

    Returns TrajectorySnapshot with optional _parse_warning on failure.
    NEVER raises.
    """
    if not session_id:
        return _empty_snapshot("", parse_warning="empty session_id")
    if session_factory is None:
        return _empty_snapshot(
            session_id,
            parse_warning="session_factory=None at read_snapshot_v2_pg",
        )

    try:
        state = await load_state_from_pg(
            session_id, session_factory, state_initializer=state_initializer,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "trajectory read_snapshot_v2_pg: load_state failed sid=%s: %s",
            session_id, exc,
        )
        return _empty_snapshot(
            session_id,
            parse_warning=f"load_state failed: {type(exc).__name__}",
        )

    if not state.records:
        return _empty_snapshot(session_id, parse_warning=None)

    if view_fn is None:
        raise ValueError("view_fn is required — callers must provide view from engine.trajectory.projection")
    return view_fn(state, limit=limit, ascending=ascending)


# ============================================================================
# 对齐 per-conversation-event cell persistence
# ============================================================================

def _cell_to_params(cell: TrajectoryCell) -> dict[str, Any]:
    """Serialize a TrajectoryCell to SQL params."""
    return {
        "cell_id": cell.cell_id,
        "session_id": cell.session_id,
        "entry_id": cell.entry_id,
        "seq": cell.seq,
        "turn": cell.turn,
        "kind": cell.kind,
        "role": cell.role,
        "content_preview": cell.content_preview,
        "tool_calls_summary": _to_jsonb(list(cell.tool_calls_summary)),
        "tool_call_id": cell.tool_call_id,
        "tool_name": cell.tool_name,
 # full-content + metric fields
        "input_detail": cell.input_detail,
        "output_detail": cell.output_detail,
        "thinking_detail": cell.thinking_detail,
        "model": cell.model,
        "finish_reason": cell.finish_reason,
        "usage_input_tokens": cell.usage_input_tokens,
        "usage_output_tokens": cell.usage_output_tokens,
        "usage_cache_read_tokens": cell.usage_cache_read_tokens,
        "usage_cache_write_tokens": cell.usage_cache_write_tokens,
        "usage_reasoning_tokens": cell.usage_reasoning_tokens,
        "ttft_ms": cell.ttft_ms,
        "decoding_ms": cell.decoding_ms,
        "is_error": cell.is_error,
        "schema_detail": cell.schema_detail,
        "started_at_ms": cell.started_at_ms,
        "completed_at_ms": cell.completed_at_ms,
        "iteration": cell.iteration,
        "error": cell.error,
    }


def _row_to_cell(row: Any) -> TrajectoryCell:
    """Deserialize a DB row back to a TrajectoryCell."""
    return TrajectoryCell(
        cell_id=str(row["cell_id"]),
        session_id=str(row["session_id"]),
        entry_id=str(row["entry_id"]),
        seq=int(row["seq"]),
        turn=int(row["turn"]),
        kind=str(row["kind"]),
        role=str(row["role"]),
        content_preview=row["content_preview"],
        tool_calls_summary=tuple(_parse_jsonb(row["tool_calls_summary"]) or []),
        tool_call_id=row["tool_call_id"],
        tool_name=row["tool_name"],
        input_detail=row["input_detail"],
        output_detail=row["output_detail"],
        thinking_detail=row["thinking_detail"],
        model=row["model"],
        finish_reason=row["finish_reason"],
        usage_input_tokens=row["usage_input_tokens"],
        usage_output_tokens=row["usage_output_tokens"],
        usage_cache_read_tokens=row["usage_cache_read_tokens"],
        usage_cache_write_tokens=row["usage_cache_write_tokens"],
        usage_reasoning_tokens=row["usage_reasoning_tokens"],
        ttft_ms=row["ttft_ms"],
        decoding_ms=row["decoding_ms"],
        is_error=row["is_error"],
        schema_detail=row["schema_detail"],
        started_at_ms=int(row["started_at_ms"]),
        completed_at_ms=row["completed_at_ms"],
        iteration=row["iteration"],
        error=row["error"],
    )


async def insert_cell_pg(
    session_factory: async_sessionmaker[AsyncSession],
    cell: TrajectoryCell,
) -> None:
    """Upsert a single TrajectoryCell.

    The ConversationNodeAdded event carries the COMPLETE cell payload
    (detail texts, LLM metrics, time range, iteration) — a single INSERT
    writes the whole row. There is no secondary metrics-UPDATE path.
    """
    params = _cell_to_params(cell)
    async with session_factory() as session, session.begin():
        await session.execute(text(sql_models.INSERT_CELL), params)


async def mark_or_synthesize_error_pg(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str,
    turn: int,
    iteration: int | None,
    error_code: str,
    error_message: str,
    model: str | None,
    ttft_ms: float | None,
    total_ms: float | None,
    failed_at_ms: int,
) -> None:
    """Mark a failed LLM iteration's assistant cell, or synthesize one.

    Failure paths may already have persisted the live assistant cell during
    streaming, so a partial cell usually exists — UPDATE it. When no partial
    output was produced (e.g. idle timeout before the first token), no
    cell exists — synthesize one so the failure is visible on the
    timeline. UPDATE-then-conditionally-INSERT in ONE task/transaction
    avoids the double-cell defect of the naive always-INSERT approach.
    """
    import uuid as _uuid
    from typing import cast as _cast

    from sqlalchemy.engine import CursorResult as _CursorResult

    try:
        async with session_factory() as session, session.begin():
            result = _cast(
                _CursorResult,
                await session.execute(
                    text(sql_models.MARK_CELL_ERROR),
                    {
                        "session_id": session_id,
                        "turn": turn,
                        "iteration": iteration,
                        "error": error_code,
                        "model": model,
                        "completed_at_ms": failed_at_ms,
                    },
                ),
            )
            if result.rowcount == 0:
                logger.info(
                    "trajectory: no partial cell for sid=%s turn=%s "
                    "iter=%s — synthesizing error cell",
                    session_id, turn, iteration,
                )
                await session.execute(
                    text(sql_models.INSERT_ERROR_CELL),
                    {
                        "cell_id": f"cell_{_uuid.uuid4().hex}",
                        "session_id": session_id,
                        # deterministic entry_id → retry-idempotent
                        # via the (session_id, entry_id) unique key
                        "entry_id": f"llm_error_{turn}_{iteration}",
                        "turn": turn,
                        "content_preview": (
                            f"LLM request failed: {error_message[:200]}"
                        ),
                        "output_detail": error_message,
                        "model": model,
                        "ttft_ms": ttft_ms,
                        "started_at_ms": failed_at_ms - int(total_ms or 0),
                        "completed_at_ms": failed_at_ms,
                        "iteration": iteration,
                        "error": error_code,
                    },
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "trajectory mark_or_synthesize_error_pg failed sid=%s turn=%s "
            "iter=%s: %s",
            session_id, turn, iteration, exc,
        )


async def insert_system_cell_pg(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    session_id: str,
    turn: int,
    iteration: int,
    sha1_16: str,
    full_text: str,
) -> None:
    """Insert a kind='system' cell snapshotting the system prompt.

    Called by the trajectory service only when the prompt sha changes
    across turns. entry_id is deterministic per turn → retry-idempotent;
    seq is computed atomically (MAX+1) inside the statement.
    """
    import uuid as _uuid

    now_ms = int(time.time() * 1000)
    try:
        async with session_factory() as session, session.begin():
            await session.execute(
                text(sql_models.INSERT_SYSTEM_CELL),
                {
                    "cell_id": f"cell_{_uuid.uuid4().hex}",
                    "session_id": session_id,
                    "entry_id": f"system_{turn}",
                    "turn": turn,
                    "content_preview": (
                        f"System prompt sha1={sha1_16} ({len(full_text)} chars)"
                    ),
                    "input_detail": full_text,
                    "iteration": iteration,
                    "started_at_ms": now_ms,
                    "completed_at_ms": now_ms,
                },
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "trajectory insert_system_cell_pg failed sid=%s turn=%s: %s",
            session_id, turn, exc,
        )


async def read_cells_pg(
    session_factory: async_sessionmaker[AsyncSession],
    session_id: str,
    *,
    after_seq: int = 0,
) -> list[TrajectoryCell]:
    """Read trajectory cells for a session, ordered by seq.

    With ``after_seq > 0`` returns only rows newer than the cursor (the
    incremental poll set). Cells are written complete in one INSERT —
    there is no post-insert enrichment to re-read.
    """
    incremental = after_seq > 0
    statement = (
        sql_models.SELECT_CELLS_INCREMENTAL if incremental
        else sql_models.SELECT_CELLS_BY_SESSION
    )
    params: dict[str, object] = {"session_id": session_id}
    if incremental:
        params["after_seq"] = after_seq
    async with session_factory() as session:
        result = await session.execute(text(statement), params)
        rows = result.mappings().all()
    return [_row_to_cell(r) for r in rows]


# ---------------------------------------------------------------------------
# TrajectoryPersistence Protocol implementation
# ---------------------------------------------------------------------------

class PgTrajectoryPersistence:
    """PostgreSQL-backed implementation of TrajectoryPersistence.

    Encapsulates ``session_factory`` so that callers (engine layer) never
    need to import persistence or manage SQLAlchemy sessions directly.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        state_initializer: Callable[[str], TrajectoryProjectionState] | None = None,
        view_fn: Callable[..., Any] | None = None,
    ) -> None:
        self._sf = session_factory
        self._init = state_initializer
        self._view = view_fn

    async def init_schema(self) -> None:
        await init_pg_schema(self._sf)

    async def flush_state(
        self,
        state: TrajectoryProjectionState,
        *,
        only_if_dirty: bool = True,
    ) -> bool:
        return await flush_state_to_pg(state, self._sf, only_if_dirty=only_if_dirty)

    async def load_state(
        self,
        session_id: str,
        *,
        state_initializer: Callable[[str], TrajectoryProjectionState] | None = None,
    ) -> TrajectoryProjectionState:
        init = state_initializer or self._init
        return await load_state_from_pg(session_id, self._sf, state_initializer=init)

    async def insert_cell(self, cell: TrajectoryCell) -> None:
        await insert_cell_pg(self._sf, cell)

    async def insert_system_cell(
        self,
        *,
        session_id: str,
        turn: int,
        iteration: int,
        sha1_16: str,
        full_text: str,
    ) -> None:
        await insert_system_cell_pg(
            self._sf,
            session_id=session_id,
            turn=turn,
            iteration=iteration,
            sha1_16=sha1_16,
            full_text=full_text,
        )

    async def mark_or_synthesize_error(
        self,
        *,
        session_id: str,
        turn: int,
        iteration: int | None,
        error_code: str,
        error_message: str,
        model: str | None,
        ttft_ms: float | None,
        total_ms: float | None,
        failed_at_ms: int,
    ) -> None:
        await mark_or_synthesize_error_pg(
            self._sf,
            session_id=session_id,
            turn=turn,
            iteration=iteration,
            error_code=error_code,
            error_message=error_message,
            model=model,
            ttft_ms=ttft_ms,
            total_ms=total_ms,
            failed_at_ms=failed_at_ms,
        )

    async def read_snapshot(
        self,
        session_id: str,
        *,
        limit: int | None = None,
        ascending: bool = True,
    ) -> TrajectorySnapshot:
        return await read_snapshot_v2_pg(
            session_id, self._sf, limit=limit, ascending=ascending,
            view_fn=self._view, state_initializer=self._init,
        )

    async def read_cells(
        self,
        session_id: str,
        *,
        after_seq: int = 0,
    ) -> list[TrajectoryCell]:
        return await read_cells_pg(self._sf, session_id, after_seq=after_seq)


__all__ = [
    "init_pg_schema",
    "flush_state_to_pg",
    "load_state_from_pg",
    "read_snapshot_v2_pg",
    "insert_cell_pg",
    "mark_or_synthesize_error_pg",
    "insert_system_cell_pg",
    "read_cells_pg",
    "PgTrajectoryPersistence",
]
