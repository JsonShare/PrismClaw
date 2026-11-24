"""L1 工作索引（Redis hash）。

边界约定：写入侧严格校验（MemoryEntry 构造即校验，非法 type 直接 raise），
读取侧逐条隔离——L1 是可重建的派生索引而非 source of truth，单条损坏不得
毒死整批，也不得因无法解码而永久阻断淘汰（写入路径 index_update → _evict
依赖同一次解码）。坏字段就地删除，读取一次即自愈。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from prismclaw.infra.redis import resolve
from prismclaw.schema.memory.memory_types import MemoryEntry

if TYPE_CHECKING:
    import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


def _decode_index(raw: dict[str, str]) -> tuple[list[tuple[str, MemoryEntry]], list[str]]:
    """逐条解码 L1 索引，返回 (可用条目, 待隔离字段)。

    单条解码失败只影响该条：记录完整异常便于定位，坏字段由调用方 hdel。
    """
    entries: list[tuple[str, MemoryEntry]] = []
    poisoned: list[str] = []
    for field, value in raw.items():
        try:
            entries.append((field, MemoryEntry.model_validate_json(value)))
        except Exception:
            poisoned.append(field)
            logger.warning("L1 index entry undecodable; quarantined field=%s", field, exc_info=True)
    return entries, poisoned


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
        key = self._hash_key(session_key)
        raw = await resolve(self._client.hgetall(key))
        if not raw:
            return []
        entries, poisoned = _decode_index(raw)
        if poisoned:
            # 自愈：坏字段就地清除，避免每次读取都重复失败。
            await resolve(self._client.hdel(key, *poisoned))
        result = [entry for _, entry in entries]
        result.sort(key=lambda e: e.updated_at, reverse=True)
        return result

    async def index_update(self, session_key: str, entry: MemoryEntry) -> None:
        key = self._hash_key(session_key)
        await resolve(self._client.hset(key, entry.id, entry.model_dump_json()))
        await self._evict(key)
        await resolve(self._client.expire(key, self._ttl_seconds))

    async def index_remove(self, session_key: str, entry_id: str) -> None:
        await resolve(self._client.hdel(self._hash_key(session_key), entry_id))

    async def close(self) -> None:
        pass

    async def _evict(self, key: str) -> None:
        raw = await resolve(self._client.hgetall(key))
        if not raw:
            return
        entries, poisoned = _decode_index(raw)
        if poisoned:
            # 淘汰是写入路径的一部分：坏字段若不清除，本轮所有后续写入都会
            # 重复失败，L1 永久停摆。
            await resolve(self._client.hdel(key, *poisoned))
        # 淘汰序（升序 = 先删，SDD D6）：已被合并替代（supersedes 非空）最优先，
        # 低置信度次之，最后才是更新时间（recency tie-break）——不再一刀切按
        # 时间淘汰，避免把高价值的新记忆或高置信条目先清出去。
        entries.sort(key=lambda pair: _eviction_rank(pair[1]))

        while len(entries) > self._max_entries:
            field, _ = entries.pop(0)
            await resolve(self._client.hdel(key, field))

        total = sum(len(pair[1].content) for pair in entries)
        while total > self._max_chars and entries:
            field, removed = entries.pop(0)
            await resolve(self._client.hdel(key, field))
            total -= len(removed.content)
