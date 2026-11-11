from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from prismclaw.schema.dream.dream_types import DreamSessionState


_DDL = """
CREATE TABLE IF NOT EXISTS pc_dream_states (
    session_key                       TEXT PRIMARY KEY,
    dream_pending                     BOOLEAN NOT NULL DEFAULT FALSE,
    memory_dirty_score                INTEGER NOT NULL DEFAULT 0,
    last_activity_at                  DOUBLE PRECISION NOT NULL DEFAULT 0,
    last_light_at                     DOUBLE PRECISION,
    last_deep_at                      DOUBLE PRECISION,
    last_rem_at                       DOUBLE PRECISION,
    dream_batch_id                    TEXT,
    dream_lock_owner                  TEXT,
    light_pending                     BOOLEAN NOT NULL DEFAULT FALSE,
    nightly_pending                   BOOLEAN NOT NULL DEFAULT FALSE,
    last_nightly_window_started_at    DOUBLE PRECISION,
    compile_cursor                    TEXT,
    last_compile_started_at           DOUBLE PRECISION,
    last_compile_finished_at          DOUBLE PRECISION,
    publish_watermark_at              DOUBLE PRECISION,
    last_compiled_candidate_count     INTEGER NOT NULL DEFAULT 0,
    last_published_memory_count       INTEGER NOT NULL DEFAULT 0,
    last_gate_rejected_entries        INTEGER NOT NULL DEFAULT 0,
    last_compile_status               TEXT,
    last_enqueue_activity_at          DOUBLE PRECISION,
    last_enqueue_activity_status      TEXT,
    last_enqueue_activity_detail      TEXT,
    last_enqueue_nightly_at           DOUBLE PRECISION,
    last_enqueue_nightly_status       TEXT,
    last_enqueue_nightly_detail       TEXT
)
"""

_SELECT_COLUMNS = """
    session_key, dream_pending, memory_dirty_score, last_activity_at,
    last_light_at, last_deep_at, last_rem_at, dream_batch_id, dream_lock_owner,
    light_pending, nightly_pending, last_nightly_window_started_at,
    compile_cursor, last_compile_started_at, last_compile_finished_at,
    publish_watermark_at, last_compiled_candidate_count,
    last_published_memory_count, last_gate_rejected_entries, last_compile_status,
    last_enqueue_activity_at, last_enqueue_activity_status, last_enqueue_activity_detail,
    last_enqueue_nightly_at, last_enqueue_nightly_status, last_enqueue_nightly_detail
"""

_UPSERT = """
INSERT INTO pc_dream_states (
    session_key, dream_pending, memory_dirty_score, last_activity_at,
    last_light_at, last_deep_at, last_rem_at, dream_batch_id, dream_lock_owner,
    light_pending, nightly_pending, last_nightly_window_started_at,
    compile_cursor, last_compile_started_at, last_compile_finished_at,
    publish_watermark_at, last_compiled_candidate_count,
    last_published_memory_count, last_gate_rejected_entries, last_compile_status,
    last_enqueue_activity_at, last_enqueue_activity_status, last_enqueue_activity_detail,
    last_enqueue_nightly_at, last_enqueue_nightly_status, last_enqueue_nightly_detail
) VALUES (
    :session_key, :dream_pending, :memory_dirty_score, :last_activity_at,
    :last_light_at, :last_deep_at, :last_rem_at, :dream_batch_id, :dream_lock_owner,
    :light_pending, :nightly_pending, :last_nightly_window_started_at,
    :compile_cursor, :last_compile_started_at, :last_compile_finished_at,
    :publish_watermark_at, :last_compiled_candidate_count,
    :last_published_memory_count, :last_gate_rejected_entries, :last_compile_status,
    :last_enqueue_activity_at, :last_enqueue_activity_status, :last_enqueue_activity_detail,
    :last_enqueue_nightly_at, :last_enqueue_nightly_status, :last_enqueue_nightly_detail
)
ON CONFLICT (session_key) DO UPDATE SET
    dream_pending = EXCLUDED.dream_pending,
    memory_dirty_score = EXCLUDED.memory_dirty_score,
    last_activity_at = EXCLUDED.last_activity_at,
    last_light_at = EXCLUDED.last_light_at,
    last_deep_at = EXCLUDED.last_deep_at,
    last_rem_at = EXCLUDED.last_rem_at,
    dream_batch_id = EXCLUDED.dream_batch_id,
    dream_lock_owner = EXCLUDED.dream_lock_owner,
    light_pending = EXCLUDED.light_pending,
    nightly_pending = EXCLUDED.nightly_pending,
    last_nightly_window_started_at = EXCLUDED.last_nightly_window_started_at,
    compile_cursor = EXCLUDED.compile_cursor,
    last_compile_started_at = EXCLUDED.last_compile_started_at,
    last_compile_finished_at = EXCLUDED.last_compile_finished_at,
    publish_watermark_at = EXCLUDED.publish_watermark_at,
    last_compiled_candidate_count = EXCLUDED.last_compiled_candidate_count,
    last_published_memory_count = EXCLUDED.last_published_memory_count,
    last_gate_rejected_entries = EXCLUDED.last_gate_rejected_entries,
    last_compile_status = EXCLUDED.last_compile_status,
    last_enqueue_activity_at = EXCLUDED.last_enqueue_activity_at,
    last_enqueue_activity_status = EXCLUDED.last_enqueue_activity_status,
    last_enqueue_activity_detail = EXCLUDED.last_enqueue_activity_detail,
    last_enqueue_nightly_at = EXCLUDED.last_enqueue_nightly_at,
    last_enqueue_nightly_status = EXCLUDED.last_enqueue_nightly_status,
    last_enqueue_nightly_detail = EXCLUDED.last_enqueue_nightly_detail
"""


