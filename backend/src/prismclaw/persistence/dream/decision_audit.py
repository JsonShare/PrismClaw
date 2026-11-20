from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prismclaw.persistence.sql_runner import apply_core_schema
from prismclaw.schema.dream.dream_types import DreamDecisionAuditRecord


class PostgresDreamDecisionAuditStore:
    """PostgreSQL-backed Dream decision audit store using a session factory."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def _init_schema(self) -> None:
        await apply_core_schema(self._sf)

    async def record(self, entry: DreamDecisionAuditRecord) -> None:
        await self._init_schema()
        async with self._sf() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO pc_dream_decision_audit (
                        decision_id, batch_id, session_key, record_id, target_id,
                        action, reason, health_status, rollout_percentage,
                        trigger, dry_run, applied, created_at
                    ) VALUES (
                        :decision_id, :batch_id, :session_key, :record_id, :target_id,
                        :action, :reason, :health_status, :rollout_percentage,
                        :trigger, :dry_run, :applied, :created_at
                    )
                    ON CONFLICT (decision_id) DO UPDATE SET
                        batch_id = EXCLUDED.batch_id,
                        session_key = EXCLUDED.session_key,
                        record_id = EXCLUDED.record_id,
                        target_id = EXCLUDED.target_id,
                        action = EXCLUDED.action,
                        reason = EXCLUDED.reason,
                        health_status = EXCLUDED.health_status,
                        rollout_percentage = EXCLUDED.rollout_percentage,
                        trigger = EXCLUDED.trigger,
                        dry_run = EXCLUDED.dry_run,
                        applied = EXCLUDED.applied,
                        created_at = EXCLUDED.created_at
                    """
                ),
                {
                    "decision_id": entry.decision_id,
                    "batch_id": entry.batch_id,
                    "session_key": entry.session_key,
                    "record_id": entry.record_id,
                    "target_id": entry.target_id,
                    "action": entry.action,
                    "reason": entry.reason,
                    "health_status": entry.health_status,
                    "rollout_percentage": entry.rollout_percentage,
                    "trigger": entry.trigger,
                    "dry_run": entry.dry_run,
                    "applied": entry.applied,
                    "created_at": entry.created_at,
                },
            )
            await session.commit()

    async def list_entries(
        self,
        *,
        session_key: str | None = None,
        batch_id: str | None = None,
        limit: int = 100,
    ) -> list[DreamDecisionAuditRecord]:
        await self._init_schema()
        where: list[str] = []
        params: dict[str, Any] = {"limit": max(1, min(limit, 500))}
        if session_key is not None:
            where.append("session_key = :session_key")
            params["session_key"] = session_key
        if batch_id is not None:
            where.append("batch_id = :batch_id")
            params["batch_id"] = batch_id
        sql = (
            "SELECT decision_id, batch_id, session_key, record_id, target_id, action, reason, "
            "health_status, rollout_percentage, trigger, dry_run, applied, created_at "
            "FROM pc_dream_decision_audit"
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT :limit"
        async with self._sf() as session:
            result = await session.execute(text(sql), params)
            return [
                DreamDecisionAuditRecord(
                    decision_id=row["decision_id"],
                    batch_id=row["batch_id"],
                    session_key=row["session_key"],
                    record_id=row["record_id"],
                    target_id=row["target_id"],
                    action=row["action"],
                    reason=row["reason"],
                    health_status=row["health_status"],
                    rollout_percentage=int(row["rollout_percentage"] or 0),
                    trigger=row["trigger"],
                    dry_run=bool(row["dry_run"]),
                    applied=bool(row["applied"]),
                    created_at=float(row["created_at"]),
                )
                for row in result.mappings()
            ]
