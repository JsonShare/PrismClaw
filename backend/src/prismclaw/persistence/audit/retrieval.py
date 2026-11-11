from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from prismclaw.schema.audit.audit_types import RetrievalAuditEntry
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, AsyncSession

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RetrievalAuditSummary:
    total_requests: int
    total_duration_s: float
    total_memory_candidates: int
    total_archive_candidates: int
    total_memory_text_candidates: int
    total_memory_vector_candidates: int
    total_archive_text_candidates: int
    total_archive_vector_candidates: int
    total_selected_memories: int
    total_selected_archives: int
    memory_source_mix: dict[str, int]
    archive_source_mix: dict[str, int]
    selector_fallback_count: int
    planner_graph_expansion_count: int
    archive_cache_hit_count: int
    memory_vector_fallback_count: int
    archive_vector_fallback_count: int
    l2l3_error_count: int
    archive_error_count: int


_DDL = """
CREATE TABLE IF NOT EXISTS pc_retrieval_audit_entries (
    id                                  BIGSERIAL PRIMARY KEY,
    session_key                         TEXT NOT NULL,
    retrieval_query                     TEXT NOT NULL,
    started_at                          DOUBLE PRECISION NOT NULL,
    ended_at                            DOUBLE PRECISION NOT NULL,
    duration_s                          DOUBLE PRECISION NOT NULL,
    memory_candidate_count              INTEGER NOT NULL DEFAULT 0,
    archive_candidate_count             INTEGER NOT NULL DEFAULT 0,
    memory_text_candidate_count         INTEGER NOT NULL DEFAULT 0,
    memory_vector_candidate_count       INTEGER NOT NULL DEFAULT 0,
    archive_text_candidate_count        INTEGER NOT NULL DEFAULT 0,
    archive_vector_candidate_count      INTEGER NOT NULL DEFAULT 0,
    selected_memory_count               INTEGER NOT NULL DEFAULT 0,
    selected_archive_count              INTEGER NOT NULL DEFAULT 0,
    selected_layer_distribution_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
    memory_source_mix_json              JSONB NOT NULL DEFAULT '{}'::jsonb,
    archive_source_mix_json             JSONB NOT NULL DEFAULT '{}'::jsonb,
    selector_strategy                   TEXT NOT NULL DEFAULT 'unknown',
    selector_fallback                   BOOLEAN NOT NULL DEFAULT FALSE,
    planner_strategy                    TEXT NOT NULL DEFAULT 'unknown',
    planner_temporal_mode               TEXT NOT NULL DEFAULT 'unknown',
    planner_memory_layers_json          JSONB NOT NULL DEFAULT '[]'::jsonb,
    planner_artifact_kinds_json         JSONB NOT NULL DEFAULT '[]'::jsonb,
    planner_graph_expansion_enabled     BOOLEAN NOT NULL DEFAULT FALSE,
    archive_cache_hit                   BOOLEAN NOT NULL DEFAULT FALSE,
    memory_vector_fallback              BOOLEAN NOT NULL DEFAULT FALSE,
    archive_vector_fallback             BOOLEAN NOT NULL DEFAULT FALSE,
    l2l3_error                          BOOLEAN NOT NULL DEFAULT FALSE,
    archive_error                       BOOLEAN NOT NULL DEFAULT FALSE
)
"""

_INDEX = """
CREATE INDEX IF NOT EXISTS idx_pc_retrieval_audit_session
ON pc_retrieval_audit_entries (session_key, started_at DESC)
"""

_SELECT_COLUMNS = """
    session_key, retrieval_query, started_at, ended_at, duration_s,
    memory_candidate_count, archive_candidate_count,
    memory_text_candidate_count, memory_vector_candidate_count,
    archive_text_candidate_count, archive_vector_candidate_count,
    selected_memory_count, selected_archive_count,
    selected_layer_distribution_json, memory_source_mix_json, archive_source_mix_json,
    selector_strategy, selector_fallback,
    planner_strategy, planner_temporal_mode,
    planner_memory_layers_json, planner_artifact_kinds_json,
    planner_graph_expansion_enabled, archive_cache_hit,
    memory_vector_fallback, archive_vector_fallback,
    l2l3_error, archive_error
"""

