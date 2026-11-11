from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from prismclaw.schema.dream.dream_types import DreamAuditEntry, DreamBatchHealthMetrics


_DDL = """
CREATE TABLE IF NOT EXISTS pc_dream_audit_entries (
    id                       BIGSERIAL PRIMARY KEY,
    session_key              TEXT NOT NULL,
    batch_id                 TEXT NOT NULL,
    phase                    TEXT NOT NULL,
    trigger                  TEXT NOT NULL,
    status                   TEXT NOT NULL,
    started_at               DOUBLE PRECISION NOT NULL,
    ended_at                 DOUBLE PRECISION NOT NULL,
    duration_s               DOUBLE PRECISION NOT NULL,
    promoted_count           INTEGER NOT NULL DEFAULT 0,
    deduplicated_count       INTEGER NOT NULL DEFAULT 0,
    archived_count           INTEGER NOT NULL DEFAULT 0,
    synthesized_count        INTEGER NOT NULL DEFAULT 0,
    compiled_candidates      INTEGER NOT NULL DEFAULT 0,
    extracted_candidates     INTEGER NOT NULL DEFAULT 0,
    published_candidates     INTEGER NOT NULL DEFAULT 0,
    discarded_candidates     INTEGER NOT NULL DEFAULT 0,
    merged_entries           INTEGER NOT NULL DEFAULT 0,
    temporal_updates         INTEGER NOT NULL DEFAULT 0,
    gate_rejected_entries    INTEGER NOT NULL DEFAULT 0,
    published_memory_count   INTEGER NOT NULL DEFAULT 0,
    llm_cost                 INTEGER NOT NULL DEFAULT 0,
    failure_reason           TEXT
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_pc_dream_audit_batch ON pc_dream_audit_entries(batch_id)",
    "CREATE INDEX IF NOT EXISTS idx_pc_dream_audit_session ON pc_dream_audit_entries(session_key)",
)

_SELECT_COLUMNS = """
    id, session_key, batch_id, phase, trigger, status, started_at, ended_at, duration_s,
    promoted_count, deduplicated_count, archived_count, synthesized_count,
    compiled_candidates, extracted_candidates, published_candidates, discarded_candidates,
    merged_entries, temporal_updates, gate_rejected_entries, published_memory_count,
    llm_cost, failure_reason
