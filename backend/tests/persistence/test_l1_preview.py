"""T1-2 验收（SDD D6）：L1 结构化预览 + 淘汰权重。"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from prismclaw.persistence.memory.composite import _build_l1_preview
from prismclaw.persistence.memory.redis_index import RedisL1Index, _eviction_rank
from prismclaw.schema.memory.memory_types import MemoryEntry


def _entry(
    entry_id: str,
    *,
    content: str,
    updated_at: float | None = None,
    confidence: float | None = 0.8,
    supersedes: list[str] | None = None,
) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        layer="L2",
        type="fact",
        content=content,
        created_at=1.0,
        updated_at=updated_at if updated_at is not None else time.time(),
        confidence=confidence,
        supersedes=supersedes or [],
    )


def test_preview_is_structured_not_truncated() -> None:
    entry = _entry(
        "e1a2b3c4",
        content="kafka 部署必须先迁移 schema。后续步骤见文档",
        updated_at=time.time(),
    )
    preview = _build_l1_preview(entry)
    assert preview.startswith("[fact|")
    assert "|刚刚|" in preview or "|" in preview
    assert "迁移 schema" in preview  # 主干保留
    assert "kafka 部署必须先迁移 schema" in preview  # 首句语义不再被 64 字符腰斩
    assert "(id=e1a2b3c4)" in preview  # v1.4：短 id 尾注支持 archive 操作


def test_preview_subject_uses_first_sentence() -> None:
    entry = _entry("e2", content="第一句是主体。第二句与主体无关……")
    preview = _build_l1_preview(entry)
    assert "第一句是主体" in preview
    assert not preview.startswith("[fact|第一句是主体。第二句")  # subject 独立截断


class _FakeL1:
    def __init__(self) -> None:
        self.updated: list[MemoryEntry] = []

    async def index_update(self, session_key: str, entry: MemoryEntry) -> None:
        self.updated.append(entry)


class _FakeL2:
    async def store(self, session_key: str, entry: MemoryEntry) -> None:
        return None


@pytest.mark.asyncio
async def test_store_preserves_full_metadata_in_l1_mirror() -> None:
    from prismclaw.persistence.memory.composite import CompositeMemoryStore

    l1 = _FakeL1()
    store = CompositeMemoryStore(l1=l1, l2=_FakeL2())  # type: ignore[arg-type]
    entry = _entry(
        "e3",
        content="部署流程：先迁移再上线",
        confidence=0.95,
        supersedes=["older"],
    )
    await store.store("web:test", entry)
    mirrored = l1.updated[0]
    assert mirrored.layer == "L1"
    assert mirrored.supersedes == ["older"]  # 淘汰依据保真
    assert mirrored.confidence == 0.95
    assert mirrored.content.startswith("[fact|")


def test_eviction_rank_superseded_first() -> None:
    new_but_superseded = _entry("a", content="x", updated_at=999.0, supersedes=["z"])
    old_normal = _entry("b", content="x", updated_at=1.0)
    assert _eviction_rank(new_but_superseded) < _eviction_rank(old_normal)


def test_eviction_rank_low_confidence_next() -> None:
    low_conf = _entry("a", content="x", confidence=0.2)
    high_conf = _entry("b", content="x", confidence=0.9)
    assert _eviction_rank(low_conf) < _eviction_rank(high_conf)


class _FakeRedis:
    def __init__(self, fields: dict[str, str]) -> None:
        self._fields = dict(fields)
        self.deleted: list[str] = []

    async def hgetall(self, key: str) -> dict[str, str]:
        return {k: v for k, v in self._fields.items()}

    async def hdel(self, key: str, *fields: str) -> int:
        self.deleted.extend(fields)
        for field in fields:
            self._fields.pop(field, None)
        return len(fields)

    async def hset(self, key: str, field: str, value: str) -> None:
        self._fields[field] = value

    async def expire(self, key: str, ttl: int) -> None:
        return None


@pytest.mark.asyncio
async def test_evict_prefers_superseded_over_recent() -> None:
    superseded = _entry("sup", content="x", updated_at=999.0, supersedes=["z"])
    normal = _entry("nrm", content="y", updated_at=1.0)
    fields = {
        e.id: e.model_dump_json() for e in (superseded, normal)
    }
    redis = _FakeRedis(fields)
    index = RedisL1Index(redis)  # type: ignore[arg-type]
    index._max_entries = 1
    await index._evict("k")
    # 被淘汰的必须是 superseded 条目（尽管它更新时间最新）
    assert "sup" in redis.deleted
    assert "nrm" not in redis.deleted