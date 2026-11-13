"""Memory graph contracts — 边存储协议与领域常量。

E7 检索管线和 E6 关系抽取的跨层依赖统一锚点。
从 persistence.memory.edge_store 上移，使 engine 层无需直接依赖 persistence。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from prismclaw.schema.llm.types import LLMClient

# ── 领域常量 ──────────────────────────────────────────

VALID_PREDICATES: frozenset[str] = frozenset({
    "causes", "prohibits", "is_a", "precedes", "replaces", "conflicts",
})

DIRECTION_SENSITIVE_PREDICATES: frozenset[str] = frozenset({
    "causes", "prohibits", "precedes",
})


# ── 领域模型 ──────────────────────────────────────────

@dataclass
class MemoryEdge:
    """边数据模型 — EdgeStore 返回值的领域对象。"""
    id: str = ""
    session_key: str = ""
    source_id: str = ""
    target_id: str = ""
    predicate: str = ""
    condition: str = ""
    confidence: float = 1.0
    evidence: str | None = None
    status: str = "active"
    created_at: float = 0.0
    updated_at: float | None = None


# ── 存储协议 ──────────────────────────────────────────

@runtime_checkable
class EdgeStoreProtocol(Protocol):
    """边存储协议 — 检索管线和关系抽取的依赖锚点。"""

    async def store_edge(self, session_key: str, edge: MemoryEdge) -> None: ...
    async def traverse(
        self, session_key: str, source_ids: list[str], *,
        direction: str = "outgoing",
        max_hops: int = 2, max_edges: int = 100,
    ) -> list[MemoryEdge]: ...
    async def edge_degree(self, session_key: str, entry_id: str) -> int: ...
    async def edges_applicable_in_context(
        self, edges: list[MemoryEdge], context: str, llm_client: LLMClient,
    ) -> list[MemoryEdge]: ...


__all__ = [
    "VALID_PREDICATES",
    "DIRECTION_SENSITIVE_PREDICATES",
    "MemoryEdge",
    "EdgeStoreProtocol",
]