"""

_INSERT = """
INSERT INTO pc_dream_audit_entries (
    session_key, batch_id, phase, trigger, status, started_at, ended_at, duration_s,
    promoted_count, deduplicated_count, archived_count, synthesized_count,
    compiled_candidates, extracted_candidates, published_candidates, discarded_candidates,
    merged_entries, temporal_updates, gate_rejected_entries, published_memory_count,
    llm_cost, failure_reason
) VALUES (
    :session_key, :batch_id, :phase, :trigger, :status, :started_at, :ended_at, :duration_s,
    :promoted_count, :deduplicated_count, :archived_count, :synthesized_count,
    :compiled_candidates, :extracted_candidates, :published_candidates, :discarded_candidates,
    :merged_entries, :temporal_updates, :gate_rejected_entries, :published_memory_count,
    :llm_cost, :failure_reason
)
"""


class PostgresDreamAuditLog:
    """PostgreSQL-backed Dream audit log using a session factory."""

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
                for stmt in _INDEXES:
                    await conn.execute(text(stmt))
                await session.commit()
            self._migrated = True

    async def record(self, entry: DreamAuditEntry) -> None:
        await self._init_schema()
        async with self._sf() as session:
            await session.execute(text(_INSERT), _entry_to_params(entry))
            await session.commit()

    async def list_entries(
        self,
        *,
        session_key: str | None = None,
        batch_id: str | None = None,
        phase: str | None = None,
    ) -> list[DreamAuditEntry]:
        await self._init_schema()
        predicates: list[str] = []
        params: dict[str, object] = {}
        if session_key is not None:
            predicates.append("session_key = :session_key")
            params["session_key"] = session_key
        if batch_id is not None:
            predicates.append("batch_id = :batch_id")
            params["batch_id"] = batch_id
        if phase is not None:
            predicates.append("phase = :phase")
            params["phase"] = phase
        where_sql = f"WHERE {' AND '.join(predicates)}" if predicates else ""
        async with self._sf() as session:
            result = await session.execute(
                text(
                    f"SELECT {_SELECT_COLUMNS} FROM pc_dream_audit_entries "
                    f"{where_sql} ORDER BY started_at ASC, id ASC"
                ),
                params,
            )
            return [_row_to_entry(row) for row in result.mappings()]

    async def clear(self) -> None:
        await self._init_schema()
        async with self._sf() as session:
            await session.execute(text("DELETE FROM pc_dream_audit_entries"))
            await session.commit()

    async def summarize_batches(
        self,
        *,
        trigger: str | None = None,
        session_key: str | None = None,
    ) -> list[DreamBatchHealthMetrics]:
        entries = await self.list_entries(session_key=session_key)
        grouped: dict[str, list[DreamAuditEntry]] = {}
        for entry in entries:
            if trigger is not None and entry.trigger != trigger:
                continue
            grouped.setdefault(entry.batch_id, []).append(entry)
        metrics = [
            _summarize_batch(items)
            for _batch_id, items in grouped.items()
            if items
        ]
        return sorted(metrics, key=lambda item: item.started_at, reverse=True)

    async def latest_batch_metrics(
        self,
        *,
        trigger: str | None = None,
        session_key: str | None = None,
    ) -> DreamBatchHealthMetrics | None:
        metrics = await self.summarize_batches(trigger=trigger, session_key=session_key)
        return metrics[0] if metrics else None


def _row_to_entry(row) -> DreamAuditEntry:
    return DreamAuditEntry(
        session_key=str(row["session_key"]),
        batch_id=str(row["batch_id"]),
        phase=str(row["phase"]),
        trigger=str(row["trigger"]),
        status=str(row["status"]),  # type: ignore[arg-type]
        started_at=float(row["started_at"]),
        ended_at=float(row["ended_at"]),
        duration_s=float(row["duration_s"]),
        promoted_count=int(row["promoted_count"] or 0),
        deduplicated_count=int(row["deduplicated_count"] or 0),
        archived_count=int(row["archived_count"] or 0),
        synthesized_count=int(row["synthesized_count"] or 0),
        compiled_candidates=int(row["compiled_candidates"] or 0),
        extracted_candidates=int(row["extracted_candidates"] or 0),
        published_candidates=int(row["published_candidates"] or 0),
        discarded_candidates=int(row["discarded_candidates"] or 0),
        merged_entries=int(row["merged_entries"] or 0),
        temporal_updates=int(row["temporal_updates"] or 0),
        gate_rejected_entries=int(row["gate_rejected_entries"] or 0),
        published_memory_count=int(row["published_memory_count"] or 0),
        llm_cost=int(row["llm_cost"] or 0),
        failure_reason=_maybe_str(row["failure_reason"]),
    )


def _entry_to_params(entry: DreamAuditEntry) -> dict:
    return {
        "session_key": entry.session_key,
        "batch_id": entry.batch_id,
        "phase": entry.phase,
        "trigger": entry.trigger,
        "status": entry.status,
        "started_at": entry.started_at,
        "ended_at": entry.ended_at,
        "duration_s": entry.duration_s,
        "promoted_count": entry.promoted_count,
        "deduplicated_count": entry.deduplicated_count,
        "archived_count": entry.archived_count,
        "synthesized_count": entry.synthesized_count,
        "compiled_candidates": entry.compiled_candidates,
        "extracted_candidates": entry.extracted_candidates,
        "published_candidates": entry.published_candidates,
        "discarded_candidates": entry.discarded_candidates,
        "merged_entries": entry.merged_entries,
        "temporal_updates": entry.temporal_updates,
        "gate_rejected_entries": entry.gate_rejected_entries,
        "published_memory_count": entry.published_memory_count,
        "llm_cost": entry.llm_cost,
        "failure_reason": entry.failure_reason,
    }


def _summarize_batch(entries: list[DreamAuditEntry]) -> DreamBatchHealthMetrics:
    ordered = sorted(entries, key=lambda entry: (entry.started_at, entry.phase))
    started_at = ordered[0].started_at
    ended_at = max(entry.ended_at for entry in ordered)
    return DreamBatchHealthMetrics(
        batch_id=ordered[0].batch_id,
        trigger=ordered[0].trigger,
        started_at=started_at,
        ended_at=ended_at,
        duration_s=max(0.0, ended_at - started_at),
        session_count=len({entry.session_key for entry in ordered}),
        phase_count=len(ordered),
        ok_phase_count=sum(1 for entry in ordered if entry.status == "ok"),
        failed_phase_count=sum(1 for entry in ordered if entry.status != "ok"),
        promoted_count=sum(entry.promoted_count for entry in ordered),
        deduplicated_count=sum(entry.deduplicated_count for entry in ordered),
        archived_count=sum(entry.archived_count for entry in ordered),
        synthesized_count=sum(entry.synthesized_count for entry in ordered),
        compiled_candidates=sum(entry.compiled_candidates for entry in ordered),
        extracted_candidates=sum(entry.extracted_candidates for entry in ordered),
        published_candidates=sum(entry.published_candidates for entry in ordered),
        discarded_candidates=sum(entry.discarded_candidates for entry in ordered),
        merged_entries=sum(entry.merged_entries for entry in ordered),
        temporal_updates=sum(entry.temporal_updates for entry in ordered),
        gate_rejected_entries=sum(entry.gate_rejected_entries for entry in ordered),
        published_memory_count=sum(entry.published_memory_count for entry in ordered),
        total_llm_cost=sum(entry.llm_cost for entry in ordered),
    )


def _maybe_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
