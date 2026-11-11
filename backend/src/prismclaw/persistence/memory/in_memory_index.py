from __future__ import annotations

from prismclaw.schema.memory.memory_types import MemoryEntry


class InMemoryL1Index:
    def __init__(
        self,
        *,
        max_entries: int = 30,
        max_chars: int = 3000,
    ) -> None:
        self._max_entries = max_entries
        self._max_chars = max_chars
        self._entries_by_session: dict[str, dict[str, MemoryEntry]] = {}

    async def index_get(self, session_key: str) -> list[MemoryEntry]:
        entries = list(self._entries_by_session.get(session_key, {}).values())
        entries.sort(key=lambda entry: entry.updated_at, reverse=True)
        return entries

    async def index_update(self, session_key: str, entry: MemoryEntry) -> None:
        bucket = self._entries_by_session.setdefault(session_key, {})
        bucket[entry.id] = entry
        self._evict(bucket)

    async def index_remove(self, session_key: str, entry_id: str) -> None:
        bucket = self._entries_by_session.get(session_key)
        if bucket is None:
            return
        bucket.pop(entry_id, None)
        if not bucket:
            self._entries_by_session.pop(session_key, None)

    async def close(self) -> None:
        self._entries_by_session.clear()

    def _evict(self, bucket: dict[str, MemoryEntry]) -> None:
        entries = sorted(bucket.values(), key=lambda entry: entry.updated_at)
        while len(entries) > self._max_entries:
            removed = entries.pop(0)
            bucket.pop(removed.id, None)
        total_chars = sum(len(entry.content) for entry in entries)
        while total_chars > self._max_chars and entries:
            removed = entries.pop(0)
            total_chars -= len(removed.content)
            bucket.pop(removed.id, None)
