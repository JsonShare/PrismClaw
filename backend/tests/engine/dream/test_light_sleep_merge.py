"""T0-4 验收测试（SDD D4）：light sleep 合并必须下架 L2/L3 原件。

断言语义变更：合并后原件经 archive_entry（status=archived + L1 驱逐），
而不是仅 index_remove（只删 L1 镜像 → 原件继续 active、制造副本）。
"""

from __future__ import annotations

from typing import Any

import pytest

from prismclaw.engine.dream.engine import DreamEngine
from prismclaw.schema.memory.memory_types import MemoryEntry


class _FakeLLM:
    def __init__(self, raw_json: str) -> None:
        self._raw = raw_json

    async def complete(self, *, request: Any, temperature: Any = None) -> Any:
        return _FakeResponse(self._raw)


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text
        self.usage = None


class _FakeMemoryStore:
    """记录调用轨迹的最小实现，只覆盖 light sleep legacy 路径所需接口。"""

    def __init__(self, l1_entries: list[MemoryEntry], fulltext: dict[str, str]) -> None:
        self._l1 = l1_entries
        self._fulltext = fulltext
        self.stored: list[MemoryEntry] = []
        self.archived: list[tuple[str, str]] = []
        self.index_removed: list[str] = []

    async def index_get(self, session_key: str) -> list[MemoryEntry]:
        return self._l1

    async def get_contents_by_ids(self, session_key: str, entry_ids: list[str]) -> dict[str, str]:
        return {entry_id: self._fulltext[entry_id] for entry_id in entry_ids}

    async def store(self, session_key: str, entry: MemoryEntry) -> None:
        self.stored.append(entry)

    async def archive_entry(self, session_key: str, entry_id: str, *, reason: str = "") -> bool:
        self.archived.append((entry_id, reason))
        return True

    async def index_remove(self, session_key: str, entry_id: str) -> None:
        self.index_removed.append(entry_id)


def _build_engine(memory_store: _FakeMemoryStore, *, raw_json: str) -> DreamEngine:
    return DreamEngine(
        memory_store=memory_store,  # type: ignore[arg-type]
        session_store=None,  # type: ignore[arg-type]
        llm_client=_FakeLLM(raw_json),  # type: ignore[arg-type]
        light_sleep_threshold_s=0,
    )


_L1_ENTRIES = [
    MemoryEntry(
        id="old-a", layer="L1", type="facts", content="preview-a",
        created_at=1730000000.0, updated_at=1730000000.0,
    ),
    MemoryEntry(
        id="old-b", layer="L1", type="facts", content="preview-b",
        created_at=1730000000.0, updated_at=1730000000.0,
    ),
]

_MERGE_JSON = (
    '[{"original_ids": ["old-a", "old-b"], "context": "修复部署报错时",'
    ' "consolidated_fact": "部署前必须迁移 schema", "type": "facts"}]'
)


@pytest.mark.asyncio
async def test_light_sleep_merge_archives_originals() -> None:
    store = _FakeMemoryStore(
        l1_entries=_L1_ENTRIES,
        fulltext={"old-a": "a 的完整原文", "old-b": "b 的完整原文"},
    )
    engine = _build_engine(store, raw_json=_MERGE_JSON)

    result = await engine.run_light("web:test")

    # 1. 合并件唯一 active
    assert len(store.stored) == 1
    assert store.stored[0].id != "old-a" and store.stored[0].id != "old-b"
    assert store.stored[0].layer == "L2"
    # 2. 原件必须 archive + 原因标记
    assert sorted(eid for eid, _ in store.archived) == ["old-a", "old-b"]
    assert all(reason == "light_sleep_merged" for _, reason in store.archived)
    # 3. 不允许只删 L1（制造副本的旧行为已移除）
    assert store.index_removed == []
    # 4. 统计口径：去重计数 = 下架原件数
    assert result["deduplicated"] == 2
    assert result["promoted_to_l2"] == 1