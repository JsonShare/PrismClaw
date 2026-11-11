from __future__ import annotations

import json
from typing import Any

from prismclaw.schema.agent.session import MessageEntry
from prismclaw.schema.session.types import CompactionEntry, CustomEntry, ModelChangeEntry, SessionTree
from prismclaw.schema.agent.data_types import FileBlock, ImageBlock, TextBlock
from prismclaw.schema.shared.utils import file_reference_text
from prismclaw.schema.content.compaction import coerce_compaction_artifact

_IMAGE_DATA_PREVIEW = 256


def render_session_markdown(tree: SessionTree) -> str:
    lines: list[str] = []
    header = tree.header
    lines.append(f"# Session `{header.id}`")
    lines.append("")
    lines.append(f"- Workspace: `{header.workspace_id}`")
    lines.append(f"- Agent: `{header.agent_id}`")
    lines.append(f"- Created: {header.created_at}")
    if header.parent_session:
        lines.append(f"- Parent session: `{header.parent_session}`")
    if header.model_override:
        lines.append(f"- Model override: `{header.model_override}`")
    lines.append("")

    branch = tree.get_branch()

    tool_responses: dict[str, MessageEntry] = {}
    for entry in branch:
        if isinstance(entry, MessageEntry) and entry.role == "tool" and entry.tool_call_id:
            tool_responses[entry.tool_call_id] = entry

    for entry in branch:
        if isinstance(entry, MessageEntry):
            if entry.role == "tool":
                continue
            lines.extend(_render_message_entry(entry, tool_responses))
        elif isinstance(entry, CompactionEntry):
            lines.extend(_render_compaction_entry(entry))
        elif isinstance(entry, ModelChangeEntry):
            lines.extend(_render_model_change_entry(entry))
        elif isinstance(entry, CustomEntry):
            lines.extend(_render_custom_entry(entry))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_session_json(tree: SessionTree) -> str:
    return json.dumps(tree.model_dump(mode="json"), ensure_ascii=False, indent=2)


def _render_message_entry(
    entry: MessageEntry, tool_responses: dict[str, MessageEntry]
) -> list[str]:
    lines: list[str] = []
    role_label = entry.role.upper()
    suffix = " (interrupted)" if entry.partial else ""
    lines.append(f"## {role_label}{suffix} — `{entry.id}` ({entry.timestamp})")
    lines.append("")
    lines.append(_format_content(entry.content))

    if entry.tool_calls:
        lines.append("")
        lines.append("**Tool calls**:")
        for tc in entry.tool_calls:
            tc_id = (tc or {}).get("id", "")
            fn = (tc or {}).get("function") or {}
            name = fn.get("name", "")
            args = fn.get("arguments")
            args_str = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
            lines.append(f"- `{name}` (id=`{tc_id}`)")
            lines.append(f"  - args: `{args_str}`")
            response = tool_responses.get(tc_id)
            if response is not None:
                lines.append("  - result:")
                lines.append(_indent(_format_content(response.content), 4))
    return lines


def _render_compaction_entry(entry: CompactionEntry) -> list[str]:
    lines = [
        f"## COMPACTION — `{entry.id}` ({entry.timestamp})",
        "",
        f"- Tokens before: {entry.tokens_before}",
        f"- First kept entry: `{entry.first_kept_entry_id}`",
        "",
        "**Summary**:",
        "",
        entry.summary,
    ]
    artifact = coerce_compaction_artifact(entry.details)
    if artifact is not None and artifact.artifact_type == "history_compaction_summary":
        lines.extend(
            [
                "",
                "**Artifact**:",
                "",
                f"- Artifact type: `{artifact.artifact_type}`",
                f"- Artifact id: `{artifact.artifact_id}`",
                f"- Source messages: {artifact.source_message_count if artifact.source_message_count is not None else '?'}",
                f"- Compacted messages: {artifact.compacted_message_count if artifact.compacted_message_count is not None else '?'}",
            ]
        )
        if artifact.preserved_identifiers:
            lines.append("- Preserved identifiers:")
            for ident in artifact.preserved_identifiers:
                lines.append(f"  - `{ident}`")
    return lines


def _render_model_change_entry(entry: ModelChangeEntry) -> list[str]:
    return [
        f"## MODEL_CHANGE — `{entry.id}` ({entry.timestamp})",
        "",
        f"- Provider: `{entry.provider}`",
        f"- Model: `{entry.model_id}`",
    ]


def _render_custom_entry(entry: CustomEntry) -> list[str]:
    body = json.dumps(entry.data or {}, ensure_ascii=False, indent=2)
    return [
        f"## CUSTOM — `{entry.id}` ({entry.timestamp})",
        "",
        f"- Custom type: `{entry.custom_type}`",
        "",
        "```json",
        body,
        "```",
    ]


def _format_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, ImageBlock):
                preview = block.data[:_IMAGE_DATA_PREVIEW]
                parts.append(f"[image: {block.mime_type}, {preview}…]")
            elif isinstance(block, FileBlock):
                parts.append(
                    file_reference_text(
                        block.path,
                        name=block.name,
                        mime_type=block.mime_type,
                    )
                )
            elif isinstance(block, TextBlock):
                parts.append(block.text)
            elif isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
                elif block.get("type") in ("image", "image_url"):
                    parts.append("[image]")
                elif block.get("type") == "file" and isinstance(block.get("path"), str):
                    parts.append(
                        file_reference_text(
                            str(block["path"]),
                            name=str(block.get("name", "")) or None,
                            mime_type=str(block.get("mime_type", "")) or None,
                        )
                    )
                else:
                    parts.append(json.dumps(block, ensure_ascii=False))
        return "\n".join(parts)
    return str(content)


def _indent(text: str, n: int) -> str:
    pad = " " * n
    return "\n".join(pad + line for line in text.splitlines())
