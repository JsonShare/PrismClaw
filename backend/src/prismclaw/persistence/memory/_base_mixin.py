"""Base mixin declaring shared attributes for PostgresMemoryBackend mixin composition.

All mixins (SchemaMixin, QueriesMixin, SearchMixin, UpsertMixin, RepositoryMixin)
reference these attributes set by PostgresMemoryBackend.__init__. Declaring them
here as type annotations allows static analysis to resolve them correctly.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prismclaw.persistence.memory.embedding import EmbeddingClient


class _PostgresBackendBase:
    """Declares attributes shared across all postgres mixins.

    These are set by PostgresMemoryBackend.__init__ and read by mixin methods.
    """

    # Set in PostgresMemoryBackend.__init__
    _engine: Any
    _embedding: EmbeddingClient | None
    _dimensions: int | None
    _async_sessionmaker: async_sessionmaker[AsyncSession] | None
    _migrated: bool
    _init_lock: Any  # asyncio.Lock
    _last_memory_search_meta: dict[str, Any]
    _last_archive_search_meta: dict[str, Any]
    _last_artifact_search_meta: dict[str, Any]

    # Defined in SchemaMixin — declared here for cross-mixin type resolution
    async def _init_db(self) -> None: ...

    # Defined in QueriesMixin — declared here for cross-mixin type resolution
    async def _search_memory_text_rows(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _search_memory_vector_rows(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _search_l4_text_rows(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _search_l4_vector_rows(self, *args: Any, **kwargs: Any) -> Any: ...

