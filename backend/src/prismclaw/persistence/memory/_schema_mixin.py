"""Auto-extracted Mixin from postgres.py — DO NOT edit method signatures."""
from __future__ import annotations

import logging

from prismclaw.persistence.memory._helpers import (
    _sql_text,
)

logger = logging.getLogger(__name__)

from prismclaw.persistence.memory._base_mixin import _PostgresBackendBase


class SchemaMixin(_PostgresBackendBase):
    async def _init_db(self) -> None:
        if self._migrated:
            return

        async with self._init_lock:
            if self._migrated:
                return

            try:
                from sqlalchemy.ext.asyncio import async_sessionmaker # type: ignore
            except ImportError:
                raise ImportError(
                    "PostgreSQL backend requires sqlalchemy[asyncio] and asyncpg. "
                    "Install with `pip install prismclaw[postgres]`."
                )

            self._async_sessionmaker = async_sessionmaker(self._engine, expire_on_commit=False)

            # Determine vector dimensions (default 1536 for OpenAI, but allow dynamic or generic VECTOR)
            dim_str = ""
            if self._dimensions:
                dim_str = f"({self._dimensions})"

            # Initialize tables and extensions
            async with self._engine.begin() as conn:
                await conn.execute(_sql_text("CREATE EXTENSION IF NOT EXISTS vector"))
                await conn.execute(_sql_text("CREATE EXTENSION IF NOT EXISTS vchord_bm25"))
                await conn.execute(_sql_text("CREATE EXTENSION IF NOT EXISTS pg_tokenizer"))
                await conn.execute(
                    _sql_text(
                        f"""
                        CREATE TABLE IF NOT EXISTS pc_memory_entries (
                            id VARCHAR PRIMARY KEY,
                            session_key VARCHAR NOT NULL,
                            layer VARCHAR NOT NULL,
                            type VARCHAR NOT NULL,
                            content TEXT NOT NULL,
                            scope VARCHAR,
                            entity_key VARCHAR,
                            source_session_id VARCHAR,
                            source_refs_json JSONB DEFAULT '[]'::jsonb,
                            created_at FLOAT NOT NULL,
                            updated_at FLOAT NOT NULL,
                            last_used_at FLOAT,
                            use_count INTEGER DEFAULT 0,
                            status VARCHAR DEFAULT 'active',
                            origin_stage VARCHAR,
                            confidence FLOAT,
                            stability_score FLOAT,
                            cluster_id VARCHAR,
                            supersedes_json JSONB DEFAULT '[]'::jsonb,
                            last_validated_at FLOAT,
                            graph_group_id VARCHAR,
                            version_chain_id VARCHAR,
                            publish_state VARCHAR,
                            valid_from FLOAT,
                            valid_to FLOAT,
                            expires_at FLOAT,
                            winner_flag BOOLEAN DEFAULT TRUE,
                            low_confidence BOOLEAN DEFAULT FALSE,
                            embedding VECTOR{dim_str},
                            content_bm25 bm25vector
                        )
                        """
                    )
                )
                await conn.execute(
                    _sql_text(
                        "CREATE INDEX IF NOT EXISTS idx_pc_memory_session_key ON pc_memory_entries (session_key)"
                    )
                )
                if dim_str:
                    await conn.execute(
                        _sql_text(
                            "CREATE INDEX IF NOT EXISTS idx_pc_memory_embedding_hnsw ON pc_memory_entries USING hnsw (embedding vector_cosine_ops)"
                        )
                    )
                
                await conn.execute(
                    _sql_text(
                        f"""
                        CREATE TABLE IF NOT EXISTS pc_memory_l4_artifacts (
                            id VARCHAR PRIMARY KEY,
                            session_key VARCHAR NOT NULL,
                            session_id VARCHAR NOT NULL,
                            artifact_kind VARCHAR NOT NULL,
                            summary TEXT NOT NULL,
                            created_at FLOAT NOT NULL,
                            updated_at FLOAT NOT NULL,
                            graph_group_id VARCHAR,
                            source_refs_json JSONB DEFAULT '[]'::jsonb,
                            origin_stage VARCHAR,
                            publish_state VARCHAR,
                            payload_json JSONB DEFAULT '{{}}'::jsonb,
                            low_confidence BOOLEAN DEFAULT FALSE,
                            embedding VECTOR{dim_str},
                            summary_bm25 bm25vector
                        )
                        """
                    )
                )
                await conn.execute(
                    _sql_text(
                        f"""
                        CREATE TABLE IF NOT EXISTS pc_memory_reference_log (
                            id VARCHAR PRIMARY KEY,
                            session_key VARCHAR NOT NULL,
                            session_id VARCHAR NOT NULL,
                            turn_seq INTEGER,
                            entry_id VARCHAR NOT NULL,
                            query_text TEXT,
                            query_embedding VECTOR{dim_str},
                            relevance_score FLOAT,
                            injected_at FLOAT NOT NULL
                        )
                        """
                    )
                )
                await conn.execute(
                    _sql_text(
                        "CREATE INDEX IF NOT EXISTS idx_pmrl_entry ON pc_memory_reference_log (entry_id)"
                    )
                )
                await conn.execute(
                    _sql_text(
                        "CREATE INDEX IF NOT EXISTS idx_pmrl_session ON pc_memory_reference_log (session_id)"
                    )
                )
                await conn.execute(
                    _sql_text(
                        """
                        CREATE TABLE IF NOT EXISTS pc_memory_validation_events (
                            id VARCHAR PRIMARY KEY,
                            session_key VARCHAR NOT NULL,
                            entry_id VARCHAR NOT NULL,
                            signal_type VARCHAR NOT NULL,
                            polarity VARCHAR NOT NULL,
                            attribution VARCHAR NOT NULL,
                            source_ref VARCHAR,
                            occurred_at FLOAT NOT NULL,
                            payload_json JSONB DEFAULT '{}'::jsonb
                        )
                        """
                    )
                )
                await conn.execute(
                    _sql_text(
                        "CREATE INDEX IF NOT EXISTS idx_pmve_entry ON pc_memory_validation_events (entry_id)"
                    )
                )
                # SDD B2 (§5.2): arbitration decisions — the shadow-mode
                # replay source and decision-gate-2 data. `executed`
                # distinguishes shadow (decided, not applied) from active.
                await conn.execute(
                    _sql_text(
                        """
                        CREATE TABLE IF NOT EXISTS pc_memory_arbitration_audit (
                            id VARCHAR PRIMARY KEY,
                            session_key VARCHAR NOT NULL,
                            candidate_json JSONB NOT NULL,
                            near_neighbors JSONB,
                            decision VARCHAR NOT NULL,
                            decided_by VARCHAR NOT NULL,
                            mode VARCHAR NOT NULL,
                            executed BOOLEAN DEFAULT FALSE,
                            target_id VARCHAR,
                            reason TEXT,
                            latency_ms INTEGER,
                            created_at FLOAT NOT NULL
                        )
                        """
                    )
                )
                await conn.execute(
                    _sql_text(
                        "CREATE INDEX IF NOT EXISTS idx_pmaa_created ON pc_memory_arbitration_audit (created_at)"
                    )
                )
                for stmt in (
                    "ALTER TABLE pc_memory_entries ADD COLUMN IF NOT EXISTS scope VARCHAR",
                    "ALTER TABLE pc_memory_entries ADD COLUMN IF NOT EXISTS entity_key VARCHAR",
                    "ALTER TABLE pc_memory_entries ADD COLUMN IF NOT EXISTS source_refs_json JSONB DEFAULT '[]'::jsonb",
                    "ALTER TABLE pc_memory_entries ADD COLUMN IF NOT EXISTS origin_stage VARCHAR",
                    "ALTER TABLE pc_memory_entries ADD COLUMN IF NOT EXISTS confidence FLOAT",
                    "ALTER TABLE pc_memory_entries ADD COLUMN IF NOT EXISTS stability_score FLOAT",
                    "ALTER TABLE pc_memory_entries ADD COLUMN IF NOT EXISTS cluster_id VARCHAR",
                    "ALTER TABLE pc_memory_entries ADD COLUMN IF NOT EXISTS supersedes_json JSONB DEFAULT '[]'::jsonb",
                    "ALTER TABLE pc_memory_entries ADD COLUMN IF NOT EXISTS last_validated_at FLOAT",
                    "ALTER TABLE pc_memory_entries ADD COLUMN IF NOT EXISTS graph_group_id VARCHAR",
                    "ALTER TABLE pc_memory_entries ADD COLUMN IF NOT EXISTS version_chain_id VARCHAR",
                    "ALTER TABLE pc_memory_entries ADD COLUMN IF NOT EXISTS publish_state VARCHAR",
                    "ALTER TABLE pc_memory_entries ADD COLUMN IF NOT EXISTS valid_from FLOAT",
                    "ALTER TABLE pc_memory_entries ADD COLUMN IF NOT EXISTS valid_to FLOAT",
                    "ALTER TABLE pc_memory_entries ADD COLUMN IF NOT EXISTS expires_at FLOAT",
                    "ALTER TABLE pc_memory_entries ADD COLUMN IF NOT EXISTS winner_flag BOOLEAN DEFAULT TRUE",
                    "ALTER TABLE pc_memory_entries ADD COLUMN IF NOT EXISTS low_confidence BOOLEAN DEFAULT FALSE",
                    "ALTER TABLE pc_memory_entries ADD COLUMN IF NOT EXISTS content_hash VARCHAR",
                    "ALTER TABLE pc_memory_entries ADD COLUMN IF NOT EXISTS content_bm25 bm25vector",
                    "ALTER TABLE pc_memory_l4_artifacts ADD COLUMN IF NOT EXISTS summary_bm25 bm25vector",
                ):
                    await conn.execute(_sql_text(stmt))
                await conn.execute(
                    _sql_text(
                        "ALTER TABLE pc_memory_l4_artifacts ADD COLUMN IF NOT EXISTS publish_state VARCHAR"
                    )
                )
                await conn.execute(
                    _sql_text(
                        "CREATE INDEX IF NOT EXISTS idx_pc_l4_session_key ON pc_memory_l4_artifacts (session_key)"
                    )
                )
                await conn.execute(
                    _sql_text(
                        "CREATE INDEX IF NOT EXISTS idx_pc_l4_artifact_kind ON pc_memory_l4_artifacts (artifact_kind)"
                    )
                )
                # E8: entity_key 条件索引（E6 精确查询加速）
                await conn.execute(
                    _sql_text(
                        "CREATE INDEX IF NOT EXISTS idx_entries_entity_key "
                        "ON pc_memory_entries(session_key, entity_key) "
                        "WHERE status = 'active'"
                    )
                )
                # E5: 记忆图谱边表
                await conn.execute(
                    _sql_text(
                        """
                        CREATE TABLE IF NOT EXISTS pc_memory_edges (
                            id          TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
                            session_key TEXT NOT NULL,
                            source_id   TEXT NOT NULL,
                            target_id   TEXT NOT NULL,
                            predicate   TEXT NOT NULL
                                        CHECK (predicate IN ('causes','prohibits','is_a','precedes','replaces','conflicts')),
                            condition   TEXT NOT NULL DEFAULT '',
                            confidence  REAL DEFAULT 1.0,
                            evidence    TEXT,
                            status      TEXT NOT NULL DEFAULT 'active'
                                        CHECK (status IN ('active','inactive')),
                            created_at  DOUBLE PRECISION NOT NULL,
                            updated_at  DOUBLE PRECISION
                        )
                        """
                    )
                )
                # F2 修正：部分唯一索引只约束 active 边，允许同逻辑边 active→inactive→active
                await conn.execute(
                    _sql_text(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_unique
                            ON pc_memory_edges(session_key, source_id, target_id, predicate, condition)
                            WHERE status = 'active'
                        """
                    )
                )
                for idx_sql in (
                    "CREATE INDEX IF NOT EXISTS idx_edges_src ON pc_memory_edges(session_key, source_id) WHERE status = 'active'",
                    "CREATE INDEX IF NOT EXISTS idx_edges_tgt ON pc_memory_edges(session_key, target_id) WHERE status = 'active'",
                    "CREATE INDEX IF NOT EXISTS idx_edges_pred ON pc_memory_edges(session_key, predicate) WHERE status = 'active'",
                ):
                    await conn.execute(_sql_text(idx_sql))
                # vchord_bm25 CJK tokenizer setup (jieba pre-tokenizer) — idempotent
                await conn.execute(
                    _sql_text(
                        """
                        DO $$
                        BEGIN
                            IF NOT EXISTS (
                                SELECT 1 FROM tokenizer_catalog.text_analyzer WHERE name = 'jieba_analyzer'
                            ) THEN
                                PERFORM create_text_analyzer('jieba_analyzer', '[pre_tokenizer.jieba]');
                            END IF;
                        END $$
                        """
                    )
                )
                # Per-table model + tokenizer + trigger: auto-fills bm25vector on INSERT/UPDATE.
                # Backfills any rows that pre-date the trigger (no-op on fresh deployments).
                for _tok, _model, _table, _src, _tgt in (
                    ("mem_content_tok", "mem_content_model", "pc_memory_entries", "content", "content_bm25"),
                    ("l4_summary_tok", "l4_summary_model", "pc_memory_l4_artifacts", "summary", "summary_bm25"),
                ):
                    await conn.execute(
                        _sql_text(
                            f"""
                            DO $$
                            BEGIN
                                IF NOT EXISTS (
                                    SELECT 1 FROM tokenizer_catalog.model WHERE name = '{_model}'
                                ) THEN
                                    PERFORM create_custom_model_tokenizer_and_trigger(
                                        tokenizer_name => '{_tok}',
                                        model_name => '{_model}',
                                        text_analyzer_name => 'jieba_analyzer',
                                        table_name => '{_table}',
                                        source_column => '{_src}',
                                        target_column => '{_tgt}'
                                    );
                                END IF;
                            END $$
                            """
                        )
                    )
                    await conn.execute(
                        _sql_text(
                            f"UPDATE {_table} SET {_src} = {_src} WHERE {_tgt} IS NULL"
                        )
                    )
                # BM25 indexes (Block-WeakAnd ranking for keyword recall)
                await conn.execute(
                    _sql_text(
                        "CREATE INDEX IF NOT EXISTS idx_pc_memory_content_bm25 "
                        "ON pc_memory_entries USING bm25 (content_bm25 bm25_ops)"
                    )
                )
                await conn.execute(
                    _sql_text(
                        "CREATE INDEX IF NOT EXISTS idx_pc_l4_summary_bm25 "
                        "ON pc_memory_l4_artifacts USING bm25 (summary_bm25 bm25_ops)"
                    )
                )
                # Drop legacy FTS/trgm indexes (superseded by vchord_bm25)
                for _drop in (
                    "DROP INDEX IF EXISTS idx_memory_content_fts",
                    "DROP INDEX IF EXISTS idx_memory_content_trgm",
                    "DROP INDEX IF EXISTS idx_archive_summary_fts",
                    "DROP INDEX IF EXISTS idx_archive_summary_trgm",
                    "DROP INDEX IF EXISTS idx_l4_summary_fts",
                    "DROP INDEX IF EXISTS idx_l4_summary_trgm",
                ):
                    await conn.execute(_sql_text(_drop))
                if dim_str:
                    await conn.execute(
                        _sql_text(
                            "CREATE INDEX IF NOT EXISTS idx_pc_l4_embedding_hnsw ON pc_memory_l4_artifacts USING hnsw (embedding vector_cosine_ops)"
                        )
                    )

            self._migrated = True
            logger.info("PostgreSQL memory backend initialized with pgvector + vchord_bm25")

