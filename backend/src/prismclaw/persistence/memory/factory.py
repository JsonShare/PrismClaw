from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncEngine

from prismclaw.infra.settings import EmbeddingSettings, MemorySettings
from prismclaw.schema.storage.storage_types import MemoryStore

if TYPE_CHECKING:
    import redis.asyncio as aioredis


def _build_embedding_client(embedding_settings: EmbeddingSettings):
    if not (embedding_settings.model or "").strip():
        return None
    from prismclaw.persistence.memory.embedding import EmbeddingClient

    return EmbeddingClient(
        model=embedding_settings.model,
        api_key=embedding_settings.api_key,
        api_base=embedding_settings.base_url,
        dimensions=embedding_settings.dimensions,
        llm_provider=embedding_settings.llm_provider,
    )


def _build_notary(memory_settings: MemorySettings, *, dlp_scanner: Any | None = None):
    """构建记忆公证器（写入前安全拦截器）。notary_enabled=False 时返回 None。"""
    if not memory_settings.notary_enabled:
        return None
    from prismclaw.persistence.memory.notary import MemoryNotary

    if dlp_scanner is None:
        # DLP scanner not injected — notary will not be created
        return None
    return MemoryNotary(dlp_scanner=dlp_scanner)


def create_memory_store(
    engine: AsyncEngine,
    memory_settings: MemorySettings,
    embedding_settings: EmbeddingSettings,
    redis_client: aioredis.Redis,
    *,
    key_prefix: str = "prismclaw:",
    dlp_scanner: Any | None = None,
) -> MemoryStore:
    from prismclaw.persistence.memory.composite import CompositeMemoryStore
    from prismclaw.persistence.memory.postgres import PostgresMemoryBackend
    from prismclaw.persistence.memory.redis_index import RedisL1Index

    embedding = _build_embedding_client(embedding_settings)
    notary = _build_notary(memory_settings, dlp_scanner=dlp_scanner)
    return CompositeMemoryStore(
        l1=RedisL1Index(
            redis_client,
            key_prefix=key_prefix,
            max_entries=memory_settings.l1_max_entries,
            max_chars=memory_settings.l1_max_chars,
            ttl_seconds=memory_settings.l1_ttl_seconds,
        ),
        l2=PostgresMemoryBackend(
            engine,
            embedding,
            dimensions=embedding_settings.dimensions,
        ),
        notary=notary,
    )
