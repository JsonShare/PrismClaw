"""Auto-extracted Mixin from postgres.py — DO NOT edit method signatures."""
from __future__ import annotations

import logging
from typing import Any

from prismclaw.schema.memory.entry import (
    ArchiveEntry,
    L4ArtifactEntry,
    MemoryEntry,
    archive_entry_from_artifact,
)
from prismclaw.persistence.memory._helpers import (
    _candidate_limit,
    _clean_query,
)
from prismclaw.persistence.memory._row_mapping import (
    _build_search_meta_from_rows,
    _merge_artifact_candidates,
    _merge_memory_candidates,
)

logger = logging.getLogger(__name__)

from prismclaw.persistence.memory._base_mixin import _PostgresBackendBase


class SearchMixin(_PostgresBackendBase):
    async def search(
        self,
        session_key: str,
        query: str,
        *,
        layers: list[str] | None = None,
        limit: int = 10,
        per_layer_limits: dict[str, int] | None = None,
        domain: str = "general",
    ) -> list[MemoryEntry]:
        await self._init_db()
        _ = domain
        normalized_query = _clean_query(query)
        query_vec = None
        vector_requested = self._embedding is not None and bool(normalized_query)
        vector_fallback = False
        if self._embedding is not None and normalized_query:
            try:
                query_vec = await self._embedding.embed(normalized_query)
            except Exception:
                vector_fallback = True
                logger.warning("pgvector query embedding failed; degrading to text-only search", exc_info=True)

        candidate_limit = _candidate_limit(limit, per_layer_limits)
        assert self._async_sessionmaker is not None
        async with self._async_sessionmaker() as session:
            text_rows = await self._search_memory_text_rows(
                session=session,
                session_key=session_key,
                query=normalized_query,
                layers=layers,
                limit=candidate_limit,
            )
            vector_rows: list[Any] = []
            if query_vec is not None:
                try:
                    vector_rows = await self._search_memory_vector_rows(
                        session=session,
                        session_key=session_key,
                        query_vec=query_vec,
                        layers=layers,
                        limit=candidate_limit,
                    )
                except Exception:
                    vector_fallback = True
                    logger.warning("pgvector similarity search failed; degrading to text-only search", exc_info=True)
        merged = _merge_memory_candidates(
            text_rows,
            vector_rows,
            limit=limit,
            per_layer_limits=per_layer_limits,
        )
        self._last_memory_search_meta = _build_search_meta_from_rows(
            merged,
            text_rows=text_rows,
            vector_rows=vector_rows,
            vector_requested=vector_requested,
            vector_fallback=vector_fallback,
        )
        # SDD B1: expose the query embedding for attribution recording
        # (reference_log.query_embedding enables offline retrieval replay).
        if query_vec is not None:
            self._last_memory_search_meta["query_embedding"] = query_vec
        return merged

    async def search_archives(
        self, session_key: str, query: str, *, limit: int = 5, min_similarity: float = 0.0
    ) -> list[ArchiveEntry]:
        artifact_results = await self._search_l4_artifacts_core(
            session_key,
            query,
            artifact_kinds=["archive"],
            limit=limit,
            min_similarity=min_similarity,
        )
        archive_entries = [archive_entry_from_artifact(entry) for entry in artifact_results]
        self._last_archive_search_meta = dict(self._last_artifact_search_meta)
        return archive_entries

    async def _search_l4_artifacts_core(
        self,
        session_key: str,
        query: str,
        *,
        artifact_kinds: list[str] | None = None,
        limit: int = 5,
        min_similarity: float = 0.0,
    ) -> list[L4ArtifactEntry]:
        await self._init_db()
        normalized_query = _clean_query(query)
        normalized_kinds = [str(kind).strip().lower() for kind in (artifact_kinds or []) if str(kind).strip()]
        query_vec = None
        vector_requested = self._embedding is not None and bool(normalized_query)
        vector_fallback = False
        if self._embedding is not None and normalized_query:
            try:
                query_vec = await self._embedding.embed(normalized_query)
            except Exception:
                vector_fallback = True
                logger.warning("pgvector L4 query embedding failed; degrading to text-only search", exc_info=True)

        candidate_limit = _candidate_limit(limit)
        assert self._async_sessionmaker is not None
        async with self._async_sessionmaker() as session:
            text_rows = await self._search_l4_text_rows(
                session=session,
                session_key=session_key,
                query=normalized_query,
                artifact_kinds=normalized_kinds or None,
                limit=candidate_limit,
            )
            vector_rows: list[Any] = []
            if query_vec is not None:
                try:
                    vector_rows = await self._search_l4_vector_rows(
                        session=session,
                        session_key=session_key,
                        query_vec=query_vec,
                        artifact_kinds=normalized_kinds or None,
                        limit=candidate_limit,
                        min_similarity=min_similarity,
                    )
                except Exception:
                    vector_fallback = True
                    logger.warning("pgvector L4 similarity search failed; degrading to text-only search", exc_info=True)
        merged = _merge_artifact_candidates(text_rows, vector_rows, limit=limit)
        self._last_artifact_search_meta = _build_search_meta_from_rows(
            merged,
            text_rows=text_rows,
            vector_rows=vector_rows,
            vector_requested=vector_requested,
            vector_fallback=vector_fallback,
        )
        return merged

    async def search_l4_artifacts(
        self,
        session_key: str,
        query: str,
        *,
        artifact_kinds: list[str] | None = None,
        limit: int = 5,
        min_similarity: float = 0.0,
    ) -> list[L4ArtifactEntry]:
        return await self._search_l4_artifacts_core(
            session_key,
            query,
            artifact_kinds=artifact_kinds,
            limit=limit,
            min_similarity=min_similarity,
        )

