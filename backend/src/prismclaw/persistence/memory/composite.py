from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Any

from prismclaw.schema.memory.memory_types import ArchiveEntry, L4ArtifactEntry, MemoryEntry
from prismclaw.schema.shared.utils import truncate_by_tokens
from prismclaw.persistence.memory.notary import MemoryNotary

if TYPE_CHECKING:
    from prismclaw.persistence.memory.postgres import PostgresMemoryBackend
    from prismclaw.persistence.memory.redis_index import RedisL1Index

logger = logging.getLogger(__name__)

# SDD docs/memory-dream-sdd.md §5.0 (A1): L1 is a pure index, not a content
# cache. The mirror carries only a short preview; any consumer that needs the
# body (dream light-sleep consolidation, diagnostics) must read the full text
# back from L2 via get_contents_by_ids(). Feeding the truncated mirror into
# the consolidation LLM produced semantically incomplete promotions and
# duplicate writes against L2 (defect D1).
#
# SDD D6: preview 由 64 字符硬切断改为结构化预览「[type|subject|age] tail…」——
# 确定性截断（非 LLM 摘要化），让模型基于可见的类型/主体/时间执行 archive
# 决策；subject 为句读断句后 ≤ submit 预算的头部。
_PREVIEW_SUBJECT_TOKENS = 40
_PREVIEW_TAIL_TOKENS = 48

_SENTENCE_BOUNDARY_RE = re.compile(r"[。．.;；\n]")


def _build_l1_preview(entry: MemoryEntry) -> str:
    """L1 结构化预览（SDD D6 + v1.4）：确定性截断，非 LLM 摘要。

    格式：[type|subject|age] tail…(id=<8位短id>)——短 id 尾注让模型能对
    索引中看到的条目直接发起 archive（memory 工具按 8 位前缀匹配）。
    """
    content = str(entry.content or "").strip()
    subject = _subject_of(content) or "—"
    age = _relative_age(getattr(entry, "updated_at", None))
    tail = truncate_by_tokens(content, _PREVIEW_TAIL_TOKENS)
    short_id = str(getattr(entry, "id", "") or "")[:8]
    id_hint = f" (id={short_id})" if short_id else ""
    return f"[{entry.type}|{subject}|{age}] {tail}{id_hint}"


def _subject_of(content: str) -> str:
    boundary = _SENTENCE_BOUNDARY_RE.search(content)
    head = content[: boundary.start()] if boundary else content
    return truncate_by_tokens(head, _PREVIEW_SUBJECT_TOKENS).rstrip("…")


def _relative_age(timestamp: float | None) -> str:
    if not timestamp:
        return "unknown"
    seconds = max(0.0, time.time() - float(timestamp))
    minutes = seconds / 60.0
    if minutes < 60:
        return "刚刚"
    hours = minutes / 60.0
    if hours < 24:
        return f"{int(hours)}h"
    days = hours / 24.0
    if days < 30:
        return f"{int(days)}d"
    if days < 365:
        return f"{int(days / 30)}mo"
    return "旧"


