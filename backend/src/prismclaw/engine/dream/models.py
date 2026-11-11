from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from prismclaw.schema.dream.dream_types import DreamPublishState, DreamMemoryKind, DreamArtifactKind

@dataclass(slots=True)
class DreamPublishableMemory:
    layer: Literal["L2", "L3"]
    memory_kind: DreamMemoryKind
    memory_type: str
    content: str
    session_id: str
    session_key: str
    scope: str | None = None
    entity_key: str | None = None
    confidence: float = 0.75
    evidence_count: int = 1
    prerequisites: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    supersedes: list[str] = field(default_factory=list)
    graph_group_id: str | None = None
    version_chain_id: str | None = None
    valid_from: float | None = None
    valid_to: float | None = None
    winner_flag: bool = True
    publish_state: DreamPublishState = "reviewed"
    origin_stage: str = "candidate_merge"


@dataclass(slots=True)
class DreamPublishResult:
    memory_id: str
    publish_state: DreamPublishState
    memory_kind: DreamMemoryKind
    layer: Literal["L2", "L3"]


@dataclass(slots=True)
class DreamPublishableArtifact:
    artifact_kind: DreamArtifactKind
    session_id: str
    session_key: str
    summary: str
    publish_state: DreamPublishState = "published"
    graph_group_id: str | None = None
    source_refs: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    origin_stage: str = "rem"


@dataclass(slots=True)
class DreamArtifactPublishResult:
    artifact_id: str
    artifact_kind: DreamArtifactKind
    publish_state: DreamPublishState
