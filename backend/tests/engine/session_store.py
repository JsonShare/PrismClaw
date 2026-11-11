from __future__ import annotations

from typing import Any

from prismclaw.persistence.session.redis import RedisSessionStore
from prismclaw.persistence.session.base import serialize_entry


class _FakeLockManager:
    async def acquire(self, _key: str, *, ttl_ms: int) -> str:
        _ = ttl_ms
        return "token"

    async def release(self, _key: str, _token: str) -> None:
        return


class _FakePipeline:
    def __init__(self, client: "_FakeRedisClient") -> None:
        self._client = client
        self._ops: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def __aenter__(self) -> "_FakePipeline":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._ops.clear()

    def __getattr__(self, name: str):
        def _enqueue(*args, **kwargs):
            self._ops.append((name, args, kwargs))
            return self

        return _enqueue

    async def execute(self) -> list[Any]:
        results: list[Any] = []
        for name, args, kwargs in self._ops:
            method = getattr(self._client, name)
            results.append(await method(*args, **kwargs))
        self._ops.clear()
        return results


class _FakeRedisClient:
    def __init__(self) -> None:
        self._kv: dict[str, Any] = {}
        self._hashes: dict[str, dict[str, Any]] = {}
        self._lists: dict[str, list[Any]] = {}
        self._zsets: dict[str, dict[str, float]] = {}

    def pipeline(self, *, transaction: bool = False) -> _FakePipeline:
        _ = transaction
        return _FakePipeline(self)

    async def set(self, key: str, value: Any, ex: int | None = None) -> bool:
        _ = ex
        self._kv[key] = value
        return True

    async def get(self, key: str) -> Any:
        return self._kv.get(key)

    async def expire(self, key: str, ttl: int) -> bool:
        _ = key, ttl
        return True

    async def hgetall(self, key: str) -> dict[Any, Any]:
        return dict(self._hashes.get(key, {}))

    async def hset(self, key: str, field: str, value: Any) -> int:
        self._hashes.setdefault(key, {})[field] = value
        return 1

    async def hvals(self, key: str) -> list[Any]:
        return list(self._hashes.get(key, {}).values())

    async def lrange(self, key: str, start: int, end: int) -> list[Any]:
        values = self._lists.get(key, [])
        if end == -1:
            end = len(values) - 1
        return list(values[start : end + 1])

    async def rpush(self, key: str, value: Any) -> int:
        bucket = self._lists.setdefault(key, [])
        bucket.append(value)
        return len(bucket)

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        bucket = self._zsets.setdefault(key, {})
        for member, score in mapping.items():
            bucket[member] = float(score)
        return len(mapping)

    async def zrevrangebyscore(
        self,
        key: str,
        _max_score: str,
        _min_score: str,
        *,
        start: int = 0,
        num: int | None = None,
    ) -> list[str]:
        bucket = self._zsets.get(key, {})
        ordered = [item[0] for item in sorted(bucket.items(), key=lambda item: item[1], reverse=True)]
        if num is None:
            return ordered[start:]
        return ordered[start : start + num]

    async def zrem(self, key: str, member: str) -> int:
        bucket = self._zsets.get(key, {})
        existed = member in bucket
        bucket.pop(member, None)
        return int(existed)

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            removed += int(self._kv.pop(key, None) is not None)
            removed += int(self._hashes.pop(key, None) is not None)
            removed += int(self._lists.pop(key, None) is not None)
            removed += int(self._zsets.pop(key, None) is not None)
        return removed


class FakeRedisSessionStore(RedisSessionStore):
    def __init__(self) -> None:
        super().__init__(_FakeRedisClient(), _FakeLockManager(), ttl_seconds=604_800, key_prefix="test:")

    async def save_header(self, tree) -> None:
        await super().save_header(tree)
        entries_key = self._entries_key(tree.header.id)
        order_key = self._order_key(tree.header.id)
        leaf_key = self._leaf_key(tree.header.id)
        await self._client.delete(entries_key, order_key, leaf_key)
        if tree.entries:
            async with self._client.pipeline(transaction=False) as pipe:
                for entry_id, entry in tree.entries.items():
                    pipe.hset(entries_key, entry_id, serialize_entry(entry))
                for entry_id in tree.order:
                    pipe.rpush(order_key, entry_id)
                if tree.leaf_id is not None:
                    pipe.set(leaf_key, tree.leaf_id)
                await pipe.execute()
