"""Memory data models — Pydantic BaseModel definitions.

Moved from prismclaw.schema.memory.entry to prismclaw.schema.memory
so that contracts can reference these models without creating a kernel→contracts
circular dependency.  kernel.memory.entry re-exports for backward compatibility.
"""
from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field

MemoryLayer = Literal["L1", "L2", "L3"]
L4ArtifactKind = Literal["archive", "pattern", "graph", "relation"]


class MemoryEntry(BaseModel):
    id: str
    layer: MemoryLayer
    type: str
    content: str
    scope: str | None = None
    entity_key: str | None = None
    source_session_id: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    created_at: float
    updated_at: float
    last_used_at: float | None = None
    use_count: int = 0
    status: str = "active"
    score: float | None = None
    origin_stage: str | None = None
    confidence: float | None = None
    stability_score: float | None = None
    cluster_id: str | None = None
    last_validated_at: float | None = None
    graph_group_id: str | None = None
    version_chain_id: str | None = None
    publish_state: str | None = None
    valid_from: float | None = None
    valid_to: float | None = None
    winner_flag: bool = True
    low_confidence: bool = False
    supersedes: list[str] = Field(default_factory=list)
    expires_at: float | None = None
    retrieval_path: str | None = None


class ArchiveEntry(BaseModel):
    id: str
    session_id: str
    summary: str
    created_at: float
    distance: float | None = None
    similarity: float | None = None
    low_confidence: bool = False


class L4ArtifactEntry(BaseModel):
    id: str
    artifact_kind: L4ArtifactKind
    session_id: str
    summary: str
    created_at: float
    updated_at: float | None = None
    graph_group_id: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    origin_stage: str | None = None
    publish_state: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    score: float | None = None
    distance: float | None = None
    similarity: float | None = None
    low_confidence: bool = False


def build_archive_artifact_id(session_id: str) -> str:
    return f"archive:{session_id}"


def build_archive_artifact_entry(
    session_id: str,
    summary: str,
    *,
    created_at: float | None = None,
    updated_at: float | None = None,
    artifact_id: str | None = None,
    publish_state: str | None = "published",
    payload: dict[str, Any] | None = None,
    low_confidence: bool = False,
) -> L4ArtifactEntry:
    timestamp = float(created_at or time.time())
    return L4ArtifactEntry(
        id=artifact_id or build_archive_artifact_id(session_id),
        artifact_kind="archive",
        session_id=session_id,
        summary=summary,
        created_at=timestamp,
        updated_at=updated_at or timestamp,
        publish_state=publish_state,
        origin_stage="session_archive",
        payload=payload or {"source": "session_archive"},
        low_confidence=low_confidence,
    )


def archive_entry_from_artifact(entry: L4ArtifactEntry) -> ArchiveEntry:
    return ArchiveEntry(
        id=entry.id,
        session_id=entry.session_id,
        summary=entry.summary,
        created_at=entry.created_at,
        distance=entry.distance,
        similarity=entry.similarity,
        low_confidence=entry.low_confidence,
    )
