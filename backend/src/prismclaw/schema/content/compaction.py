from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from prismclaw.schema.content.context import ContextPackLayout, TaskProfile
from prismclaw.schema.content.evidence import ContextGraph, EvidenceStrategy
from prismclaw.schema.content import (
    PreservationContract,
    SemanticCapsule,
)


CompactionArtifactType = Literal[
    "tool_result_compaction",
    "context_block_compaction",
    "history_compaction_summary",
]
CompactionArtifactScope = Literal["tool_result", "context_block", "history_summary"]


class CompactionPreservedSegment(BaseModel):
    head_entry_id: str | None = None
    anchor_entry_id: str | None = None
    tail_entry_id: str | None = None


class CompactionBoundary(BaseModel):
    boundary_entry_id: str | None = None
    session_id: str | None = None
    source_head_entry_id: str | None = None
    source_tail_entry_id: str | None = None
    first_kept_entry_id: str | None = None
    preserved_segment: CompactionPreservedSegment | None = None


class CompactionArtifact(BaseModel):
    artifact_type: CompactionArtifactType
    artifact_id: str
    scope: CompactionArtifactScope
    reference_id: str | None = None
    material_type: str | None = None
    source_name: str | None = None
    source_id: str | None = None
    source_digest: str | None = None
    tool_call_id: str | None = None
    original_chars: int | None = None
    compacted_chars: int | None = None
    preview: str | None = None
    replacement_text: str | None = None
    summary_text: str | None = None
    strategies: list[str] = Field(default_factory=list)
    frozen: bool | None = None
    state_updates: dict[str, Any] | None = None
    source_message_count: int | None = None
    compacted_message_count: int | None = None
    token_budget: int | None = None
    preserved_identifiers: list[str] = Field(default_factory=list)
    boundary: CompactionBoundary | None = None

    @classmethod
    def for_tool_result(
        cls,
        *,
        artifact_id: str,
        tool_name: str,
        tool_call_id: str | None,
        source_digest: str | None,
        material_type: str,
        original_chars: int,
        compacted_chars: int,
        preview: str,
        replacement_text: str,
        strategies: list[str],
        frozen: bool,
        state_updates: dict[str, Any] | None = None,
    ) -> "CompactionArtifact":
        return cls(
            artifact_type="tool_result_compaction",
            artifact_id=artifact_id,
            scope="tool_result",
            reference_id=artifact_id,
            material_type=material_type,
            source_name=tool_name,
            source_id=tool_call_id,
            source_digest=source_digest,
            tool_call_id=tool_call_id,
            original_chars=original_chars,
            compacted_chars=compacted_chars,
            preview=preview,
            replacement_text=replacement_text,
            strategies=list(dict.fromkeys(strategies)),
            frozen=frozen,
            state_updates=state_updates,
        )

    @classmethod
    def for_context_block(
        cls,
        *,
        artifact_id: str,
        block_source: str,
        block_id: str,
        source_digest: str | None,
        material_type: str,
        original_chars: int,
        compacted_chars: int,
        preview: str,
        replacement_text: str,
        strategies: list[str],
        frozen: bool,
        state_updates: dict[str, Any] | None = None,
    ) -> "CompactionArtifact":
        return cls(
            artifact_type="context_block_compaction",
            artifact_id=artifact_id,
            scope="context_block",
            reference_id=artifact_id,
            material_type=material_type,
            source_name=block_source,
            source_id=block_id,
            source_digest=source_digest,
            original_chars=original_chars,
            compacted_chars=compacted_chars,
            preview=preview,
            replacement_text=replacement_text,
            strategies=list(dict.fromkeys(strategies)),
            frozen=frozen,
            state_updates=state_updates,
        )

    @classmethod
    def for_history_summary(
        cls,
        *,
        artifact_id: str,
        source_id: str,
        summary_text: str,
        source_message_count: int,
        compacted_message_count: int,
        token_budget: int,
        preserved_identifiers: list[str],
        state_updates: dict[str, Any] | None = None,
        boundary: CompactionBoundary | None = None,
    ) -> "CompactionArtifact":
        return cls(
            artifact_type="history_compaction_summary",
            artifact_id=artifact_id,
            scope="history_summary",
            source_id=source_id,
            summary_text=summary_text,
            source_message_count=source_message_count,
            compacted_message_count=compacted_message_count,
            token_budget=token_budget,
            preserved_identifiers=preserved_identifiers,
            frozen=True,
            state_updates=state_updates,
            boundary=boundary,
        )

    def to_metadata_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


