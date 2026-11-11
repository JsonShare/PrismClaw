from __future__ import annotations

import json

from prismclaw.engine.execution.handoff.contract import render_handoff_request_handover
from prismclaw.engine.state.projection_rendering import (
    render_result_compaction_preview,
)
from prismclaw.schema.agent.data_types import TextBlock
from prismclaw.schema.content.compaction import CompactionArtifact
from prismclaw.schema.content.content_types import ToolResult
from prismclaw.schema.content.subagent import SubagentResultEnvelope

# Structural keys that identify a legacy subagent envelope serialized as plain
# tool-result text. Every envelope field is optional, so validating arbitrary
# JSON against SubagentResultEnvelope succeeds on unrelated payloads (e.g. glob
# emits {"paths": [], "count": 0}) and silently rewrites them into an empty
# "[subagent_result]" block. Require at least one envelope-specific key.
_SUBAGENT_ENVELOPE_KEYS = frozenset(
    {
        "findings",
        "evidence",
        "decisions",
        "open_questions",
        "artifact_refs",
        "recommended_next_actions",
        "confidence_assessment",
    }
)


def tool_result_to_llm_content(result: ToolResult) -> str:
    subagent_rendered = _render_subagent_envelope_handover(result)
    if subagent_rendered:
        return subagent_rendered
    result_artifact = _tool_result_compaction_artifact(result)
    if (
        result_artifact is not None
        and result_artifact.scope == "tool_result"
        and "reference_preview" in result_artifact.strategies
    ):
        return render_result_compaction_preview(
            result_artifact,
            source=result_artifact.source_name,
        )
    parts: list[str] = []
    for block in result.content:
        if isinstance(block, TextBlock):
            parts.append(block.text)
    text = "\n".join(parts) if parts else ""
    if not text.strip():
        return text
    sidecar = _render_tool_result_sidecar(result)
    if sidecar:
        return f"{sidecar}\n\n{text}"
    try:
        envelope_data = json.loads(text)
    except ValueError:
        return text
    if not isinstance(envelope_data, dict) or not (
        _SUBAGENT_ENVELOPE_KEYS & envelope_data.keys()
    ):
        return text
    try:
        envelope = SubagentResultEnvelope.model_validate_json(text)
    except ValueError:
        return text
    return _render_subagent_result_handover(envelope)


def _render_subagent_envelope_handover(result: ToolResult) -> str:
    envelope = result.subagent_envelope
    if envelope is None:
        return ""
    blocks: list[str] = []
    if envelope.result_envelope is not None:
        blocks.append(_render_subagent_result_handover(envelope.result_envelope))
    handoff_request = envelope.handoff_request
    if handoff_request is not None:
        blocks.append(
            render_handoff_request_handover(
                handoff_request.model_dump(mode="python", exclude_none=True)
            )
        )
    return "\n\n".join(block for block in blocks if block).strip()


def _tool_result_compaction_artifact(result: ToolResult) -> CompactionArtifact | None:
    artifact = result.ui_artifact
    if artifact is None or artifact.artifact_type != "result_compaction_preview":
        return None
    preview = artifact.result_compaction_preview
    if not isinstance(preview, dict):
        return None
    try:
        return CompactionArtifact.model_validate(preview)
    except Exception:
        return None


def _render_tool_result_sidecar(result: ToolResult) -> str:
    blocks: list[str] = []
    search_block = _render_search_evidence_sidecar(result)
    if search_block:
        blocks.append(search_block)
    extract_block = _render_extract_evidence_sidecar(result)
    if extract_block:
        blocks.append(extract_block)
    return "\n\n".join(blocks)


def _render_search_evidence_sidecar(result: ToolResult) -> str:
    reference = result.reference_artifact
    evidence = reference.search_evidence if reference is not None else None
    if not isinstance(evidence, dict):
        return ""
    lines = [
        "[machine_search_evidence]",
        "将此块视为权威的机器生成搜索证据。",
    ]
    query = evidence.get("query")
    if isinstance(query, str) and query.strip():
        lines.append(f"query: {query}")
    for key in (
        "search_hit_count",
        "usable_source_count",
        "extractable_source_count",
        "has_public_evidence",
    ):
        value = evidence.get(key)
        if isinstance(value, (str, int, float, bool)):
            lines.append(f"{key}: {value}")
    provider_statuses = evidence.get("provider_statuses")
    if isinstance(provider_statuses, dict) and provider_statuses:
        lines.append("provider_statuses:")
        for name, status in provider_statuses.items():
            if isinstance(name, str) and isinstance(status, str):
                lines.append(f"- {name}: {status}")
    lines.append("[/machine_search_evidence]")
    return "\n".join(lines)


def _render_extract_evidence_sidecar(result: ToolResult) -> str:
    reference = result.reference_artifact
    if reference is None:
        return ""
    lines = [
        "[machine_extract_evidence]",
        "将此块视为权威的机器生成提取证据。",
    ]
    for key, value in (
        ("extract_result_count", reference.extract_result_count),
        ("extract_source", reference.extract_source),
        ("extract_cached", reference.extract_cached),
    ):
        if isinstance(value, (str, int, float, bool)):
            lines.append(f"{key}: {value}")
    if len(lines) == 2:
        return ""
    lines.append("[/machine_extract_evidence]")
    return "\n".join(lines)


def _render_subagent_result_handover(envelope: SubagentResultEnvelope) -> str:
    lines = ["[subagent_result]", "summary:", envelope.summary.strip() or "(empty)"]
    for name, values in (
        ("findings", envelope.findings),
        ("decisions", envelope.decisions),
        ("open_questions", envelope.open_questions),
        ("artifact_refs", envelope.artifact_refs),
        ("recommended_next_actions", envelope.recommended_next_actions),
    ):
        if not values:
            continue
        lines.append(f"{name}:")
        for value in values:
            lines.append(f"- {value}")
    if envelope.evidence:
        lines.append("evidence:")
        for ev in envelope.evidence:
            ev_line = f"- [{ev.confidence}] {ev.source_type}: {ev.source_path}"
            if ev.source_location:
                ev_line += f" ({ev.source_location})"
            # 领域扩展（泛化渲染，保留域名前缀避免歧义）
            for key, value in ev.domain_extensions.items():
                ev_line += f" [{key}={value}]"
            # deprecated: 旧域字段兜底
            if not ev.domain_extensions:
                if ev.evidence_level:
                    ev_line += f" [evidence:{ev.evidence_level}]"
                if ev.recommendation_grade:
                    ev_line += f" [grade:{ev.recommendation_grade}]"
                if ev.legal_source_type:
                    ev_line += f" [legal:{ev.legal_source_type}]"
                if ev.legal_effective_date:
                    ev_line += f" effective:{ev.legal_effective_date}"
                if ev.legal_status:
                    ev_line += f" ({ev.legal_status})"
            lines.append(ev_line)
    if envelope.confidence_assessment:
        lines.append(f"confidence: {envelope.confidence_assessment}")
    lines.append("[/subagent_result]")
    return "\n".join(lines)
