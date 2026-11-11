"""T0-2a 验收测试（SDD D2）：检索超时/异常降级为确定性启发式 fallback。

覆盖：
1. 检索挂起超时 → fallback 返回关键词命中的 active 条目 + packets；
2. 非超时异常（SQLAlchemyError 类）→ 同样 fallback；
3. fallback 自身挂起 → 0.5s 短超时后空结果（不挂回合）；
4. 连续 3 次失败 → 熔断打开，后续调用直走 fallback（不再触发慢检索）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from prismclaw.engine.compression.memory_retriever import MemoryRetriever
from prismclaw.schema.memory.memory_types import MemoryEntry


class _SlowSearchError(Exception):
    pass


class _FakeStore:
    """search 挂起/抛错由 flag 控制；list_entries_by_status 由 list_mode 控制。"""

    def __init__(self) -> None:
        self.search_calls = 0
        self.search_mode = "hang"  # hang | raise | ok
        self.list_mode = "ok"  # ok | hang
        self.entries: list[MemoryEntry] = [
            MemoryEntry(
                id="e1", layer="L2", type="fact",
                content="kafka 部署需要先迁移 schema",
                created_at=1.0, updated_at=1000.0,
            ),
            MemoryEntry(
                id="e2", layer="L3", type="procedure",
                content="完全无关的另一条记忆",
                created_at=1.0, updated_at=900.0,
            ),
        ]

    async def search(self, session_key: str, query: str, **kwargs: Any) -> list[MemoryEntry]:
        self.search_calls += 1
        if self.search_mode == "hang":
            await asyncio.sleep(10.0)
            return []
        if self.search_mode == "raise":
            raise _SlowSearchError("simulated sqlalchemy failure")
        return self.entries

    async def list_entries_by_status(
        self, session_key: str, statuses: list[str], *, limit: int = 500
    ) -> list[MemoryEntry]:
        if self.list_mode == "hang":
            await asyncio.sleep(10.0)
        return self.entries


class _FakeSettings:
    search_l2_quota = 3
    search_l3_quota = 2
    archive_max_results = 5
    archive_enabled = True
    archive_min_similarity = 0.5


class _FakeStrategy:
    query_text = "kafka 部署步骤"
    memory_layers = ["L2", "L3"]
    per_layer_limits = {"L2": 3, "L3": 2}
    temporal_mode = "current_only"


class _FakeTaskProfile:
    requires_grounding = False
    prefers_tooling = False
    risk_level = "low"


def _retriever(store: _FakeStore) -> MemoryRetriever:
    return MemoryRetriever(
        memory_store=store,  # type: ignore[arg-type]
        memory_settings=_FakeSettings(),  # type: ignore[arg-type]
        retrieval_budget_s=0.05,  # 极小预算立即触发超时
    )


@pytest.mark.asyncio
async def test_timeout_degrades_to_heuristic_fallback() -> None:
    store = _FakeStore()
    retriever = _retriever(store)
    l2l3, archives, packets = await retriever.retrieve_evidence(
        session_id="web:test",
        evidence_strategy=_FakeStrategy(),  # type: ignore[arg-type]
        task_profile=_FakeTaskProfile(),  # type: ignore[arg-type]
    )
    # 超时后拿到关键词命中的兜底结果（非空注入）
    assert [e.id for e in l2l3] == ["e1"]
    assert archives == []
    assert len(packets) == 1
    assert packets[0].packet_id == "memory:e1"


@pytest.mark.asyncio
async def test_error_degrades_to_heuristic_fallback() -> None:
    store = _FakeStore()
    store.search_mode = "raise"
    retriever = _retriever(store)
    l2l3, _a, _p = await retriever.retrieve_evidence(
        session_id="web:test",
        evidence_strategy=_FakeStrategy(),  # type: ignore[arg-type]
        task_profile=_FakeTaskProfile(),  # type: ignore[arg-type]
    )
    assert [e.id for e in l2l3] == ["e1"]


@pytest.mark.asyncio
async def test_fallback_hang_returns_empty_not_hang() -> None:
    store = _FakeStore()
    store.search_mode = "raise"
    store.list_mode = "hang"
    retriever = _retriever(store)
    l2l3, _a, _p = await retriever.retrieve_evidence(
        session_id="web:test",
        evidence_strategy=_FakeStrategy(),  # type: ignore[arg-type]
        task_profile=_FakeTaskProfile(),  # type: ignore[arg-type]
    )
    assert l2l3 == []


@pytest.mark.asyncio
async def test_circuit_opens_after_consecutive_failures() -> None:
    store = _FakeStore()
    retriever = _retriever(store)
    for _ in range(3):
        await retriever.retrieve_evidence(
            session_id="web:test",
            evidence_strategy=_FakeStrategy(),  # type: ignore[arg-type]
            task_profile=_FakeTaskProfile(),  # type: ignore[arg-type]
        )
    assert store.search_calls == 3
    # 熔断打开：第 4 次不再触发慢检索
    await retriever.retrieve_evidence(
        session_id="web:test",
        evidence_strategy=_FakeStrategy(),  # type: ignore[arg-type]
        task_profile=_FakeTaskProfile(),  # type: ignore[arg-type]
    )
    assert store.search_calls == 3
    assert retriever._retrieval_circuit_open_until > 0.0