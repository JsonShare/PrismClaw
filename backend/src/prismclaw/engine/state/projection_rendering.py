from __future__ import annotations

from prismclaw.engine.state.state_protocol import render_state_payload_block
from prismclaw.schema.content.context import ContextBlock
from prismclaw.schema.content.compaction import CompactionArtifact, parse_compaction_artifact
from prismclaw.schema.shared.utils import estimate_tokens

_HISTORY_COMPACTION_PLACEHOLDER_PREFIX = "[projection:history_compaction:"
_RESULT_COMPACTION_PLACEHOLDER_PREFIX = "[projection:result_compaction:"


def estimate_rendered_text_tokens(text: str) -> int:
    """CJK-calibrated token estimate for rendered context-block text.

    SDD context-governance W1 (residual copy): this feeds
    ``ContextBlock.estimated_tokens``, which BudgetOrchestrator /
    governance interceptor / turn_preparation all sum for overflow
    decisions — a ``len // 4`` copy here under-counts Chinese-heavy
    blocks by ~2.3x exactly like the pre-fix history estimator did.
    """
    return estimate_tokens(text)


def build_history_compaction_placeholder(artifact_id: str | None) -> str:
    suffix = artifact_id or "unknown"
    return f"{_HISTORY_COMPACTION_PLACEHOLDER_PREFIX}{suffix}]"


def build_result_compaction_placeholder(reference_id: str | None) -> str:
    suffix = reference_id or "unknown"
    return f"{_RESULT_COMPACTION_PLACEHOLDER_PREFIX}{suffix}]"


def render_history_compaction_block(summary_text: str | None) -> str | None:
    if not isinstance(summary_text, str) or not summary_text.strip():
        return None
    return render_state_payload_block(
        wrapper="history_compaction",
        state_payload=None,
        summary_text=summary_text,
    )


def render_result_compaction_preview(
    artifact: CompactionArtifact,
    *,
    source: str | None = None,
) -> str:
    return render_result_compaction_preview_text(
        material_type=artifact.material_type or "generic_text",
        source_name=artifact.source_name or source or "context",
        reference_id=artifact.reference_id or artifact.artifact_id,
        original_chars=max(artifact.original_chars or 0, 0),
        preview=str(artifact.preview or "").rstrip(),
        scope=artifact.scope,
    )


def render_result_compaction_preview_text(
    *,
    material_type: str,
    source_name: str,
    reference_id: str | None,
    original_chars: int,
    preview: str,
    scope: str | None,
) -> str:
    subject = (
        f"[compacted {material_type} from {source_name}]"
        if scope == "tool_result"
        else f"[compacted {material_type} context from {source_name}]"
    )
    return (
        f"{subject}\n"
        f"reference_id: {reference_id}\n"
        f"original_chars: {original_chars}\n"
        "preview:\n"
        f"{preview}"
    ).strip()


def projected_context_block(block: ContextBlock) -> ContextBlock:
    metadata = dict(block.metadata or {})

    history_artifact = parse_compaction_artifact(metadata.get("history_compaction"))
    if history_artifact is not None and history_artifact.scope == "history_summary":
        rendered = render_history_compaction_block(history_artifact.summary_text)
        if rendered:
            return block.model_copy(
                update={
                    "content": rendered,
                    "estimated_tokens": estimate_rendered_text_tokens(rendered),
                }
            )

    result_artifact = parse_compaction_artifact(metadata.get("result_compaction"))
    if (
        result_artifact is not None
        and result_artifact.scope == "context_block"
        and "reference_preview" in result_artifact.strategies
    ):
        rendered = render_result_compaction_preview(result_artifact, source=block.source)
        return block.model_copy(
            update={
                "content": rendered,
                "estimated_tokens": estimate_rendered_text_tokens(rendered),
            }
        )

    return block
