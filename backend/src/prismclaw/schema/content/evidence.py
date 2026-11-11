from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

EvidenceTemporalMode = Literal["current_only", "include_history"]
EvidenceRelation = Literal["anchors", "derived_from", "related_to", "supersedes", "supports"]
EvidencePacketKind = Literal["anchor", "memory", "archive", "artifact"]


class EvidenceStrategy(BaseModel):
    strategy_id: str
    task_id: str | None = None
    session_id: str
    query_text: str | None = None
    strategy: str
    memory_layers: list[str] = Field(default_factory=list)
    per_layer_limits: dict[str, int] = Field(default_factory=dict)
    archive_limit: int = 0
    artifact_kinds: list[str] = Field(default_factory=list)
    graph_expansion_enabled: bool = False
    temporal_mode: EvidenceTemporalMode = "current_only"
    evidence_types: list[str] = Field(default_factory=list)
    anchor_sources: list[str] = Field(default_factory=list)
    merged_state_payload: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class EvidencePacket(BaseModel):
    packet_id: str
    kind: EvidencePacketKind
    source: str
    title: str
    content: str
    summary: str | None = None
    layer: str | None = None
    source_ref: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    freshness: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextGraphEdge(BaseModel):
    source_id: str
    target_id: str
    relation: EvidenceRelation
    reason: str | None = None


class ContextGraph(BaseModel):
    graph_id: str
    strategy_id: str | None = None
    packets: list[EvidencePacket] = Field(default_factory=list)
    edges: list[ContextGraphEdge] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