class PostgresDreamStateStore:
    """PostgreSQL-backed Dream state store using a session factory."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory
        self._migrated = False
        self._init_lock = asyncio.Lock()

    async def _init_schema(self) -> None:
        if self._migrated:
            return
        async with self._init_lock:
            if self._migrated:
                return
            async with self._sf() as session:
                conn = await session.connection()
                await conn.execute(text(_DDL))
                await session.commit()
            self._migrated = True

    async def get(self, session_key: str) -> DreamSessionState | None:
        await self._init_schema()
        async with self._sf() as session:
            result = await session.execute(
                text(f"SELECT {_SELECT_COLUMNS} FROM pc_dream_states WHERE session_key = :session_key"),
                {"session_key": session_key},
            )
            row = result.mappings().first()
            if row is None:
                return None
            return _row_to_state(row)

    async def put(self, state: DreamSessionState) -> None:
        await self._init_schema()
        async with self._sf() as session:
            await session.execute(text(_UPSERT), _state_to_params(state))
            await session.commit()

    async def list_states(self) -> list[DreamSessionState]:
        await self._init_schema()
        async with self._sf() as session:
            result = await session.execute(
                text(f"SELECT {_SELECT_COLUMNS} FROM pc_dream_states")
            )
            return [_row_to_state(row) for row in result.mappings()]


def _row_to_state(row) -> DreamSessionState:
    return DreamSessionState(
        session_key=row["session_key"],
        dream_pending=bool(row["dream_pending"]),
        memory_dirty_score=int(row["memory_dirty_score"] or 0),
        last_activity_at=float(row["last_activity_at"] or 0.0),
        last_light_at=_maybe_float(row["last_light_at"]),
        last_deep_at=_maybe_float(row["last_deep_at"]),
        last_rem_at=_maybe_float(row["last_rem_at"]),
        dream_batch_id=_maybe_str(row["dream_batch_id"]),
        dream_lock_owner=_maybe_str(row["dream_lock_owner"]),
        light_pending=bool(row["light_pending"]),
        nightly_pending=bool(row["nightly_pending"]),
        last_nightly_window_started_at=_maybe_float(row["last_nightly_window_started_at"]),
        compile_cursor=_maybe_str(row["compile_cursor"]),
        last_compile_started_at=_maybe_float(row["last_compile_started_at"]),
        last_compile_finished_at=_maybe_float(row["last_compile_finished_at"]),
        publish_watermark_at=_maybe_float(row["publish_watermark_at"]),
        last_compiled_candidate_count=int(row["last_compiled_candidate_count"] or 0),
        last_published_memory_count=int(row["last_published_memory_count"] or 0),
        last_gate_rejected_entries=int(row["last_gate_rejected_entries"] or 0),
        last_compile_status=_maybe_str(row["last_compile_status"]),
        last_enqueue_activity_at=_maybe_float(row["last_enqueue_activity_at"]),
        last_enqueue_activity_status=_maybe_str(row["last_enqueue_activity_status"]),
        last_enqueue_activity_detail=_maybe_str(row["last_enqueue_activity_detail"]),
        last_enqueue_nightly_at=_maybe_float(row["last_enqueue_nightly_at"]),
        last_enqueue_nightly_status=_maybe_str(row["last_enqueue_nightly_status"]),
        last_enqueue_nightly_detail=_maybe_str(row["last_enqueue_nightly_detail"]),
    )


def _state_to_params(state: DreamSessionState) -> dict:
    return {
        "session_key": state.session_key,
        "dream_pending": state.dream_pending,
        "memory_dirty_score": state.memory_dirty_score,
        "last_activity_at": state.last_activity_at,
        "last_light_at": state.last_light_at,
        "last_deep_at": state.last_deep_at,
        "last_rem_at": state.last_rem_at,
        "dream_batch_id": state.dream_batch_id,
        "dream_lock_owner": state.dream_lock_owner,
        "light_pending": state.light_pending,
        "nightly_pending": state.nightly_pending,
        "last_nightly_window_started_at": state.last_nightly_window_started_at,
        "compile_cursor": state.compile_cursor,
        "last_compile_started_at": state.last_compile_started_at,
        "last_compile_finished_at": state.last_compile_finished_at,
        "publish_watermark_at": state.publish_watermark_at,
        "last_compiled_candidate_count": state.last_compiled_candidate_count,
        "last_published_memory_count": state.last_published_memory_count,
        "last_gate_rejected_entries": state.last_gate_rejected_entries,
        "last_compile_status": state.last_compile_status,
        "last_enqueue_activity_at": state.last_enqueue_activity_at,
        "last_enqueue_activity_status": state.last_enqueue_activity_status,
        "last_enqueue_activity_detail": state.last_enqueue_activity_detail,
        "last_enqueue_nightly_at": state.last_enqueue_nightly_at,
        "last_enqueue_nightly_status": state.last_enqueue_nightly_status,
        "last_enqueue_nightly_detail": state.last_enqueue_nightly_detail,
    }


def _maybe_float(value) -> float | None:
    if value is None:
        return None
    return float(value)


def _maybe_str(value) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
