from __future__ import annotations

from typing import Any

from prismclaw.engine.compression._helpers import (
    _content_digest,
    _context_block_reference_id,
    _preview_text,
    _reference_id,
    _result_text,
)
from prismclaw.engine.compression._material_policy import ResultMaterialType
from prismclaw.engine.compression.semantic_fidelity import (
    build_preservation_contract,
    build_semantic_capsule,
    fidelity_metadata,
    validate_semantic_capsule,
)
from prismclaw.engine.state.projection_rendering import (
    build_result_compaction_placeholder,
    estimate_rendered_text_tokens,
    render_result_compaction_preview,
    render_result_compaction_preview_text,
)
from prismclaw.engine.state.state_protocol import (
    derive_state_payload,
    has_state_update_payload,
    normalize_state_payload,
)
from prismclaw.schema.agent.data_types import TextBlock
from prismclaw.schema.content.compaction import CompactionArtifact
from prismclaw.schema.content.content_types import ReferenceArtifact, ToolResult, UiArtifact
from prismclaw.schema.content.context import ContextBlock
from prismclaw.schema.content.replacement_store import replacement_decision_from_artifact
from prismclaw.schema.content.compaction import parse_compaction_artifact
from prismclaw.schema.content.replacement_state import ReplacementDecisionState


def _attach_compaction_metadata(
    result: ToolResult,
    tool_call: dict[str, Any],
    *,
    material_type: ResultMaterialType,
    original_chars: int,
    compacted_chars: int,
    strategies: list[str],
    state_source_text: str,
) -> ToolResult:
    reference_id = _reference_id(tool_call, material_type)
    replacement_text = _result_text(result)
    state_updates = _extract_result_state_updates(state_source_text)
    artifact = CompactionArtifact.for_tool_result(
        artifact_id=reference_id,
        tool_name=((tool_call or {}).get("function") or {}).get("name") or "",
        tool_call_id=result.tool_call_id,
        source_digest=_content_digest(state_source_text),
        material_type=material_type,
        original_chars=original_chars,
        compacted_chars=compacted_chars,
        preview=_preview_text(replacement_text, limit=280),
        replacement_text=replacement_text[:2000],
        strategies=strategies,
        frozen=True,
        state_updates=state_updates,
    )
    preservation_contract = build_preservation_contract(
        scope="tool_result",
        source_id=result.tool_call_id,
        source_text=state_source_text,
        state_updates=state_updates,
        source_refs=[result.tool_call_id],
        loss_budget=_loss_budget_for_strategies(strategies),
    )
    semantic_capsule = build_semantic_capsule(
        capsule_type="tool_result_summary",
        summary_text=replacement_text,
        contract=preservation_contract,
        source_text=state_source_text,
        source_span_refs=[result.tool_call_id],
        recovery_handle={
            "tool_call_id": result.tool_call_id,
            "state_updates": state_updates,
            "material_type": material_type,
        },
        loss_classification=_loss_classification_for_strategies(strategies),
        metadata={"strategies": list(strategies)},
    )
    validation = validate_semantic_capsule(preservation_contract, semantic_capsule)
    artifact_payload = artifact.to_metadata_dict()
    artifact_payload.update(
        fidelity_metadata(
            contract=preservation_contract,
            capsule=semantic_capsule,
            validation=validation,
        )
    )
    next_metadata = dict(result.metadata or {})
    next_metadata["result_compaction"] = artifact_payload
    reference_artifact = result.reference_artifact
    if reference_artifact is None:
        reference_artifact = ReferenceArtifact(artifact_type="tool_reference")
    else:
        reference_artifact = reference_artifact.model_copy(
            update={"artifact_type": reference_artifact.artifact_type or "tool_reference"}
        )
    return result.model_copy(
        update={
            "metadata": next_metadata,
            "ui_artifact": UiArtifact(
                artifact_type="result_compaction_preview",
                result_compaction_preview=artifact_payload,
            ),
            "reference_artifact": reference_artifact,
        }
    )


def _result_replacement_state(
    artifacts: list[Any],
) -> ReplacementDecisionState:
    decisions = []
    for artifact in artifacts:
        ui_artifact = artifact.result.ui_artifact
        compaction_meta = (
            ui_artifact.result_compaction_preview
            if ui_artifact is not None
            and ui_artifact.artifact_type == "result_compaction_preview"
            else None
        )
        parsed_artifact = parse_compaction_artifact(compaction_meta)
        decision = replacement_decision_from_artifact(parsed_artifact)
        if decision is not None:
            decisions.append(decision)
    return ReplacementDecisionState(decisions=decisions)


