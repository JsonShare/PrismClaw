"""T0-2b 验收测试（SDD D16）：LLM selector 单次调用预算 + 超时启发式兜底。"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

import prismclaw.engine.compression.memory_selector as memory_selector
from prismclaw.engine.compression.memory_selector import select_relevant_memories
from prismclaw.schema.memory.memory_types import MemoryEntry


class _SlowLLM:
    async def complete(self, *, request: Any, temperature: Any = None) -> Any:
        await asyncio.sleep(1.0)  # 超过测试中 0.05s 的预算
        return _Response("{}")


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text
        self.usage = None


class _FastLLM:
    def __init__(self, text: str) -> None:
        self._text = text

    async def complete(self, *, request: Any, temperature: Any = None) -> Any:
        return _Response(self._text)


class _TaskProfile:
    pass


def _candidates() -> list[MemoryEntry]:
    now = time.time()
    return [
        MemoryEntry(
            id="m1", layer="L2", type="fact", content="候选事实一",
            created_at=now, updated_at=now, confidence=0.8,
        ),
        MemoryEntry(
            id="m2", layer="L3", type="procedure", content="候选流程二",
            created_at=now, updated_at=now, confidence=0.7,
        ),
    ]


@pytest.mark.asyncio
async def test_selector_timeout_falls_back_to_heuristic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(memory_selector, "_SELECTOR_LIMIT_S", 0.05)
    metrics: dict[str, Any] = {}
    memories, _archives = await select_relevant_memories(
        llm_client=_SlowLLM(),
        retrieval_query="任意查询",
        task_profile=_TaskProfile(),
        memory_candidates=_candidates(),
        archive_candidates=[],
        max_memory=5,
        max_archives=2,
        selector_metrics=metrics,
    )
    assert memories, "超时兜底必须返回启发式选中的条目"
    assert metrics["strategy"] == "heuristic_timeout"
    assert metrics["fallback"] is True


@pytest.mark.asyncio
async def test_selector_success_path_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(memory_selector, "_SELECTOR_LIMIT_S", 5.0)
    metrics: dict[str, Any] = {}
    memories, _archives = await select_relevant_memories(
        llm_client=_FastLLM('{"selected_memory_ids": ["m1"], "selected_archive_ids": []}'),
        retrieval_query="任意查询",
        task_profile=_TaskProfile(),
        memory_candidates=_candidates(),
        archive_candidates=[],
        max_memory=5,
        max_archives=2,
        selector_metrics=metrics,
    )
    assert [m.id for m in memories] == ["m1"]
    assert metrics["strategy"] == "llm"