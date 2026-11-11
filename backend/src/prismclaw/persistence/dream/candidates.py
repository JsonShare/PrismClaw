from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from prismclaw.schema.dream.dream_types import DreamCandidateKind, DreamCandidateStatus, DreamMemoryCandidate

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS pc_dream_candidates (
    candidate_id           TEXT PRIMARY KEY,
    session_key            TEXT NOT NULL,
    session_id             TEXT NOT NULL,
    kind                   TEXT NOT NULL,
    content                TEXT NOT NULL,
    source_entry_ids_json  JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at             DOUBLE PRECISION NOT NULL,
    updated_at             DOUBLE PRECISION NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'draft',
    metadata_json          TEXT NOT NULL DEFAULT '{}'
)
"""

_INDEX = """
CREATE INDEX IF NOT EXISTS idx_pc_dream_candidates_session
ON pc_dream_candidates(session_key, status, kind, created_at DESC)
"""

_SELECT_COLUMNS = """
    candidate_id, session_key, session_id, kind, content,
    source_entry_ids_json, created_at, updated_at, status, metadata_json
"""

_UPSERT = """
INSERT INTO pc_dream_candidates (
    candidate_id, session_key, session_id, kind, content,
    source_entry_ids_json, created_at, updated_at, status, metadata_json
) VALUES (
    :candidate_id, :session_key, :session_id, :kind, :content,
    CAST(:source_entry_ids_json AS jsonb), :created_at, :updated_at, :status, :metadata_json
)
ON CONFLICT (candidate_id) DO UPDATE SET
    session_key = EXCLUDED.session_key,
    session_id = EXCLUDED.session_id,
    kind = EXCLUDED.kind,
    content = EXCLUDED.content,
    source_entry_ids_json = EXCLUDED.source_entry_ids_json,
    updated_at = EXCLUDED.updated_at,
    status = EXCLUDED.status,
    metadata_json = EXCLUDED.metadata_json
"""


class PostgresDreamCandidateStore:
    """PostgreSQL-backed Dream candidate store using a session factory."""

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
                await conn.execute(text(_INDEX))
                await session.commit()
            self._migrated = True

    async def upsert(self, candidate: DreamMemoryCandidate) -> None:
        await self._init_schema()
        now = time.time()
        updated_at = max(candidate.updated_at, now)
        async with self._sf() as session:
            await session.execute(text(_UPSERT), _candidate_to_params(candidate, updated_at))
            await session.commit()

    async def get_candidate(self, candidate_id: str) -> DreamMemoryCandidate | None:
        await self._init_schema()
        async with self._sf() as session:
            result = await session.execute(
                text(
                    f"SELECT {_SELECT_COLUMNS} FROM pc_dream_candidates "
                    f"WHERE candidate_id = :candidate_id LIMIT 1"
                ),
                {"candidate_id": candidate_id},
            )
            row = result.mappings().first()
            if row is None:
                return None
            return _candidate_from_row(row)

    async def list_candidates(
        self,
        session_key: str,
        *,
        kind: DreamCandidateKind | None = None,
        status: DreamCandidateStatus | None = "draft",
        limit: int = 100,
    ) -> list[DreamMemoryCandidate]:
        await self._init_schema()
        clauses = ["session_key = :session_key"]
        params: dict[str, Any] = {"session_key": session_key}
        if kind is not None:
            clauses.append("kind = :kind")
            params["kind"] = kind
        if status is not None:
            clauses.append("status = :status")
            params["status"] = status
        params["limit"] = max(1, int(limit))
        sql = f"""
            SELECT {_SELECT_COLUMNS}
            FROM pc_dream_candidates
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC
            LIMIT :limit
        """
        async with self._sf() as session:
            result = await session.execute(text(sql), params)
            return [_candidate_from_row(row) for row in result.mappings()]


def _candidate_from_row(row: Any) -> DreamMemoryCandidate:
    return DreamMemoryCandidate(
        candidate_id=str(row["candidate_id"]),
        session_key=str(row["session_key"]),
        session_id=str(row["session_id"]),
        kind=str(row["kind"]),
        content=str(row["content"]),
        source_entry_ids=_load_json_list(row["source_entry_ids_json"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        status=str(row["status"]),
        metadata=_load_json_dict(row["metadata_json"]),
    )


def _candidate_to_params(candidate: DreamMemoryCandidate, updated_at: float) -> dict:
    return {
        "candidate_id": candidate.candidate_id,
        "session_key": candidate.session_key,
        "session_id": candidate.session_id,
        "kind": candidate.kind,
        "content": candidate.content,
        "source_entry_ids_json": json.dumps(candidate.source_entry_ids, ensure_ascii=True),
        "created_at": float(candidate.created_at),
        "updated_at": float(updated_at),
        "status": candidate.status,
        "metadata_json": json.dumps(candidate.metadata, ensure_ascii=True, sort_keys=True),
    }


def _load_json_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw]
    try:
        value = json.loads(str(raw))
    except Exception:
        logger.warning("failed to decode dream candidate source ids", exc_info=True)
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _load_json_dict(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(str(raw))
    except Exception:
        logger.warning("failed to decode dream candidate metadata", exc_info=True)
        return {}
    return value if isinstance(value, dict) else {}
