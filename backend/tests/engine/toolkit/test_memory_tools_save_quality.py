"""T1-3 验收测试（SDD D7）：save 写侧质量控制——type 词表、置信度、来源标记、id 回传。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from prismclaw.engine.toolkit.memory.memory_tools import MemoryTool
from prismclaw.schema.memory.classify import CANONICAL_TYPES, normalize_type
from prismclaw.schema.memory.memory_types import MemoryEntry


def test_normalize_type_alias_mapping() -> None:
    assert normalize_type("facts") == "fact"
    assert normalize_type("general") == "fact"
    assert normalize_type("patient_medical_fact") == "fact"
    assert normalize_type("user_preference") == "preference"
    assert normalize_type("procedures") == "procedure"
    assert normalize_type("process") == "procedure"
    assert normalize_type("workflow") == "procedure"
    assert normalize_type("auto_sop") == "procedure"
    assert normalize_type(None) == "fact"
    for canonical in CANONICAL_TYPES:
        assert normalize_type(canonical) == canonical


class _FakeStore:
    def __init__(self) -> None:
        self.stored: list[MemoryEntry] = []

    async def store(self, session_key: str, entry: MemoryEntry) -> None:
        self.stored.append(entry)


@pytest.mark.asyncio
async def test_save_rejects_unknown_type() -> None:
    tool = MemoryTool(memory_store=_FakeStore(), session_store=None)  # type: ignore[arg-type]
    result = await tool._save(
        "call-1",
        {"content": "x", "layer": "L2", "type": "weird_vocab"},
        SimpleNamespace(session_id="web:test"),
    )
    assert "weird_vocab" in str(result)


@pytest.mark.asyncio
async def test_save_writes_quality_controlled_entry() -> None:
    store = _FakeStore()
    tool = MemoryTool(memory_store=store, session_store=None)  # type: ignore[arg-type]
    result = await tool._save(
        "call-2",
        {"content": "部署前必须迁移 schema", "layer": "L2", "type": "facts"},
        SimpleNamespace(session_id="web:test"),
    )
    assert len(store.stored) == 1
    entry = store.stored[0]
    assert entry.type == "fact"  # 归一
    assert entry.confidence == 0.7
    assert entry.origin_stage == "memorize_tool"
    assert entry.source_session_id == "web:test"
    assert entry.id[:8] in str(result)  # id 回传


@pytest.mark.asyncio
async def test_save_default_type_is_fact() -> None:
    store = _FakeStore()
    tool = MemoryTool(memory_store=store, session_store=None)  # type: ignore[arg-type]
    await tool._save(
        "call-3",
        {"content": "api 基址是 https://x", "layer": "L3"},
        SimpleNamespace(session_id="web:test"),
    )
    assert store.stored[0].type == "fact"
    assert store.stored[0].layer == "L3"