"""Auto-extracted Mixin from postgres.py — DO NOT edit method signatures."""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from prismclaw.schema.memory.entry import (
    L4ArtifactEntry,
    MemoryEntry,
    build_archive_artifact_entry,
    build_archive_artifact_id,
)
from prismclaw.persistence.memory._helpers import (
    _sql_text,
)
from prismclaw.persistence.memory._row_mapping import (
    _memory_entry_from_row,
)

logger = logging.getLogger(__name__)

from prismclaw.persistence.memory._base_mixin import _PostgresBackendBase


class RepositoryMixin(_PostgresBackendBase):
    async def delete(self, session_key: str, entry_id: str) -> None:
        await self._init_db()
        assert self._async_sessionmaker is not None
        async with self._async_sessionmaker() as session:
            await session.execute(
                _sql_text("DELETE FROM pc_memory_entries WHERE session_key = :session_key AND id = :id"),
                {"session_key": session_key, "id": entry_id},
            )
            await session.commit()

    async def archive_entry(
        self, session_key: str, entry_id: str, *, reason: str = ""
    ) -> bool:
        """Soft-archive one entry: ``status='archived'`` + bump ``updated_at``.

        Returns ``True`` iff a row was actually transitioned from ``active``.
        ``reason`` is accepted for API compatibility and logged at debug; it is
        not persisted (no dedicated column on ``pc_memory_entries``).
        """
        import time

        await self._init_db()
        assert self._async_sessionmaker is not None
        now = time.time()
        async with self._async_sessionmaker() as session:
            result = await session.execute(
                _sql_text(
                    "UPDATE pc_memory_entries "
                    "SET status = 'archived', updated_at = :now "
                    "WHERE session_key = :session_key AND id = :id "
                    "AND status = 'active'"
                ),
                {"now": now, "session_key": session_key, "id": entry_id},
            )
            await session.commit()
            changed = int(getattr(result, "rowcount", 0) or 0) > 0
        if changed and reason:
            logger.debug("archived entry %s:%s reason=%s", session_key, entry_id[:8], reason)
        return changed

    async def archive_session(
        self,
        session_key: str,
        session_id: str,
        summary: str,
        *,
        payload: dict[str, Any] | None = None,
        demote_entries: bool = True,
    ) -> None:
        import time

        now = time.time()
        # SDD B5: normalized payload — provenance for offline replay
        # (message counts, summary engine). Callers may extend; the source
        # marker stays canonical.
        effective_payload: dict[str, Any] = {"source": "archive_session"}
        if payload:
            effective_payload.update(payload)
        await self.store_l4_artifact(
            session_key,
            build_archive_artifact_entry(
                session_id,
                summary,
                created_at=now,
                updated_at=now,
                artifact_id=build_archive_artifact_id(session_id),
                payload=effective_payload,
            ),
        )
        # SDD D15：归档收敛——会话内仍 active 的条目下架为 archived，
        # 由 L4 artifact 检索补位（检索粒度变粗是预期取舍，非缺陷）。
        # source_session_id 为空的历史条目不受影响。
        if demote_entries:
            await self._demote_session_entries(session_key, session_id, now=now)

    async def _demote_session_entries(
        self, session_key: str, session_id: str, *, now: float
    ) -> int:
        await self._init_db()
        assert self._async_sessionmaker is not None
        async with self._async_sessionmaker() as session:
            result = await session.execute(
                _sql_text(
                    "UPDATE pc_memory_entries SET status = 'archived', updated_at = :now "
                    "WHERE session_key = :session_key AND source_session_id = :session_id "
                    "AND status = 'active'"
                ),
                {
                    "session_key": session_key,
                    "session_id": session_id,
                    "now": now,
                },
            )
            await session.commit()
        demoted = int(getattr(result, "rowcount", 0) or 0)
        if demoted:
            logger.info(
                "archive_session demoted %d active entries for %s",
                demoted,
                session_id,
            )
        return demoted

    async def delete_archive(self, session_key: str, session_id: str) -> bool:
        # SDD T2-4：pc_memory_archives 死表已移除，归档删除只走 L4 artifact。
        return await self.delete_l4_artifact(session_key, build_archive_artifact_id(session_id))

    async def store_l4_artifact(self, session_key: str, entry: L4ArtifactEntry) -> None:
        await self._init_db()
        vec = None
        if self._embedding is not None:
            try:
                vec = await self._embedding.embed(entry.summary)
            except Exception:
                logger.warning("Failed to embed L4 artifact %s", entry.id, exc_info=True)
        assert self._async_sessionmaker is not None
        async with self._async_sessionmaker() as session:
            stmt = _sql_text(
                """
                INSERT INTO pc_memory_l4_artifacts (
                    id, session_key, session_id, artifact_kind, summary,
                    created_at, updated_at, graph_group_id, source_refs_json,
                    origin_stage, publish_state, payload_json, low_confidence, embedding
                ) VALUES (
                    :id, :session_key, :session_id, :artifact_kind, :summary,
                    :created_at, :updated_at, :graph_group_id, CAST(:source_refs_json AS jsonb),
                    :origin_stage, :publish_state, CAST(:payload_json AS jsonb), :low_confidence, :embedding
                )
                ON CONFLICT (id) DO UPDATE SET
                    session_id = EXCLUDED.session_id,
                    artifact_kind = EXCLUDED.artifact_kind,
                    summary = EXCLUDED.summary,
                    updated_at = EXCLUDED.updated_at,
                    graph_group_id = EXCLUDED.graph_group_id,
                    source_refs_json = EXCLUDED.source_refs_json,
                    origin_stage = EXCLUDED.origin_stage,
                    publish_state = EXCLUDED.publish_state,
                    payload_json = EXCLUDED.payload_json,
                    low_confidence = EXCLUDED.low_confidence,
                    embedding = EXCLUDED.embedding
                """
            )
            await session.execute(
                stmt,
                {
                    "id": entry.id,
                    "session_key": session_key,
                    "session_id": entry.session_id,
                    "artifact_kind": entry.artifact_kind,
                    "summary": entry.summary,
                    "created_at": entry.created_at,
                    "updated_at": entry.updated_at or entry.created_at,
                    "graph_group_id": entry.graph_group_id,
                    "source_refs_json": json.dumps(entry.source_refs),
                    "origin_stage": entry.origin_stage,
                    "publish_state": entry.publish_state,
                    "payload_json": json.dumps(entry.payload),
                    "low_confidence": entry.low_confidence,
                    "embedding": json.dumps(vec) if vec else None,
                },
            )
            await session.commit()

    async def delete_l4_artifact(self, session_key: str, artifact_id: str) -> bool:
        await self._init_db()
        assert self._async_sessionmaker is not None
        async with self._async_sessionmaker() as session:
            result = await session.execute(
                _sql_text(
                    """
                    DELETE FROM pc_memory_l4_artifacts
                    WHERE session_key = :session_key AND id = :artifact_id
                    """
                ),
                {"session_key": session_key, "artifact_id": artifact_id},
            )
            await session.commit()
            return bool(getattr(result, "rowcount", 0) and getattr(result, "rowcount", 0) > 0)


    async def get_archive_storage_stats(self, session_key: str) -> dict[str, int]:
        await self._init_db()
        assert self._async_sessionmaker is not None
        sessionmaker = self._async_sessionmaker
        async with sessionmaker() as session:
            artifact_row = await session.execute(
                _sql_text(
                    "SELECT COUNT(*) AS cnt FROM pc_memory_l4_artifacts "
                    "WHERE session_key = :session_key AND artifact_kind = 'archive'"
                ),
                {"session_key": session_key},
            )
            artifact_count = int(artifact_row.scalar_one() or 0)
            # SDD T2-4：pc_memory_archives 死表已移除，不再统计 legacy 双份。
            return {
                "archive_artifact_count": artifact_count,
            }

    async def count_by_layer(self, session_key: str) -> dict[str, int]:
        """Per-layer durable-entry counts for /memory stats.

        Keys l2/l3/l4 (lowercase — the stats display contract; composite adds
        l1 from the Redis index). Lifecycle-retired entries (archived /
        contested / decayed) don't count toward what the user "has".
        """
        await self._init_db()
        assert self._async_sessionmaker is not None
        async with self._async_sessionmaker() as session:
            rows = (await session.execute(
                _sql_text(
                    "SELECT layer, COUNT(*) FROM pc_memory_entries "
                    "WHERE session_key = :session_key "
                    "AND status NOT IN ('archived', 'contested', 'decayed') "
                    "GROUP BY layer"
                ),
                {"session_key": session_key},
            )).fetchall()
            archive_count = int((await session.execute(
                _sql_text(
                    "SELECT COUNT(*) FROM pc_memory_l4_artifacts "
                    "WHERE session_key = :session_key AND artifact_kind = 'archive'"
                ),
                {"session_key": session_key},
            )).scalar_one() or 0)
        counts = {"l2": 0, "l3": 0}
        for row in rows:
            layer = str(row[0]).lower()
            if layer in counts:
                counts[layer] = int(row[1] or 0)
        counts["l4"] = archive_count
        return counts

    async def get_contents_by_ids(
        self, session_key: str, entry_ids: list[str]
    ) -> dict[str, str]:
        """Batch-resolve entry ids to full contents (active entries only).

        SDD docs/memory-dream-sdd.md §5.0 (A1): L1 mirrors carry previews;
        consumers that need the body (dream light-sleep) read it back from
        here in one round trip. Ids missing from the result are simply
        absent (archived / deleted / unknown) — callers skip them.
        """
        if not entry_ids:
            return {}
        from sqlalchemy import bindparam  # type: ignore[import-not-found]

        await self._init_db()
        assert self._async_sessionmaker is not None
        stmt = _sql_text(
            "SELECT id, content FROM pc_memory_entries "
            "WHERE session_key = :session_key AND id IN :ids "
            "AND status = 'active'"
        ).bindparams(bindparam("ids", expanding=True))
        async with self._async_sessionmaker() as session:
            rows = (await session.execute(
                stmt,
                {"session_key": session_key, "ids": list(entry_ids)},
            )).fetchall()
        return {str(row[0]): str(row[1]) for row in rows}

    async def get_entries_by_ids(
        self,
        session_key: str,
        entry_ids: list[str],
    ) -> list[MemoryEntry]:
        """E7: 按 ID 列表批量获取完整 MemoryEntry（active only）。

        替代 index_get() 的 L1 镜像（content 被截断到 64 字符），
        用于边扩展等需要完整条目数据的场景。
        """
        if not entry_ids:
            return []
        from sqlalchemy import bindparam  # type: ignore[import-not-found]

        await self._init_db()
        assert self._async_sessionmaker is not None
        stmt = _sql_text(
            "SELECT id, layer, type, content, source_session_id, created_at, updated_at, "
            "scope, entity_key, source_refs_json, last_used_at, use_count, status, "
            "origin_stage, confidence, stability_score, cluster_id, supersedes_json, "
            "last_validated_at, graph_group_id, version_chain_id, publish_state, "
            "valid_from, valid_to, expires_at, winner_flag, low_confidence "
            "FROM pc_memory_entries "
            "WHERE session_key = :session_key AND id IN :ids AND status = 'active'"
        ).bindparams(bindparam("ids", expanding=True))
        try:
            async with self._async_sessionmaker() as session:
                rows = (await session.execute(
                    stmt,
                    {"session_key": session_key, "ids": list(entry_ids)},
                )).fetchall()
        except Exception:
            logger.warning(
                "get_entries_by_ids failed for %s", session_key, exc_info=True
            )
            return []
        return [_memory_entry_from_row(row, score=0.0) for row in rows]

    async def record_memory_references(
        self, session_key: str, records: list[dict[str, Any]]
    ) -> int:
        """Insert reference-log rows for one turn's injected memories.

        SDD docs/memory-dream-sdd.md §5.1 (B1): every retrieval that reaches
        the prompt is attributable. Best-effort: failures are logged and
        swallowed (attribution must never break retrieval).
        """
        if not records:
            return 0
        await self._init_db()
        assert self._async_sessionmaker is not None
        inserted = 0
        try:
            async with self._async_sessionmaker() as session:
                for rec in records:
                    emb = rec.get("query_embedding")
                    await session.execute(
                        _sql_text(
                            "INSERT INTO pc_memory_reference_log "
                            "(id, session_key, session_id, turn_seq, entry_id, "
                            " query_text, query_embedding, relevance_score, injected_at) "
                            "VALUES (:id, :session_key, :session_id, :turn_seq, :entry_id, "
                            " :query_text, :query_embedding, :relevance_score, :injected_at)"
                        ),
                        {
                            "id": rec["id"],
                            "session_key": session_key,
                            "session_id": rec["session_id"],
                            "turn_seq": rec.get("turn_seq"),
                            "entry_id": rec["entry_id"],
                            "query_text": rec.get("query_text"),
                            "query_embedding": json.dumps(emb) if emb else None,
                            "relevance_score": rec.get("relevance_score"),
                            "injected_at": rec["injected_at"],
                        },
                    )
                    inserted += 1
                await session.commit()
        except Exception:
            logger.warning(
                "reference-log insert failed (%d rows dropped)", len(records), exc_info=True
            )
        return inserted

    async def record_validation_events(
        self, session_key: str, events: list[dict[str, Any]]
    ) -> int:
        """Insert validation-event rows (correction / task outcome signals).

        SDD docs/memory-dream-sdd.md §5.1: strong = explicit user correction,
        weak = task-outcome co-occurrence. Best-effort like the reference log.
        """
        if not events:
            return 0
        await self._init_db()
        assert self._async_sessionmaker is not None
        inserted = 0
        try:
            async with self._async_sessionmaker() as session:
                for ev in events:
                    await session.execute(
                        _sql_text(
                            "INSERT INTO pc_memory_validation_events "
                            "(id, session_key, entry_id, signal_type, polarity, "
                            " attribution, source_ref, occurred_at, payload_json) "
                            "VALUES (:id, :session_key, :entry_id, :signal_type, :polarity, "
                            " :attribution, :source_ref, :occurred_at, CAST(:payload_json AS jsonb))"
                        ),
                        {
                            "id": ev["id"],
                            "session_key": session_key,
                            "entry_id": ev["entry_id"],
                            "signal_type": ev["signal_type"],
                            "polarity": ev["polarity"],
                            "attribution": ev.get("attribution", "weak"),
                            "source_ref": ev.get("source_ref"),
                            "occurred_at": ev["occurred_at"],
                            "payload_json": json.dumps(ev.get("payload") or {}),
                        },
                    )
                    inserted += 1
                await session.commit()
        except Exception:
            logger.warning(
                "validation-event insert failed (%d rows dropped)", len(events), exc_info=True
            )
        return inserted

    async def record_arbitration_audit(
        self,
        session_key: str,
        *,
        candidate: dict[str, Any],
        near_neighbors: list[dict[str, Any]] | None,
        decision: str,
        decided_by: str,
        mode: str,
        executed: bool,
        target_id: str | None = None,
        reason: str | None = None,
        latency_ms: int | None = None,
    ) -> bool:
        """Insert one arbitration audit row (SDD B2 §5.2). Best-effort."""
        import time as _time
        import uuid as _uuid

        await self._init_db()
        assert self._async_sessionmaker is not None
        try:
            async with self._async_sessionmaker() as session:
                await session.execute(
                    _sql_text(
                        "INSERT INTO pc_memory_arbitration_audit "
                        "(id, session_key, candidate_json, near_neighbors, decision, "
                        " decided_by, mode, executed, target_id, reason, latency_ms, created_at) "
                        "VALUES (:id, :session_key, CAST(:candidate_json AS jsonb), "
                        " CAST(:near_neighbors AS jsonb), :decision, :decided_by, :mode, "
                        " :executed, :target_id, :reason, :latency_ms, :created_at)"
                    ),
                    {
                        "id": f"arb_{_uuid.uuid4().hex[:16]}",
                        "session_key": session_key,
                        "candidate_json": json.dumps(candidate, ensure_ascii=False),
                        "near_neighbors": json.dumps(near_neighbors or [], ensure_ascii=False),
                        "decision": decision,
                        "decided_by": decided_by,
                        "mode": mode,
                        "executed": executed,
                        "target_id": target_id,
                        "reason": reason,
                        "latency_ms": latency_ms,
                        "created_at": _time.time(),
                    },
                )
                await session.commit()
            return True
        except Exception:
            logger.warning("arbitration audit insert failed", exc_info=True)
            return False

    async def count_validation_signals(
        self, session_key: str, entry_ids: list[str]
    ) -> dict[str, dict[str, int]]:
        """Aggregate validation-event polarity counts per entry (SDD B2 evidence).

        Feeds the arbitration prompt with RAW numbers only — no pre-judgment.
        """
        if not entry_ids:
            return {}
        from sqlalchemy import bindparam  # type: ignore[import-not-found]

        await self._init_db()
        assert self._async_sessionmaker is not None
        stmt = _sql_text(
            "SELECT entry_id, polarity, COUNT(*) FROM pc_memory_validation_events "
            "WHERE session_key = :session_key AND entry_id IN :ids "
            "GROUP BY entry_id, polarity"
        ).bindparams(bindparam("ids", expanding=True))
        try:
            async with self._async_sessionmaker() as session:
                rows = (await session.execute(
                    stmt, {"session_key": session_key, "ids": list(entry_ids)}
                )).fetchall()
        except Exception:
            logger.warning(
                "validation-signal aggregation failed (%d ids)", len(entry_ids), exc_info=True
            )
            return {}
        counts: dict[str, dict[str, int]] = {}
        for row in rows:
            entry_id, polarity, n = str(row[0]), str(row[1]), int(row[2] or 0)
            counts.setdefault(entry_id, {"positive": 0, "negative": 0})
            if polarity in ("positive", "negative"):
                counts[entry_id][polarity] = n
        return counts

    async def list_entries_by_status(
        self,
        session_key: str,
        statuses: list[str],
        *,
        limit: int = 500,
    ) -> list[MemoryEntry]:
        """Raw scan by status — includes quarantined states the search path
        filters out (SDD B3 mid tier: contested/decayed lifecycle handling).

        Results ordered by updated_at ASC (oldest first — decay/exit checks
        care about the oldest stale entries first).
        """
        if not statuses:
            return []
        from sqlalchemy import bindparam  # type: ignore[import-not-found]

        await self._init_db()
        assert self._async_sessionmaker is not None
        stmt = _sql_text(
            "SELECT id, layer, type, content, source_session_id, created_at, updated_at, "
            "scope, entity_key, source_refs_json, last_used_at, use_count, status, "
            "origin_stage, confidence, stability_score, cluster_id, supersedes_json, "
            "last_validated_at, graph_group_id, version_chain_id, publish_state, "
            "valid_from, valid_to, expires_at, winner_flag, low_confidence "
            "FROM pc_memory_entries "
            "WHERE session_key = :session_key AND status IN :statuses "
            "ORDER BY updated_at ASC LIMIT :limit"
        ).bindparams(bindparam("statuses", expanding=True))
        try:
            async with self._async_sessionmaker() as session:
                rows = (await session.execute(
                    stmt,
                    {"session_key": session_key, "statuses": list(statuses), "limit": limit},
                )).fetchall()
        except Exception:
            logger.warning(
                "list_entries_by_status failed for %s", session_key, exc_info=True
            )
            return []
        return [_memory_entry_from_row(row, score=0.0) for row in rows]

    async def aggregate_validation_signals(
        self, session_key: str, *, since: float | None = None
    ) -> dict[str, dict[str, int]]:
        """Per-entry validation-event counts by polarity × attribution (SDD B3).

        Keys: strong_negative / weak_negative / strong_positive / weak_positive.
        Full-history view (or everything after `since`) — used by the mid
        tier's CONTEST lifecycle checks and the deep tier's evidence gate.
        """
        await self._init_db()
        assert self._async_sessionmaker is not None
        sql = (
            "SELECT entry_id, polarity, attribution, COUNT(*) "
            "FROM pc_memory_validation_events "
            "WHERE session_key = :session_key"
        )
        params: dict[str, Any] = {"session_key": session_key}
        if since is not None:
            sql += " AND occurred_at > :since"
            params["since"] = float(since)
        sql += " GROUP BY entry_id, polarity, attribution"
        try:
            async with self._async_sessionmaker() as session:
                rows = (await session.execute(_sql_text(sql), params)).fetchall()
        except Exception:
            logger.warning(
                "aggregate_validation_signals failed for %s", session_key, exc_info=True
            )
            return {}
        counts: dict[str, dict[str, int]] = {}
        for row in rows:
            entry_id, polarity, attribution, n = (
                str(row[0]), str(row[1]), str(row[2]), int(row[3] or 0)
            )
            bucket = counts.setdefault(
                entry_id,
                {"strong_negative": 0, "weak_negative": 0, "strong_positive": 0, "weak_positive": 0},
            )
            key = f"{attribution}_{polarity}"
            if key in bucket:
                bucket[key] = n
        return counts

    async def aggregate_pending_validation_signals(
        self, session_key: str
    ) -> dict[str, dict[str, int]]:
        """Per-entry validation-event counts NOT yet consumed by a mid-tier
        writeback (SDD B3 idempotency fix).

        An event is pending while occurred_at > the entry's last_validated_at
        (NULL = never consumed). The mid tier stamps last_validated_at on
        every writeback, so each event adjusts confidence/stability exactly
        once. Without this watermark the daily run re-applies the full
        history every day — confidence × 0.8^n compounding to the floor and
        +0.02/day drift to the ceiling, corrupting the B4 ranking signal
        within days.
        """
        await self._init_db()
        assert self._async_sessionmaker is not None
        sql = (
            "SELECT v.entry_id, v.polarity, v.attribution, COUNT(*) "
            "FROM pc_memory_validation_events v "
            "JOIN pc_memory_entries e "
            "  ON e.id = v.entry_id AND e.session_key = v.session_key "
            "WHERE v.session_key = :session_key "
            "  AND v.occurred_at > COALESCE(e.last_validated_at, 0) "
            "GROUP BY v.entry_id, v.polarity, v.attribution"
        )
        try:
            async with self._async_sessionmaker() as session:
                rows = (
                    await session.execute(_sql_text(sql), {"session_key": session_key})
                ).fetchall()
        except Exception:
            logger.warning(
                "aggregate_pending_validation_signals failed for %s",
                session_key,
                exc_info=True,
            )
            return {}
        counts: dict[str, dict[str, int]] = {}
        for row in rows:
            entry_id, polarity, attribution, n = (
                str(row[0]), str(row[1]), str(row[2]), int(row[3] or 0)
            )
            bucket = counts.setdefault(
                entry_id,
                {"strong_negative": 0, "weak_negative": 0, "strong_positive": 0, "weak_positive": 0},
            )
            key = f"{attribution}_{polarity}"
            if key in bucket:
                bucket[key] = n
        return counts

    async def get_entry_by_id(
        self, session_key: str, entry_id: str, *, prefix_match: bool = False
    ) -> tuple[str | None, str | None]:
        """Resolve a short/full entry_id to (full_id, content) or (None, error_msg)."""
        await self._init_db()
        assert self._async_sessionmaker is not None
        async with self._async_sessionmaker() as session:
            if prefix_match:
                rows = (await session.execute(
                    _sql_text(
                        "SELECT id, content FROM pc_memory_entries "
                        "WHERE session_key = :sk AND id LIKE :eid "
                        "AND status = 'active'"
                    ),
                    {"sk": session_key, "eid": f"{entry_id}%"},
                )).fetchall()
            else:
                rows = (await session.execute(
                    _sql_text(
                        "SELECT id, content FROM pc_memory_entries "
                        "WHERE session_key = :sk AND id = :eid "
                        "AND status = 'active'"
                    ),
                    {"sk": session_key, "eid": entry_id},
                )).fetchall()
        if len(rows) == 0:
            return None, f"memory: 未找到 ID 匹配 '{entry_id}' 的活跃条目"
        if len(rows) > 1:
            return None, f"memory: '{entry_id}' 匹配多条记录({len(rows)}条)，请提供更长的 ID"
        return str(rows[0][0]), str(rows[0][1])

    async def get_entry_id_by_entity_key(
        self, session_key: str, entity_key: str,
    ) -> str | None:
        """E6: 精确查询 entity_key 对应的最新 active entry_id（IG1）。

        按 updated_at DESC 取最新，避免依赖 search() 排序不确定性。
        """
        if not entity_key:
            return None
        await self._init_db()
        assert self._async_sessionmaker is not None
        async with self._async_sessionmaker() as session:
            rows = (await session.execute(
                _sql_text(
                    "SELECT id FROM pc_memory_entries "
                    "WHERE session_key = :sk AND entity_key = :ek AND status = 'active' "
                    "ORDER BY updated_at DESC LIMIT 1"
                ),
                {"sk": session_key, "ek": entity_key},
            )).fetchall()
        return str(rows[0][0]) if rows else None

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()

    async def verify_integrity(
        self, session_key: str, *, limit: int = 10000
    ) -> dict[str, Any]:
        """E2: 校验记忆条目的内容完整性（IE2：只读操作）。

        遍历所有条目，验证 content_hash 与实际哈希是否一致。
        返回 {valid: bool, total: int, broken_ids: list[str]}。
        已知限制（S5）：DBA 可同时更新 content + content_hash 完美伪造。
        """
        await self._init_db()
        assert self._async_sessionmaker is not None
        try:
            async with self._async_sessionmaker() as session:
                rows = (await session.execute(
                    _sql_text(
                        "SELECT id, content, entity_key, scope, content_hash "
                        "FROM pc_memory_entries WHERE session_key = :session_key "
                        "LIMIT :limit"
                    ),
                    {"session_key": session_key, "limit": limit},
                )).fetchall()
        except Exception:
            logger.warning("verify_integrity query failed for %s", session_key, exc_info=True)
            return {"valid": False, "total": 0, "broken_ids": []}

        broken: list[str] = []
        for row in rows:
            eid, content, entity_key, scope, stored_hash = (
                str(row[0]), str(row[1] or ""), row[2], row[3], row[4]
            )
            if stored_hash is None:
                continue  # E2 上线前的旧条目，无 content_hash
            expected = hashlib.sha256(
                f"{eid}\n{content}\n{entity_key}\n{scope}".encode()
            ).hexdigest()
            if expected != stored_hash:
                broken.append(eid)

        return {
            "valid": len(broken) == 0,
            "total": len(rows),
            "broken_ids": broken,
        }

