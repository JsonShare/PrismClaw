from __future__ import annotations

from typing import TYPE_CHECKING

from prismclaw.schema.memory.memory_types import MemoryEntry

if TYPE_CHECKING:
    import redis.asyncio as aioredis


def _eviction_rank(entry: MemoryEntry) -> tuple[float, float, float]:
    """L1 淘汰排序键（升序 = 先淘汰，SDD D6）。

    (superseded, confidence, updated_at)：已被 merge/supersede 的条目先清；
    同态下低置信度先清；再同则按更新时间从旧到新。
    """
    superseded = 0.0 if (entry.supersedes or []) else 1.0
    confidence = entry.confidence if isinstance(entry.confidence, (int, float)) else 0.5
    return (superseded, float(confidence), float(entry.updated_at or 0.0))


class RedisL1Index:
    def __init__(
        self,
        client: aioredis.Redis,
        *,
        key_prefix: str = "prismclaw:",
        max_entries: int = 30,
        max_chars: int = 3000,
        ttl_seconds: int = 2_592_000,
    ) -> None:
        self._client = client
        self._key_prefix = key_prefix
        self._max_entries = max_entries
        self._max_chars = max_chars
        self._ttl_seconds = ttl_seconds

    def _hash_key(self, session_key: str) -> str:
        return f"{self._key_prefix}memory:L1:{session_key}"

    async def index_get(self, session_key: str) -> list[MemoryEntry]:
        raw = await self._client.hgetall(self._hash_key(session_key))
        if not raw:
            return []
        entries = [MemoryEntry.model_validate_json(v) for v in raw.values()]
        entries.sort(key=lambda e: e.updated_at, reverse=True)
        return entries

    async def index_update(self, session_key: str, entry: MemoryEntry) -> None:
        key = self._hash_key(session_key)
        await self._client.hset(key, entry.id, entry.model_dump_json())
        await self._evict(key)
        await self._client.expire(key, self._ttl_seconds)

    async def index_remove(self, session_key: str, entry_id: str) -> None:
        await self._client.hdel(self._hash_key(session_key), entry_id)

    async def close(self) -> None:
        pass

    async def _evict(self, key: str) -> None:
        raw = await self._client.hgetall(key)
        if not raw:
            return
        entries = [(field, MemoryEntry.model_validate_json(value)) for field, value in raw.items()]
        # 淘汰序（升序 = 先删，SDD D6）：已被合并替代（supersedes 非空）最优先，
        # 低置信度次之，最后才是更新时间（recency tie-break）——不再一刀切按
        # 时间淘汰，避免把高价值的新记忆或高置信条目先清出去。
        entries.sort(key=lambda pair: _eviction_rank(pair[1]))

        while len(entries) > self._max_entries:
            field, _ = entries.pop(0)
            await self._client.hdel(key, field)

        total = sum(len(pair[1].content) for pair in entries)
        while total > self._max_chars and entries:
            field, removed = entries.pop(0)
            await self._client.hdel(key, field)
            total -= len(removed.content)
