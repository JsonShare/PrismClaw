from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from prismclaw.persistence.memory.embedding import EmbeddingClient
from prismclaw.persistence.memory._schema_mixin import SchemaMixin
from prismclaw.persistence.memory._queries_mixin import QueriesMixin
from prismclaw.persistence.memory._search_mixin import SearchMixin
from prismclaw.persistence.memory._upsert_mixin import UpsertMixin
from prismclaw.persistence.memory._repository_mixin import RepositoryMixin

logger = logging.getLogger(__name__)


class PostgresMemoryBackend(
    SchemaMixin,
    QueriesMixin,
    SearchMixin,
    UpsertMixin,
    RepositoryMixin,
):
    """
    PostgreSQL backend for Memory Store using pgvector + vchord_bm25.

    Hybrid search: BM25 keyword recall (vchord_bm25 + pg_tokenizer CJK) fused
    with vector semantic recall (pgvector) via Reciprocal Rank Fusion (RRF).
    Uses the application-wide shared AsyncEngine (see ``prismclaw.infra.db``).
    """

    def __init__(
        self,
        engine: AsyncEngine,
        embedding: EmbeddingClient | None = None,
        *,
        dimensions: int | None = None,
    ) -> None:
        self._engine = engine
        self._embedding = embedding
        self._dimensions = dimensions or (embedding.dimensions if embedding else None)
        self._async_sessionmaker = None
        self._migrated = False
        self._init_lock = asyncio.Lock()
        self._last_memory_search_meta: dict[str, Any] = {}
        self._last_archive_search_meta: dict[str, Any] = {}
        self._last_artifact_search_meta: dict[str, Any] = {}

    @property
    def last_memory_search_meta(self) -> dict[str, Any]:
        return dict(self._last_memory_search_meta)

    @property
    def last_archive_search_meta(self) -> dict[str, Any]:
        return dict(self._last_archive_search_meta)

    @property
    def last_artifact_search_meta(self) -> dict[str, Any]:
        return dict(self._last_artifact_search_meta)