_INSERT = """
INSERT INTO pc_retrieval_audit_entries (
    session_key, retrieval_query, started_at, ended_at, duration_s,
    memory_candidate_count, archive_candidate_count,
    memory_text_candidate_count, memory_vector_candidate_count,
    archive_text_candidate_count, archive_vector_candidate_count,
    selected_memory_count, selected_archive_count,
    selected_layer_distribution_json, memory_source_mix_json, archive_source_mix_json,
    selector_strategy, selector_fallback,
    planner_strategy, planner_temporal_mode,
    planner_memory_layers_json, planner_artifact_kinds_json,
    planner_graph_expansion_enabled, archive_cache_hit,
    memory_vector_fallback, archive_vector_fallback,
    l2l3_error, archive_error
) VALUES (
    :session_key, :retrieval_query, :started_at, :ended_at, :duration_s,
    :memory_candidate_count, :archive_candidate_count,
    :memory_text_candidate_count, :memory_vector_candidate_count,
    :archive_text_candidate_count, :archive_vector_candidate_count,
    :selected_memory_count, :selected_archive_count,
    CAST(:selected_layer_distribution_json AS jsonb),
    CAST(:memory_source_mix_json AS jsonb),
    CAST(:archive_source_mix_json AS jsonb),
    :selector_strategy, :selector_fallback,
    :planner_strategy, :planner_temporal_mode,
    CAST(:planner_memory_layers_json AS jsonb),
    CAST(:planner_artifact_kinds_json AS jsonb),
    :planner_graph_expansion_enabled, :archive_cache_hit,
    :memory_vector_fallback, :archive_vector_fallback,
    :l2l3_error, :archive_error
)
"""


async def init_schema(engine: AsyncEngine) -> None:
    """Create the retrieval audit table and index if they do not exist."""
    async with engine.begin() as conn:
        await conn.execute(text(_DDL))
        await conn.execute(text(_INDEX))


