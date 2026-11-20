from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prismclaw.persistence.sql_runner import apply_core_schema
from prismclaw.schema.dream.dream_types import (
    DreamGovernanceRecord,
    DreamPublishChannel,
    DreamPublishState,
    DreamReviewStatus,
    DreamRolloutStage,
    DreamTargetType,
)

_SELECT_COLUMNS = """
    record_id, session_key, target_type, target_id, publish_state, review_status,
    publish_channel, rollout_stage, shadow, created_at, updated_at, metadata_json
"""

_UPSERT = """
INSERT INTO pc_dream_governance_records (
    record_id, session_key, target_type, target_id, publish_state, review_status,
    publish_channel, rollout_stage, shadow, created_at, updated_at, metadata_json
) VALUES (
    :record_id, :session_key, :target_type, :target_id, :publish_state, :review_status,
    :publish_channel, :rollout_stage, :shadow, :created_at, :updated_at, CAST(:metadata_json AS jsonb)
)
ON CONFLICT (record_id) DO UPDATE SET
    session_key = EXCLUDED.session_key,
    target_type = EXCLUDED.target_type,
    target_id = EXCLUDED.target_id,
    publish_state = EXCLUDED.publish_state,
    review_status = EXCLUDED.review_status,
    publish_channel = EXCLUDED.publish_channel,
    rollout_stage = EXCLUDED.rollout_stage,
    shadow = EXCLUDED.shadow,
    updated_at = EXCLUDED.updated_at,
    metadata_json = EXCLUDED.metadata_json
"""

_UPDATE_STATUS = """
UPDATE pc_dream_governance_records
SET publish_state = :publish_state,
    review_status = :review_status,
    metadata_json = CAST(:metadata_json AS jsonb),
    updated_at = :updated_at
WHERE record_id = :record_id
"""


class PostgresDreamGovernanceStore:
    """PostgreSQL-backed Dream governance store using a session factory."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def _init_schema(self) -> None:
        await apply_core_schema(self._sf)

    async def record(self, record: DreamGovernanceRecord) -> None:
        await self._init_schema()
        now = time.time()
        async with self._sf() as session:
            await session.execute(text(_UPSERT), _record_to_params(record, now))
            await session.commit()

    async def get(self, record_id: str) -> DreamGovernanceRecord | None:
        await self._init_schema()
        async with self._sf() as session:
            result = await session.execute(
                text(
                    f"SELECT {_SELECT_COLUMNS} FROM pc_dream_governance_records "
                    f"WHERE record_id = :record_id"
                ),
                {"record_id": record_id},
            )
            row = result.mappings().first()
            if row is None:
                return None
            return _record_from_row(row)

    async def list_records(
        self,
        *,
        session_key: str | None = None,
        target_type: DreamTargetType | None = None,
        target_id: str | None = None,
        review_status: DreamReviewStatus | None = None,
        publish_channel: DreamPublishChannel | None = None,
        rollout_stage: DreamRolloutStage | None = None,
        limit: int = 100,
    ) -> list[DreamGovernanceRecord]:
        await self._init_schema()
        clauses: list[str] = []
        params: dict[str, Any] = {}
        if session_key is not None:
            clauses.append("session_key = :session_key")
            params["session_key"] = session_key
        if target_type is not None:
            clauses.append("target_type = :target_type")
            params["target_type"] = target_type
        if target_id is not None:
            clauses.append("target_id = :target_id")
            params["target_id"] = target_id
        if review_status is not None:
            clauses.append("review_status = :review_status")
            params["review_status"] = review_status
        if publish_channel is not None:
            clauses.append("publish_channel = :publish_channel")
            params["publish_channel"] = publish_channel
        if rollout_stage is not None:
            clauses.append("rollout_stage = :rollout_stage")
            params["rollout_stage"] = rollout_stage
        params["limit"] = max(1, int(limit))
        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT {_SELECT_COLUMNS}
            FROM pc_dream_governance_records
            {where_clause}
            ORDER BY created_at DESC
            LIMIT :limit
        """
        async with self._sf() as session:
            result = await session.execute(text(sql), params)
            return [_record_from_row(row) for row in result.mappings()]

    async def update_status(
        self,
        record_id: str,
        *,
        publish_state: DreamPublishState | None = None,
        review_status: DreamReviewStatus | None = None,
        metadata_updates: dict[str, Any] | None = None,
    ) -> DreamGovernanceRecord | None:
        await self._init_schema()
        current = await self.get(record_id)
        if current is None:
            return None
        metadata = dict(current.metadata)
        if metadata_updates:
            metadata.update(metadata_updates)
        new_publish_state = publish_state or current.publish_state
        new_review_status = review_status or current.review_status
        now = time.time()
        async with self._sf() as session:
            await session.execute(
                text(_UPDATE_STATUS),
                {
                    "record_id": record_id,
                    "publish_state": new_publish_state,
                    "review_status": new_review_status,
                    "metadata_json": json.dumps(metadata, ensure_ascii=True, sort_keys=True),
                    "updated_at": now,
                },
            )
            await session.commit()
        return DreamGovernanceRecord(
            record_id=current.record_id,
            session_key=current.session_key,
            target_type=current.target_type,
            target_id=current.target_id,
            publish_state=new_publish_state,
            review_status=new_review_status,
            publish_channel=current.publish_channel,
            rollout_stage=current.rollout_stage,
            shadow=current.shadow,
            created_at=current.created_at,
            updated_at=now,
            metadata=metadata,
        )


def _record_from_row(row: Any) -> DreamGovernanceRecord:
    return DreamGovernanceRecord(
        record_id=str(row["record_id"]),
        session_key=str(row["session_key"]),
        target_type=str(row["target_type"]),
        target_id=str(row["target_id"]),
        publish_state=str(row["publish_state"]),
        review_status=str(row["review_status"]),
        publish_channel=str(row["publish_channel"]),
        rollout_stage=str(row["rollout_stage"]),
        shadow=bool(row["shadow"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        metadata=_load_metadata(row["metadata_json"]),
    )


def _record_to_params(record: DreamGovernanceRecord, now: float) -> dict:
    return {
        "record_id": record.record_id,
        "session_key": record.session_key,
        "target_type": record.target_type,
        "target_id": record.target_id,
        "publish_state": record.publish_state,
        "review_status": record.review_status,
        "publish_channel": record.publish_channel,
        "rollout_stage": record.rollout_stage,
        "shadow": record.shadow,
        "created_at": float(record.created_at),
        "updated_at": float(max(record.updated_at, now)),
        "metadata_json": json.dumps(record.metadata, ensure_ascii=True, sort_keys=True),
    }


def _load_metadata(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(str(raw))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}