def _attach_context_block_compaction_metadata(
    block: ContextBlock,
    *,
    material_type: ResultMaterialType,
    original_chars: int,
    compacted_text: str,
    strategies: list[str],
    metadata: dict[str, Any] | None = None,
    artifact_id: str | None = None,
    source_digest: str | None = None,
    frozen: bool = False,
    preview_text: str | None = None,
) -> ContextBlock:
    next_metadata = dict(metadata or block.metadata or {})
    reference_id = artifact_id or _context_block_reference_id(block, material_type)
    raw_preview = preview_text or _preview_text(compacted_text, limit=280)
    artifact_model = CompactionArtifact.for_context_block(
        artifact_id=reference_id,
        block_source=block.source,
        block_id=block.id,
        source_digest=source_digest or _content_digest(block.content),
        material_type=material_type,
        original_chars=original_chars,
        compacted_chars=len(compacted_text),
        preview=raw_preview,
        replacement_text=compacted_text[:2000],
        strategies=strategies,
        frozen=frozen,
        state_updates=None,
    )
    projected_text = (
        render_result_compaction_preview(artifact_model, source=block.source)
        if "reference_preview" in strategies
        else compacted_text
    )
    state_updates = _extract_block_state_updates(block, projected_text)
    projected_tokens = estimate_rendered_text_tokens(projected_text)
    artifact_model.compacted_chars = len(projected_text)
    artifact_model.state_updates = state_updates
    artifact = artifact_model.to_metadata_dict()
    preservation_contract = build_preservation_contract(
        scope="context_block",
        source_id=block.id,
        source_text=block.content,
        state_updates=state_updates,
        source_refs=[block.id, *[str(ref) for ref in (next_metadata.get("evidence_packet_ids") or [])]],
        loss_budget=_loss_budget_for_strategies(strategies),
    )
    semantic_capsule = build_semantic_capsule(
        capsule_type="context_block_summary",
        summary_text=projected_text,
        contract=preservation_contract,
        source_text=block.content,
        source_span_refs=[block.id],
        recovery_handle={
            "block_id": block.id,
            "state_updates": state_updates,
            "source": block.source,
        },
        loss_classification=_loss_classification_for_strategies(strategies),
        metadata={"strategies": list(strategies), "frozen": frozen},
    )
    validation = validate_semantic_capsule(preservation_contract, semantic_capsule)
    artifact.update(
        fidelity_metadata(
            contract=preservation_contract,
            capsule=semantic_capsule,
            validation=validation,
        )
    )
    next_metadata["result_compaction"] = artifact
    next_metadata["projected_compacted_chars"] = len(projected_text)
    replaceable = block.replaceable or "reference_preview" in strategies
    return block.model_copy(
        update={
            "content": compacted_text,
            "estimated_tokens": projected_tokens,
            "state_payload": state_updates or block.state_payload,
            "metadata": next_metadata,
            "replaceable": replaceable,
        }
    )


def _replace_with_reference_preview(
    result: ToolResult,
    tool_call: dict[str, Any],
    *,
    material_type: ResultMaterialType,
    original_chars: int,
) -> ToolResult:
    preview = _preview_text(_result_text(result), limit=500)
    tool_name = ((tool_call or {}).get("function") or {}).get("name") or "tool"
    reference_id = _reference_id(tool_call, material_type)
    replacement = render_result_compaction_preview_text(
        material_type=material_type,
        source_name=tool_name,
        reference_id=reference_id,
        original_chars=original_chars,
        preview=preview,
        scope="tool_result",
    )
    return ToolResult(
        tool_call_id=result.tool_call_id,
        content=[TextBlock(text=replacement)],
        is_error=result.is_error,
        ui_artifact=result.ui_artifact,
        persistence_artifact=result.persistence_artifact,
        state_delta=result.state_delta,
        runtime_control=result.runtime_control,
        subagent_envelope=result.subagent_envelope,
        reference_artifact=result.reference_artifact,
        metadata=result.metadata,
    )


def _replace_context_block_with_reference_preview(
    block: ContextBlock,
    *,
    material_type: ResultMaterialType,
    original_chars: int,
) -> ContextBlock:
    metadata = dict(block.metadata or {})
    reference_id = _context_block_reference_id(block, material_type)
    replacement = build_result_compaction_placeholder(reference_id)
    return _attach_context_block_compaction_metadata(
        block,
        material_type=material_type,
        original_chars=original_chars,
        compacted_text=replacement,
        strategies=["aggregate_budget_compact", "reference_preview"],
        metadata=metadata,
        artifact_id=reference_id,
        preview_text=_preview_text(block.content, limit=500),
    )


def _extract_result_state_updates(text: str) -> dict[str, Any] | None:
    return derive_state_payload([{"role": "tool", "content": text}])


def _extract_block_state_updates(block: ContextBlock, _compacted_text: str) -> dict[str, Any] | None:
    if block.state_payload:
        normalized = normalize_state_payload(block.state_payload)
        return normalized if has_state_update_payload(normalized) else None
    return derive_state_payload([{"role": "tool", "content": block.content}])


def _loss_classification_for_strategies(strategies: list[str]) -> str:
    if "reference_preview" in strategies or "aggregate_budget_compact" in strategies:
        return "aggressive"
    if any(strategy.startswith("semantic_reduce:") for strategy in strategies):
        return "bounded_loss"
    return "lossless"


def _loss_budget_for_strategies(strategies: list[str]) -> str:
    if "reference_preview" in strategies or "aggregate_budget_compact" in strategies:
        return "high"
    if any(strategy.startswith("semantic_reduce:") for strategy in strategies):
        return "medium"
    return "low"