class PostgresRetrievalAuditLog:
    """PostgreSQL-backed retrieval audit log using a shared session_factory."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def record(self, entry: RetrievalAuditEntry) -> None:
        async with self._sf() as session:
            await session.execute(text(_INSERT), _entry_to_params(entry))
            await session.commit()

    async def list_entries(
        self,
        *,
        session_key: str | None = None,
    ) -> list[RetrievalAuditEntry]:
        params: dict[str, Any] = {}
        where_sql = ""
        if session_key is not None:
            where_sql = "WHERE session_key = :session_key"
            params["session_key"] = session_key
        sql = (
            f"SELECT {_SELECT_COLUMNS} FROM pc_retrieval_audit_entries {where_sql} "
            "ORDER BY started_at ASC, id ASC"
        )
        async with self._sf() as session:
            result = await session.execute(text(sql), params)
            return [_entry_from_row(row) for row in result.mappings()]

    async def summarize(
        self,
        *,
        session_key: str | None = None,
    ) -> RetrievalAuditSummary:
        entries = await self.list_entries(session_key=session_key)
        return RetrievalAuditSummary(
            total_requests=len(entries),
            total_duration_s=sum(entry.duration_s for entry in entries),
            total_memory_candidates=sum(entry.memory_candidate_count for entry in entries),
            total_archive_candidates=sum(entry.archive_candidate_count for entry in entries),
            total_memory_text_candidates=sum(entry.memory_text_candidate_count for entry in entries),
            total_memory_vector_candidates=sum(entry.memory_vector_candidate_count for entry in entries),
            total_archive_text_candidates=sum(entry.archive_text_candidate_count for entry in entries),
            total_archive_vector_candidates=sum(entry.archive_vector_candidate_count for entry in entries),
            total_selected_memories=sum(entry.selected_memory_count for entry in entries),
            total_selected_archives=sum(entry.selected_archive_count for entry in entries),
            memory_source_mix=_sum_distributions(entry.memory_source_mix for entry in entries),
            archive_source_mix=_sum_distributions(entry.archive_source_mix for entry in entries),
            selector_fallback_count=sum(1 for entry in entries if entry.selector_fallback),
            planner_graph_expansion_count=sum(
                1 for entry in entries if entry.planner_graph_expansion_enabled
            ),
            archive_cache_hit_count=sum(1 for entry in entries if entry.archive_cache_hit),
            memory_vector_fallback_count=sum(1 for entry in entries if entry.memory_vector_fallback),
            archive_vector_fallback_count=sum(1 for entry in entries if entry.archive_vector_fallback),
            l2l3_error_count=sum(1 for entry in entries if entry.l2l3_error),
            archive_error_count=sum(1 for entry in entries if entry.archive_error),
        )

    async def clear(self) -> None:
        async with self._sf() as session:
            await session.execute(text("DELETE FROM pc_retrieval_audit_entries"))
            await session.commit()


def _entry_to_params(entry: RetrievalAuditEntry) -> dict[str, Any]:
    return {
        "session_key": entry.session_key,
        "retrieval_query": entry.retrieval_query,
        "started_at": float(entry.started_at),
        "ended_at": float(entry.ended_at),
        "duration_s": float(entry.duration_s),
        "memory_candidate_count": int(entry.memory_candidate_count),
        "archive_candidate_count": int(entry.archive_candidate_count),
        "memory_text_candidate_count": int(entry.memory_text_candidate_count),
        "memory_vector_candidate_count": int(entry.memory_vector_candidate_count),
        "archive_text_candidate_count": int(entry.archive_text_candidate_count),
        "archive_vector_candidate_count": int(entry.archive_vector_candidate_count),
        "selected_memory_count": int(entry.selected_memory_count),
        "selected_archive_count": int(entry.selected_archive_count),
        "selected_layer_distribution_json": _dump_distribution(entry.selected_layer_distribution),
        "memory_source_mix_json": _dump_distribution(entry.memory_source_mix),
        "archive_source_mix_json": _dump_distribution(entry.archive_source_mix),
        "selector_strategy": entry.selector_strategy,
        "selector_fallback": bool(entry.selector_fallback),
        "planner_strategy": entry.planner_strategy,
        "planner_temporal_mode": entry.planner_temporal_mode,
        "planner_memory_layers_json": _dump_sequence(entry.planner_memory_layers),
        "planner_artifact_kinds_json": _dump_sequence(entry.planner_artifact_kinds),
        "planner_graph_expansion_enabled": bool(entry.planner_graph_expansion_enabled),
        "archive_cache_hit": bool(entry.archive_cache_hit),
        "memory_vector_fallback": bool(entry.memory_vector_fallback),
        "archive_vector_fallback": bool(entry.archive_vector_fallback),
        "l2l3_error": bool(entry.l2l3_error),
        "archive_error": bool(entry.archive_error),
    }


def _entry_from_row(row: Any) -> RetrievalAuditEntry:
    return RetrievalAuditEntry(
        session_key=str(row["session_key"]),
        retrieval_query=str(row["retrieval_query"]),
        started_at=float(row["started_at"]),
        ended_at=float(row["ended_at"]),
        duration_s=float(row["duration_s"]),
        memory_candidate_count=int(row["memory_candidate_count"] or 0),
        archive_candidate_count=int(row["archive_candidate_count"] or 0),
        memory_text_candidate_count=int(row["memory_text_candidate_count"] or 0),
        memory_vector_candidate_count=int(row["memory_vector_candidate_count"] or 0),
        archive_text_candidate_count=int(row["archive_text_candidate_count"] or 0),
        archive_vector_candidate_count=int(row["archive_vector_candidate_count"] or 0),
        selected_memory_count=int(row["selected_memory_count"] or 0),
        selected_archive_count=int(row["selected_archive_count"] or 0),
        selected_layer_distribution=_parse_distribution(row["selected_layer_distribution_json"]),
        memory_source_mix=_parse_distribution(row["memory_source_mix_json"]),
        archive_source_mix=_parse_distribution(row["archive_source_mix_json"]),
        selector_strategy=str(row["selector_strategy"] or "unknown"),
        selector_fallback=bool(row["selector_fallback"]),
        planner_strategy=str(row["planner_strategy"] or "unknown"),
        planner_temporal_mode=str(row["planner_temporal_mode"] or "unknown"),
        planner_memory_layers=_parse_sequence(row["planner_memory_layers_json"]),
        planner_artifact_kinds=_parse_sequence(row["planner_artifact_kinds_json"]),
        planner_graph_expansion_enabled=bool(row["planner_graph_expansion_enabled"]),
        archive_cache_hit=bool(row["archive_cache_hit"]),
        memory_vector_fallback=bool(row["memory_vector_fallback"]),
        archive_vector_fallback=bool(row["archive_vector_fallback"]),
        l2l3_error=bool(row["l2l3_error"]),
        archive_error=bool(row["archive_error"]),
    )


def _dump_distribution(distribution: dict[str, int]) -> str:
    return json.dumps(distribution, sort_keys=True)


def _parse_distribution(raw: object) -> dict[str, int]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        normalized: dict[str, int] = {}
        for key, value in raw.items():
            try:
                normalized[str(key)] = int(value)
            except Exception:
                continue
        return normalized
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except Exception:
            return {}
        if isinstance(payload, dict):
            normalized = {}
            for key, value in payload.items():
                try:
                    normalized[str(key)] = int(value)
                except Exception:
                    continue
            return normalized
    return {}


def _dump_sequence(values: list[str]) -> str:
    return json.dumps([str(item) for item in values], ensure_ascii=True)


def _parse_sequence(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item)]
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except Exception:
            return []
        if isinstance(payload, list):
            return [str(item) for item in payload if str(item)]
    return []


def _sum_distributions(distributions: object) -> dict[str, int]:
    merged: dict[str, int] = {}
    for distribution in distributions:
        if not isinstance(distribution, dict):
            continue
        for key, value in distribution.items():
            merged[str(key)] = merged.get(str(key), 0) + int(value)
    return merged
