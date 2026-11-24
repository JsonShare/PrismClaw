"""读边界隔离契约：单条非法存量不得毒死整批记忆读取。

写边界（MemoryEntry 构造 / store）保持严格校验（SDD D7）；读边界必须隔离，
否则一行历史遗留值会让整批检索、扫描与统计全部失败。
"""

from __future__ import annotations

import pytest

from prismclaw.persistence.memory._row_mapping import _entries_from_rows, _merge_memory_candidates


def _row(entry_id: str, entry_type: str) -> dict:
    return {
        "id": entry_id,
        "layer": "L2",
        "type": entry_type,
        "content": "c",
        "source_session_id": "s",
        "created_at": 1.0,
        "updated_at": 1.0,
        "last_used_at": None,
        "use_count": 0,
        "status": "active",
        "origin_stage": None,
        "confidence": 0.8,
        "stability_score": None,
        "cluster_id": None,
        "supersedes_json": [],
        "last_validated_at": None,
        "graph_group_id": None,
        "low_confidence": False,
    }


def test_entries_from_rows_drops_only_invalid_row() -> None:
    rows = [_row("good", "fact"), _row("bad", "procedures"), _row("good2", "decision")]

    entries = _entries_from_rows(rows, score=0.0)

    assert [e.id for e in entries] == ["good", "good2"]


def test_merge_memory_candidates_drops_only_poisoned_row() -> None:
    entries = _merge_memory_candidates(
        [_row("bad", "procedures"), _row("good", "fact")], [], limit=10
    )

    assert [e.id for e in entries] == ["good"]


def test_write_boundary_still_strict() -> None:
    """隔离只发生在读边界：构造期仍必须拒绝非法词表。"""
    from pydantic import ValidationError

    from prismclaw.schema.memory.memory_types import MemoryEntry

    with pytest.raises(ValidationError):
        MemoryEntry(
            id="x", layer="L2", type="procedures", content="c", created_at=0.0, updated_at=0.0
        )
