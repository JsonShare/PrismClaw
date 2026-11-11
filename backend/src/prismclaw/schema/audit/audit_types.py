"""Retrieval audit type definitions — pure data, no behaviour.

Lives in contracts (L0) so that Protocol definitions can reference
RetrievalAuditEntry without depending on engine (L2).  See SDD §5.1.3.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class RetrievalAuditEntry:
    session_key: str
    retrieval_query: str
    started_at: float
    ended_at: float
    duration_s: float
    memory_candidate_count: int
    archive_candidate_count: int
    selected_memory_count: int
    selected_archive_count: int
    memory_text_candidate_count: int = 0
    memory_vector_candidate_count: int = 0
    archive_text_candidate_count: int = 0
    archive_vector_candidate_count: int = 0
    selected_layer_distribution: dict[str, int] = field(default_factory=dict)
    memory_source_mix: dict[str, int] = field(default_factory=dict)
    archive_source_mix: dict[str, int] = field(default_factory=dict)
    selector_strategy: str = "unknown"
    selector_fallback: bool = False
    planner_strategy: str = "unknown"
    planner_temporal_mode: str = "unknown"
    planner_memory_layers: list[str] = field(default_factory=list)
    planner_artifact_kinds: list[str] = field(default_factory=list)
    planner_graph_expansion_enabled: bool = False
    archive_cache_hit: bool = False
    memory_vector_fallback: bool = False
    archive_vector_fallback: bool = False
    l2l3_error: bool = False
    archive_error: bool = False
