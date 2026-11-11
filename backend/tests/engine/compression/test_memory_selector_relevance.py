"""T1-1 验收测试（SDD D5）：启发式路径相关性先行。

旧 rank key 以 layer_bias 为首位（L3=1.0 > L2=0.8）——低相关的 L3 条目会
压制高相关的 L2 条目。重构后相关性带（检索序折算）支配选择，层级仅同带内
tie-break。
"""

from __future__ import annotations

import time

from prismclaw.engine.compression.memory_selector import _heuristic_select
from prismclaw.schema.memory.memory_types import MemoryEntry, ArchiveEntry


def _entry(entry_id: str, layer: str, score: float) -> MemoryEntry:
    now = time.time()
    return MemoryEntry(
        id=entry_id,
        layer=layer,  # type: ignore[arg-type]
        type="fact",
        content=f"content-{entry_id}",
        created_at=now,
        updated_at=now,
        confidence=0.8,
        score=score,
    )


def test_high_relevance_l2_beats_low_relevance_l3() -> None:
    """高相关 L2 必须赢过低相关 L3（旧逻辑会因 layer_bias 选 L3）。

    band 覆盖：rank≤3→band3。低相关 L3 需落在 band 之外（rank≥4）才构成
    跨带对比；同带内 L3 tie-break 保留（见下一条测试）。
    """
    now = time.time()
    high_l2 = MemoryEntry(
        id="high-l2", layer="L2", type="fact", content="c",
        created_at=now, updated_at=now, confidence=0.8, score=1.0 / 61.0,
    )
    fillers = [
        MemoryEntry(
            id=f"f{i}", layer="L2", type="fact", content="c",
            created_at=now, updated_at=now, confidence=0.8,
            score=1.0 / (61.0 + i),
        )
        for i in range(1, 6)  # 占据 rank 2-6
    ]
    low_l3 = MemoryEntry(
        id="low-l3", layer="L3", type="fact", content="c",
        created_at=now, updated_at=now, confidence=1.0,  # 置信度甚至更高
        score=1.0 / 121.0,  # rank 7 → band 2
    )
    memories, _archives = _heuristic_select(
        memory_candidates=[low_l3, high_l2, *fillers],
        archive_candidates=[],
        max_memory=1,
        max_archives=0,
    )
    assert [m.id for m in memories] == ["high-l2"]


def test_same_band_layer_tiebreak_prefers_l3() -> None:
    """同相关性带内，L3 仍优先（层级 tie-break 语义保留）。"""
    m1 = _entry("l2-a", "L2", score=1.0 / 61.0)  # band 3
    m2 = _entry("l3-a", "L3", score=1.0 / 62.0)  # band 3
    memories, _archives = _heuristic_select(
        memory_candidates=[m1, m2],
        archive_candidates=[],
        max_memory=1,
        max_archives=0,
    )
    assert [m.id for m in memories] == ["l3-a"]


def test_composite_path_untouched() -> None:
    """composites 路径（ranking v2）排序不受两阶段改动影响。"""
    m1 = _entry("m1", "L2", score=0.01)
    m2 = _entry("m2", "L3", score=0.01)
    memories, _archives = _heuristic_select(
        memory_candidates=[m1, m2],
        archive_candidates=[],
        max_memory=1,
        max_archives=0,
        composites={"m2": 0.9, "m1": 0.5},
    )
    assert [m.id for m in memories] == ["m2"]