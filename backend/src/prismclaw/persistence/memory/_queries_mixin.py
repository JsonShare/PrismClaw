"""Auto-extracted Mixin from postgres.py — DO NOT edit method signatures."""
from __future__ import annotations

import json
import logging
from typing import Any

from prismclaw.persistence.memory._helpers import (
    _build_layer_filter_sql,
    _sql_text,
)

logger = logging.getLogger(__name__)

from prismclaw.persistence.memory._base_mixin import _PostgresBackendBase


class QueriesMixin(_PostgresBackendBase):
    async def _search_memory_text_rows(
        self,
        *,
        session: Any,
        session_key: str,
        query: str,
        layers: list[str] | None,
        limit: int,
    ) -> list[Any]:
        layer_sql, layer_params = _build_layer_filter_sql(layers)
        base_params: dict[str, Any] = {
            "session_key": session_key,
            "limit": limit,
            **layer_params,
        }
        if not query:
            result = await session.execute(
                _sql_text(
                    f"""
                    SELECT id, layer, type, content, source_session_id, created_at, updated_at,
                           scope, entity_key, source_refs_json, last_used_at, use_count, status, origin_stage, confidence,
                           stability_score, cluster_id, supersedes_json, last_validated_at,
                           graph_group_id, version_chain_id, publish_state, valid_from,
                           valid_to, expires_at, winner_flag, low_confidence, 0.0 AS retrieval_score
                    FROM pc_memory_entries
                    WHERE session_key = :session_key{layer_sql}
                      AND status NOT IN ('archived', 'contested', 'decayed')
                    ORDER BY updated_at DESC
                    LIMIT :limit
                    """
                ),
                base_params,
            )
            return list(result.mappings().all())

        params = {
            **base_params,
            "query": query,
        }
        result = await session.execute(
            _sql_text(
                f"""
                SELECT id, layer, type, content, source_session_id, created_at, updated_at,
                       scope, entity_key, source_refs_json, last_used_at, use_count, status, origin_stage, confidence,
                       stability_score, cluster_id, supersedes_json, last_validated_at,
                       graph_group_id, version_chain_id, publish_state, valid_from,
                       valid_to, expires_at, winner_flag, low_confidence,
                       -(content_bm25 <&> to_bm25query(
                           'idx_pc_memory_content_bm25'::regclass,
                           tokenize(:query, 'mem_content_tok')::bm25vector
                       )) AS retrieval_score
                FROM pc_memory_entries
                WHERE session_key = :session_key{layer_sql}
                  AND status NOT IN ('archived', 'contested', 'decayed')
                  AND (content_bm25 <&> to_bm25query(
                           'idx_pc_memory_content_bm25'::regclass,
                           tokenize(:query, 'mem_content_tok')::bm25vector
                       )) < 0
                ORDER BY retrieval_score DESC, updated_at DESC
                LIMIT :limit
                """
            ),
            params,
        )
        return list(result.mappings().all())

    async def _search_memory_vector_rows(
        self,
        *,
        session: Any,
        session_key: str,
        query_vec: list[float],
        layers: list[str] | None,
        limit: int,
    ) -> list[Any]:
        layer_sql, layer_params = _build_layer_filter_sql(layers)
        result = await session.execute(
            _sql_text(
                f"""
                SELECT id, layer, type, content, source_session_id, created_at, updated_at,
                       scope, entity_key, source_refs_json, last_used_at, use_count, status, origin_stage, confidence,
                       stability_score, cluster_id, supersedes_json, last_validated_at,
                       graph_group_id, version_chain_id, publish_state, valid_from,
                       valid_to, expires_at, winner_flag, low_confidence,
                       1 - (embedding <=> :query_vec) AS retrieval_score
                FROM pc_memory_entries
                WHERE session_key = :session_key{layer_sql}
                  AND status NOT IN ('archived', 'contested', 'decayed')
                  AND embedding IS NOT NULL
                ORDER BY embedding <=> :query_vec
                LIMIT :limit
                """
            ),
            {
                "session_key": session_key,
                "query_vec": json.dumps(query_vec),
                "limit": limit,
                **layer_params,
            },
        )
        return list(result.mappings().all())

    async def _search_l4_text_rows(
        self,
        *,
        session: Any,
        session_key: str,
        query: str,
        artifact_kinds: list[str] | None,
        limit: int,
    ) -> list[Any]:
        kind_sql, kind_params = _build_layer_filter_sql(artifact_kinds)
        # Replace column name and param prefix from 'layer' to 'artifact_kind'
        filter_sql = kind_sql.replace("layer", "artifact_kind")
        filter_params = {
            k.replace("layer_", "artifact_kind_"): v for k, v in kind_params.items()
        }
        base_params: dict[str, Any] = {
            "session_key": session_key,
            "limit": limit,
            **filter_params,
        }
        if not query:
            result = await session.execute(
                _sql_text(
                    f"""
                    SELECT id, session_id, artifact_kind AS layer, artifact_kind, summary,
                           created_at, updated_at, graph_group_id, source_refs_json,
                           origin_stage, publish_state, payload_json, low_confidence, 0.0 AS retrieval_score
                    FROM pc_memory_l4_artifacts
                    WHERE session_key = :session_key{filter_sql}
                    ORDER BY updated_at DESC, created_at DESC
                    LIMIT :limit
                    """
                ),
                base_params,
            )
            return list(result.mappings().all())
        result = await session.execute(
            _sql_text(
                f"""
                SELECT id, session_id, artifact_kind AS layer, artifact_kind, summary,
                       created_at, updated_at, graph_group_id, source_refs_json,
                       origin_stage, publish_state, payload_json, low_confidence,
                       -(summary_bm25 <&> to_bm25query(
                           'idx_pc_l4_summary_bm25'::regclass,
                           tokenize(:query, 'l4_summary_tok')::bm25vector
                       )) AS retrieval_score
                FROM pc_memory_l4_artifacts
                WHERE session_key = :session_key{filter_sql}
                  AND (summary_bm25 <&> to_bm25query(
                           'idx_pc_l4_summary_bm25'::regclass,
                           tokenize(:query, 'l4_summary_tok')::bm25vector
                       )) < 0
                ORDER BY retrieval_score DESC, updated_at DESC, created_at DESC
                LIMIT :limit
                """
            ),
            {
                **base_params,
                "query": query,
            },
        )
        return list(result.mappings().all())

    async def _search_l4_vector_rows(
        self,
        *,
        session: Any,
        session_key: str,
        query_vec: list[float],
        artifact_kinds: list[str] | None,
        limit: int,
        min_similarity: float,
    ) -> list[Any]:
        kind_sql, kind_params = _build_layer_filter_sql(artifact_kinds)
        filter_sql = kind_sql.replace("layer", "artifact_kind")
        filter_params = {
            k.replace("layer_", "artifact_kind_"): v for k, v in kind_params.items()
        }
        result = await session.execute(
            _sql_text(
                f"""
                SELECT id, session_id, artifact_kind AS layer, artifact_kind, summary,
                       created_at, updated_at, graph_group_id, source_refs_json,
                       origin_stage, publish_state, payload_json, low_confidence,
                       1 - (embedding <=> :query_vec) AS similarity
                FROM pc_memory_l4_artifacts
                WHERE session_key = :session_key{filter_sql}
                  AND embedding IS NOT NULL
                  AND 1 - (embedding <=> :query_vec) >= :min_similarity
                ORDER BY embedding <=> :query_vec
                LIMIT :limit
                """
            ),
            {
                "session_key": session_key,
                "query_vec": json.dumps(query_vec),
                "min_similarity": min_similarity,
                "limit": limit,
                **filter_params,
            },
        )
        return list(result.mappings().all())

