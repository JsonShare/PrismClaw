"""T1-6 验收（SDD D11）：L1 注入上限与 subagents 目录 token 预算截断。"""

from __future__ import annotations

from prismclaw.engine.execution.query._catalog_builder import (
    _build_available_subagents_catalog,
)
from prismclaw.engine.execution.query._static_context_types import (
    MAX_L1_SNAPSHOT_LINES,
    format_l1_snapshot,
)
from prismclaw.schema.memory.memory_types import MemoryEntry


def _l1_entry(entry_id: str, content: str, status: str = "active") -> MemoryEntry:
    return MemoryEntry(
        id=entry_id, layer="L1", type="fact", content=content,
        created_at=1.0, updated_at=1.0, status=status,
    )


def test_l1_snapshot_cap_with_trailer() -> None:
    entries = [_l1_entry(f"e{i}", f"记忆 {i}") for i in range(20)]
    text = format_l1_snapshot(entries)
    lines = text.splitlines()
    assert lines[0] == "<memory_index>"
    assert lines[-1] == "</memory_index>"
    body = lines[1:-1]
    assert len(body) == MAX_L1_SNAPSHOT_LINES + 1  # 15 条 + trailer
    assert body[-1].startswith("- …及 5 条")


def test_l1_snapshot_filters_non_active() -> None:
    entries = [
        _l1_entry("a", "active 条目"),
        _l1_entry("b", "archived 条目", status="archived"),
    ]
    text = format_l1_snapshot(entries)
    assert "active 条目" in text
    assert "archived 条目" not in text


class _FakeEntry:
    def __init__(self, index: int) -> None:
        self.name = f"agent-{index}"
        self.description = "d" * 200
        self.plugin_name = "p"
        self.marketplace_name = "mp"
        self.marketplace_description = "x" * 200
        self.can_handoff_to = []


class _FakeCatalog:
    def __init__(self, count: int) -> None:
        self._entries = [_FakeEntry(i) for i in range(count)]

    def list_agent_entries(self) -> list[_FakeEntry]:
        return self._entries


def test_subagents_catalog_is_full_and_bounded() -> None:
    """目录全量返回（固定 prompt 只渲染核心子代理，体积由渲染侧裁剪）。"""
    entries = _build_available_subagents_catalog(
        capability_catalog=_FakeCatalog(30),  # type: ignore[arg-type]
        tool_summaries=[("sub_agent", "desc")],
    )
    assert len(entries) == 30
    assert all(e.plugin_name == "p" for e in entries)
    assert all(len(e.description) <= 101 for e in entries)  # 100 字 + 省略号


def test_subagents_catalog_empty_when_tool_absent() -> None:
    entries = _build_available_subagents_catalog(
        capability_catalog=_FakeCatalog(5),  # type: ignore[arg-type]
        tool_summaries=[],
    )
    assert entries == ()