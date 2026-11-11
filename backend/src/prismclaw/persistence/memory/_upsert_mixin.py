"""Auto-extracted Mixin from postgres.py — DO NOT edit method signatures."""
from __future__ import annotations

import json
import logging
from typing import Any

from prismclaw.schema.memory.entry import (
    MemoryEntry,
)
from prismclaw.persistence.memory._helpers import (
    _compute_content_hash,
    _sql_text,
)

logger = logging.getLogger(__name__)

from prismclaw.persistence.memory._base_mixin import _PostgresBackendBase


class UpsertMixin(_PostgresBackendBase):
    async def store(self, session_key: str, entry: MemoryEntry) -> None:
        await self._init_db()

        vec = None
        if self._embedding is not None and entry.layer in ("L2", "L3"):
            try:
                vec = await self._embedding.embed(entry.content)
            except Exception:
                logger.warning("Failed to embed entry %s", entry.id, exc_info=True)

        assert self._async_sessionmaker is not None
        async with self._async_sessionmaker() as session:
            stmt = _sql_text(
                """
                INSERT INTO pc_memory_entries (
                    id, session_key, layer, type, content, scope, entity_key, source_session_id, source_refs_json,
                    created_at, updated_at, last_used_at, use_count, status,
                    origin_stage, confidence, stability_score, cluster_id,
                    supersedes_json, last_validated_at, graph_group_id,
                    version_chain_id, publish_state, valid_from, valid_to, expires_at, winner_flag,
                    low_confidence, content_hash, embedding
                ) VALUES (
                    :id, :session_key, :layer, :type, :content, :scope, :entity_key, :source_session_id, CAST(:source_refs_json AS jsonb),
                    :created_at, :updated_at, :last_used_at, :use_count, :status,
                    :origin_stage, :confidence, :stability_score, :cluster_id,
                    CAST(:supersedes_json AS jsonb), :last_validated_at, :graph_group_id,
                    :version_chain_id, :publish_state, :valid_from, :valid_to, :expires_at, :winner_flag,
                    :low_confidence, :content_hash, :embedding
                )
                ON CONFLICT (id) DO UPDATE SET
                    content = EXCLUDED.content,
                    scope = EXCLUDED.scope,
                    entity_key = EXCLUDED.entity_key,
                    source_session_id = EXCLUDED.source_session_id,
                    source_refs_json = EXCLUDED.source_refs_json,
                    updated_at = EXCLUDED.updated_at,
                    last_used_at = EXCLUDED.last_used_at,
                    use_count = EXCLUDED.use_count,
                    status = EXCLUDED.status,
                    origin_stage = EXCLUDED.origin_stage,
                    confidence = EXCLUDED.confidence,
                    stability_score = EXCLUDED.stability_score,
                    cluster_id = EXCLUDED.cluster_id,
                    supersedes_json = EXCLUDED.supersedes_json,
                    last_validated_at = EXCLUDED.last_validated_at,
                    graph_group_id = EXCLUDED.graph_group_id,
                    version_chain_id = EXCLUDED.version_chain_id,
                    publish_state = EXCLUDED.publish_state,
                    valid_from = EXCLUDED.valid_from,
                    valid_to = EXCLUDED.valid_to,
                    expires_at = EXCLUDED.expires_at,
                    winner_flag = EXCLUDED.winner_flag,
                    low_confidence = EXCLUDED.low_confidence,
                    content_hash = EXCLUDED.content_hash
                """
            )
            await session.execute(
                stmt,
                {
                    "id": entry.id,
                    "session_key": session_key,
                    "layer": entry.layer,
                    "type": entry.type,
                    "content": entry.content,
                    "scope": entry.scope,
                    "entity_key": entry.entity_key,
                    "source_session_id": entry.source_session_id,
                    "source_refs_json": json.dumps(entry.source_refs),
                    "created_at": entry.created_at,
                    "updated_at": entry.updated_at,
                    "last_used_at": entry.last_used_at,
                    "use_count": entry.use_count,
                    "status": entry.status,
                    "origin_stage": entry.origin_stage,
                    "confidence": entry.confidence,
                    "stability_score": entry.stability_score,
                    "cluster_id": entry.cluster_id,
                    "supersedes_json": json.dumps(entry.supersedes),
                    "last_validated_at": entry.last_validated_at,
                    "graph_group_id": entry.graph_group_id,
                    "version_chain_id": entry.version_chain_id,
                    "publish_state": entry.publish_state,
                    "valid_from": entry.valid_from,
                    "valid_to": entry.valid_to,
                    "expires_at": entry.expires_at,
                    "winner_flag": entry.winner_flag,
                    "low_confidence": entry.low_confidence,
                    "content_hash": _compute_content_hash(entry),
                    "embedding": json.dumps(vec) if vec else None,
                },
            )
            await session.commit()


    async def writeback_use_counts(self, session_key: str, entry_ids: list[str]) -> int:
        """use_count += 1 / last_used_at = now for the given entries (D4 fix).

        Returns the number of rows actually bumped.
        """
        if not entry_ids:
            return 0
        from sqlalchemy import bindparam  # type: ignore[import-not-found]

        await self._init_db()
        assert self._async_sessionmaker is not None
        import time as _time

        now = _time.time()
        stmt = _sql_text(
            "UPDATE pc_memory_entries "
            "SET use_count = COALESCE(use_count, 0) + 1, last_used_at = :now "
            "WHERE session_key = :session_key AND id IN :ids AND status = 'active'"
        ).bindparams(bindparam("ids", expanding=True))
        try:
            async with self._async_sessionmaker() as session:
                result = await session.execute(
                    stmt, {"now": now, "session_key": session_key, "ids": list(entry_ids)}
                )
                await session.commit()
                return int(getattr(result, "rowcount", 0) or 0)
        except Exception:
            logger.warning(
                "use_count writeback failed (%d ids dropped)", len(entry_ids), exc_info=True
            )
            return 0

    async def apply_entry_updates(
        self, session_key: str, updates: list[tuple[str, dict[str, Any]]]
    ) -> int:
        """Batch lifecycle updates for mid tier (SDD B3).

        Column whitelist — only governance fields the deterministic bookkeeping
        tier may touch. Returns the number of applied rows (best-effort).
        """
        if not updates:
            return 0
        allowed = {
            "status", "confidence", "stability_score", "valid_to", "winner_flag",
            "expires_at", "publish_state", "updated_at", "cluster_id",
            "last_validated_at",
        }
        # E2（RE4 风险）：content 不在白名单中。content 更新走 store() 路径
        # （ON CONFLICT upsert），每次 store 都重算 content_hash。如果未来
        # 白名单扩展到包含 content，必须同步重算 content_hash 列。
        await self._init_db()
        assert self._async_sessionmaker is not None
        applied = 0
        try:
            async with self._async_sessionmaker() as session:
                for entry_id, fields in updates:
                    safe = {k: v for k, v in fields.items() if k in allowed}
                    if not safe:
                        continue
                    # NOTE: updated_at is NOT auto-bumped here. Mid-tier
                    # bookkeeping (confidence/stability writeback, decay,
                    # contest exits) is lifecycle management, not a content
                    # change — auto-bumping would reset the B4 decay clock
                    # (age is computed from updated_at) on every daily run,
                    # making decay permanently ineffective. Callers pass
                    # updated_at explicitly when a real change warrants it.
                    set_clause = ", ".join(f"{k} = :{k}" for k in safe)
                    await session.execute(
                        _sql_text(
                            f"UPDATE pc_memory_entries SET {set_clause} "
                            "WHERE session_key = :session_key AND id = :entry_id"
                        ),
                        {"session_key": session_key, "entry_id": entry_id, **safe},
                    )
                    applied += 1
                await session.commit()
        except Exception:
            logger.warning(
                "apply_entry_updates failed for %s (%d updates)",
                session_key,
                len(updates),
                exc_info=True,
            )
        return applied