class CompositeMemoryStore:
    def __init__(
        self,
        *,
        l1: RedisL1Index,
        l2: PostgresMemoryBackend,
        notary: MemoryNotary | None = None,
    ) -> None:
        self._l1 = l1
        self._l2 = l2
        self._notary = notary

    @property
    def last_memory_search_meta(self) -> dict[str, Any]:
        return dict(getattr(self._l2, "last_memory_search_meta", {}))

    @property
    def last_archive_search_meta(self) -> dict[str, Any]:
        return dict(getattr(self._l2, "last_archive_search_meta", {}))

    async def index_get(self, session_key: str) -> list[MemoryEntry]:
        return await self._l1.index_get(session_key)

    async def index_update(self, session_key: str, entry: MemoryEntry) -> None:
        await self._l1.index_update(session_key, entry)

    async def index_remove(self, session_key: str, entry_id: str) -> None:
        await self._l1.index_remove(session_key, entry_id)

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
        return await self._l2.search(
            session_key, 
            query, 
            layers=layers, 
            limit=limit, 
            per_layer_limits=per_layer_limits,
            domain=domain
        )

    async def store(self, session_key: str, entry: MemoryEntry) -> None:
        entry = self._notarize(entry)
        await self._l2.store(session_key, entry)
        # L1 mirror = index + structured preview only (see module note).
        l1_entry = entry.model_copy(
            update={"layer": "L1", "content": _build_l1_preview(entry)}
        )
        await self._l1.index_update(session_key, l1_entry)

    async def delete(self, session_key: str, entry_id: str) -> None:
        await self._l2.delete(session_key, entry_id)
        await self._l1.index_remove(session_key, entry_id)

    async def archive_entry(self, session_key: str, entry_id: str, *, reason: str = "") -> bool:
        """Soft-archive: UPDATE status='archived' on L2 + evict from L1."""
        changed = await self._l2.archive_entry(session_key, entry_id, reason=reason)
        if changed:
            try:
                await self._l1.index_remove(session_key, entry_id)
            except Exception:
                logger.warning("L1 evict failed for %s:%s", session_key, entry_id, exc_info=True)
        return changed

    async def archive_session(
        self,
        session_key: str,
        session_id: str,
        summary: str,
        *,
        payload: dict[str, Any] | None = None,
        demote_entries: bool = True,
    ) -> None:
        await self._l2.archive_session(
            session_key,
            session_id,
            summary,
            payload=payload,
            demote_entries=demote_entries,
        )

    async def delete_archive(self, session_key: str, session_id: str) -> bool:
        return await self._l2.delete_archive(session_key, session_id)

    async def search_archives(
        self, session_key: str, query: str, *, limit: int = 5, min_similarity: float = 0.0
    ) -> list[ArchiveEntry]:
        return await self._l2.search_archives(
            session_key, query, limit=limit, min_similarity=min_similarity
        )

    async def store_l4_artifact(self, session_key: str, entry: L4ArtifactEntry) -> None:
        await self._l2.store_l4_artifact(session_key, entry)

    async def delete_l4_artifact(self, session_key: str, artifact_id: str) -> bool:
        return await self._l2.delete_l4_artifact(session_key, artifact_id)

    async def search_l4_artifacts(
        self,
        session_key: str,
        query: str,
        *,
        artifact_kinds: list[str] | None = None,
        limit: int = 5,
        min_similarity: float = 0.0,
    ) -> list[L4ArtifactEntry]:
        return await self._l2.search_l4_artifacts(
            session_key,
            query,
            artifact_kinds=artifact_kinds,
            limit=limit,
            min_similarity=min_similarity,
        )

    async def get_archive_storage_stats(self, session_key: str) -> dict[str, int]:
        return await self._l2.get_archive_storage_stats(session_key)

    async def get_entry_by_id(
        self, session_key: str, entry_id: str, *, prefix_match: bool = False
    ) -> tuple[str | None, str | None]:
        return await self._l2.get_entry_by_id(
            session_key, entry_id, prefix_match=prefix_match
        )

    async def get_contents_by_ids(
        self, session_key: str, entry_ids: list[str]
    ) -> dict[str, str]:
        return await self._l2.get_contents_by_ids(session_key, entry_ids)

    async def get_entries_by_ids(
        self, session_key: str, entry_ids: list[str]
    ) -> list[MemoryEntry]:
        return await self._l2.get_entries_by_ids(session_key, entry_ids)

    async def record_memory_references(
        self, session_key: str, records: list[dict[str, Any]]
    ) -> int:
        return await self._l2.record_memory_references(session_key, records)

    async def writeback_use_counts(self, session_key: str, entry_ids: list[str]) -> int:
        return await self._l2.writeback_use_counts(session_key, entry_ids)

    async def record_validation_events(
        self, session_key: str, events: list[dict[str, Any]]
    ) -> int:
        return await self._l2.record_validation_events(session_key, events)

    async def record_arbitration_audit(
        self,
        session_key: str,
        *,
        candidate: dict[str, Any],
        near_neighbors: list[dict[str, Any]] | None,
        decision: str,
        decided_by: str,
        mode: str,
        executed: bool,
        target_id: str | None = None,
        reason: str | None = None,
        latency_ms: int | None = None,
    ) -> bool:
        return await self._l2.record_arbitration_audit(
            session_key,
            candidate=candidate,
            near_neighbors=near_neighbors,
            decision=decision,
            decided_by=decided_by,
            mode=mode,
            executed=executed,
            target_id=target_id,
            reason=reason,
            latency_ms=latency_ms,
        )

    async def count_validation_signals(
        self, session_key: str, entry_ids: list[str]
    ) -> dict[str, dict[str, int]]:
        return await self._l2.count_validation_signals(session_key, entry_ids)

    async def list_entries_by_status(
        self,
        session_key: str,
        statuses: list[str],
        *,
        limit: int = 500,
    ) -> list[MemoryEntry]:
        return await self._l2.list_entries_by_status(session_key, statuses, limit=limit)

    async def aggregate_validation_signals(
        self, session_key: str, *, since: float | None = None
    ) -> dict[str, dict[str, int]]:
        return await self._l2.aggregate_validation_signals(session_key, since=since)

    async def aggregate_pending_validation_signals(
        self, session_key: str
    ) -> dict[str, dict[str, int]]:
        return await self._l2.aggregate_pending_validation_signals(session_key)

    async def apply_entry_updates(
        self, session_key: str, updates: list[tuple[str, dict[str, Any]]]
    ) -> int:
        return await self._l2.apply_entry_updates(session_key, updates)

    async def verify_integrity(
        self, session_key: str, **kw: Any
    ) -> dict[str, Any]:
        return await self._l2.verify_integrity(session_key, **kw)

    async def get_entry_id_by_entity_key(
        self, session_key: str, entity_key: str,
    ) -> str | None:
        return await self._l2.get_entry_id_by_entity_key(session_key, entity_key)

    async def count_by_layer(self, session_key: str) -> dict[str, int]:
        l1_entries = await self._l1.index_get(session_key)
        counts = await self._l2.count_by_layer(session_key)
        return {"l1": len(l1_entries), **counts}

    async def close(self) -> None:
        for backend in (self._l1, self._l2):
            try:
                await backend.close()
            except Exception:
                logger.warning("error closing %s", type(backend).__name__, exc_info=True)

    # ── 内部方法 ──

    def _notarize(self, entry: MemoryEntry) -> MemoryEntry:
        """写入前安全拦截。fail-open：任何异常不阻断写入。"""
        if self._notary is None:
            return entry
        try:
            result = self._notary.inspect(entry.content)
            content = result.content
            tags: list[str] = []
            if result.injection_score >= 0.3:
                tags.append("injection_suspect")
            if result.has_hidden_unicode:
                tags.append("hidden_unicode")
            if result.redacted_by_dlp:
                tags.append("dlp_redacted")
            if not tags and content == entry.content:
                return entry
            updates: dict[str, Any] = {}
            if content != entry.content:
                updates["content"] = content
            if tags:
                origin_stage = entry.origin_stage or ""
                updates["origin_stage"] = (
                    f"{origin_stage}:{':'.join(tags)}" if origin_stage else ':'.join(tags)
                )
            return entry.model_copy(update=updates)
        except Exception:
            logger.warning("Notary inspect failed, passing through", exc_info=True)
            return entry
