from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

PreservationItemKind = Literal["constraint", "decision", "question", "fact", "identifier", "artifact_ref"]
LossClassification = Literal["lossless", "bounded_loss", "aggressive"]


class PreservationItem(BaseModel):
    kind: PreservationItemKind
    content: str
    required: bool = True


class PreservationContract(BaseModel):
    contract_id: str
    scope: Literal["history", "tool_result", "context_block"]
    source_id: str | None = None
    loss_budget: Literal["low", "medium", "high"] = "medium"
    invariants: list[PreservationItem] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SemanticCapsule(BaseModel):
    capsule_id: str
    capsule_type: Literal["history_summary", "tool_result_summary", "context_block_summary"]
    summary_text: str
    preserved_invariants: list[str] = Field(default_factory=list)
    source_span_refs: list[str] = Field(default_factory=list)
    loss_classification: LossClassification = "bounded_loss"
    recovery_handle: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