def parse_compaction_artifact(data: Any | None) -> CompactionArtifact | None:
    if isinstance(data, CompactionArtifact):
        return data
    if not isinstance(data, dict) or not data:
        return None
    try:
        return CompactionArtifact.model_validate(data)
    except Exception:
        return None


def coerce_compaction_artifact(data: dict[str, Any] | None) -> CompactionArtifact | None:
    artifact = parse_compaction_artifact(data)
    if artifact is not None:
        return artifact
    if not isinstance(data, dict) or not data:
        return None

    reference_id = data.get("reference_id")
    if isinstance(reference_id, str) and reference_id:
        if data.get("scope") == "context_block":
            return CompactionArtifact.for_context_block(
                artifact_id=reference_id,
                block_source=str(data.get("block_source") or ""),
                block_id=str(data.get("block_id") or ""),
                source_digest=(str(data.get("source_digest")) if data.get("source_digest") is not None else None),
                material_type=str(data.get("material_type") or "generic_text"),
                original_chars=int(data.get("original_chars") or 0),
                compacted_chars=int(data.get("compacted_chars") or 0),
                preview=str(data.get("preview") or ""),
                replacement_text=str(data.get("replacement_text") or ""),
                strategies=[str(item) for item in data.get("strategies") or []],
                frozen=bool(data.get("frozen")),
            )
        return CompactionArtifact.for_tool_result(
            artifact_id=reference_id,
            tool_name=str(data.get("tool_name") or ""),
            tool_call_id=(str(data.get("tool_call_id")) if data.get("tool_call_id") is not None else None),
            source_digest=(str(data.get("source_digest")) if data.get("source_digest") is not None else None),
            material_type=str(data.get("material_type") or "generic_text"),
            original_chars=int(data.get("original_chars") or 0),
            compacted_chars=int(data.get("compacted_chars") or 0),
            preview=str(data.get("preview") or ""),
            replacement_text=str(data.get("replacement_text") or ""),
            strategies=[str(item) for item in data.get("strategies") or []],
            frozen=bool(data.get("frozen")),
        )

    if data.get("artifact_type") == "history_compaction_summary" or isinstance(
        data.get("summary_text"), str
    ):
        artifact_id = str(data.get("artifact_id") or "")
        if not artifact_id:
            return None
        return CompactionArtifact.for_history_summary(
            artifact_id=artifact_id,
            source_id=str(data.get("source_id") or ""),
            summary_text=str(data.get("summary_text") or ""),
            source_message_count=int(data.get("source_message_count") or 0),
            compacted_message_count=int(data.get("compacted_message_count") or 0),
            token_budget=int(data.get("token_budget") or 0),
            preserved_identifiers=[str(item) for item in data.get("preserved_identifiers") or []],
        )
    return None


class CompressionValidationResult(BaseModel):
    ok: bool
    checked_count: int = 0
    preserved_count: int = 0
    missing_invariants: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DecisionLedgerEvent(BaseModel):
    event_id: str
    session_id: str
    task_id: str
    event_type: str
    actor: str = "runner"
    turn_index: int = 1
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class AssembleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    messages: list[dict[str, Any]]
    context_layout: ContextPackLayout = Field(default_factory=lambda: ContextPackLayout())
    task_profile: TaskProfile | None = None
    evidence_strategy: EvidenceStrategy | None = None
    context_graph: ContextGraph | None = None
    estimated_tokens: int = 0

    def model_post_init(self, __context: Any) -> None:
        if self.estimated_tokens <= 0:
            self.estimated_tokens = sum(
                block.estimated_tokens for block in self.context_layout.all_blocks()
            )


CompactionReasonCode = Literal[
    "compacted",
    "no_compactable_entries",
    "below_threshold",
    "already_compacted_recently",
    "live_context_still_exceeds_target",
    "summary_failed",
    "handler_error",
    "timeout",
    "aborted",
    "prompt_too_long",
    "compaction_failed",
    "provider_error_4xx",
    "provider_error_5xx",
    "unknown",
]


class CompactResult(BaseModel):
    ok: bool
    compacted: bool
    reason: str | None = None
    reason_code: CompactionReasonCode | None = None
    summary: str | None = None
    details: dict[str, Any] | None = None
    first_kept_entry_id: str | None = None
    tokens_before: int = 0
    tokens_after: int | None = None
    preservation_contract: PreservationContract | None = None
    semantic_capsule: SemanticCapsule | None = None
    validation: CompressionValidationResult | None = None
