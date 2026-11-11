from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field

from prismclaw.schema.content.compaction import CompactionArtifact, CompactionArtifactType

logger = logging.getLogger(__name__)

ReplacementScope = Literal["tool_result", "context_block"]


class ReplacementDecision(BaseModel):
    decision_id: str
    scope: ReplacementScope
    artifact_type: str
    source_id: str
    reference_id: str | None = None
    source_name: str | None = None
    source_digest: str | None = None
    material_type: str | None = None
    original_chars: int | None = None
    compacted_chars: int | None = None
    preview: str | None = None
    replacement_text: str | None = None
    strategies: list[str] = Field(default_factory=list)
    frozen: bool = True

    @classmethod
    def from_artifact(cls, artifact: CompactionArtifact) -> "ReplacementDecision | None":
        if artifact.scope not in {"tool_result", "context_block"}:
            logger.debug(
                "ReplacementDecision.from_artifact skipped: unsupported scope=%s artifact_id=%s",
                artifact.scope, artifact.artifact_id,
            )
            return None
        source_id = artifact.source_id or artifact.tool_call_id
        if not isinstance(source_id, str) or not source_id:
            logger.debug(
                "ReplacementDecision.from_artifact skipped: missing source_id artifact_id=%s",
                artifact.artifact_id,
            )
            return None
        decision_id = artifact.reference_id or artifact.artifact_id or source_id
        return cls(
            decision_id=decision_id,
            scope=artifact.scope,
            artifact_type=artifact.artifact_type,
            source_id=source_id,
            reference_id=artifact.reference_id or artifact.artifact_id,
            source_name=artifact.source_name,
            source_digest=artifact.source_digest,
            material_type=artifact.material_type,
            original_chars=artifact.original_chars,
            compacted_chars=artifact.compacted_chars,
            preview=artifact.preview,
            replacement_text=artifact.replacement_text,
            strategies=list(artifact.strategies),
            frozen=bool(artifact.frozen if artifact.frozen is not None else True),
        )

    def to_compaction_artifact(self) -> CompactionArtifact:
        return CompactionArtifact(
            artifact_type=self.artifact_type,  # type: ignore[arg-type]
            artifact_id=self.reference_id or self.decision_id,
            scope=self.scope,
            reference_id=self.reference_id or self.decision_id,
            material_type=self.material_type,
            source_name=self.source_name,
            source_id=self.source_id,
            source_digest=self.source_digest,
            tool_call_id=self.source_id if self.scope == "tool_result" else None,
            original_chars=self.original_chars,
            compacted_chars=self.compacted_chars,
            preview=self.preview,
            replacement_text=self.replacement_text,
            strategies=list(self.strategies),
            frozen=self.frozen,
        )

    def stable_payload(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "scope": self.scope,
            "artifact_type": self.artifact_type,
            "source_id": self.source_id,
            "reference_id": self.reference_id,
            "source_name": self.source_name,
            "source_digest": self.source_digest,
            "material_type": self.material_type,
            "original_chars": self.original_chars,
            "compacted_chars": self.compacted_chars,
            "preview": self.preview,
            "replacement_text": self.replacement_text,
            "strategies": list(self.strategies),
            "frozen": self.frozen,
        }


class ReplacementDecisionState(BaseModel):
    decisions: list[ReplacementDecision] = Field(default_factory=list)

    def latest_by_source_id(self) -> dict[str, ReplacementDecision]:
        latest: dict[str, ReplacementDecision] = {}
        for decision in self.decisions:
            latest[decision.source_id] = decision
        return latest

    def to_artifact_map(self) -> dict[str, CompactionArtifact]:
        valid: set[str] = set(get_args(CompactionArtifactType))
        out: dict[str, CompactionArtifact] = {}
        for source_id, decision in self.latest_by_source_id().items():
            if decision.artifact_type not in valid:
                continue
            out[source_id] = decision.to_compaction_artifact()
        return out

    def decision_count(self) -> int:
        return len(self.latest_by_source_id())

    def state_digest(self) -> str | None:
        normalized = [
            decision.stable_payload()
            for decision in sorted(
                self.latest_by_source_id().values(),
                key=lambda item: (item.scope, item.source_id, item.reference_id or item.decision_id),
            )
        ]
        if not normalized:
            return None
        payload = json.dumps(
            normalized,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]

    def merged_with(self, other: "ReplacementDecisionState | None") -> "ReplacementDecisionState":
        merged = self.latest_by_source_id()
        if other is not None:
            merged.update(other.latest_by_source_id())
        return ReplacementDecisionState(decisions=list(merged.values()))
