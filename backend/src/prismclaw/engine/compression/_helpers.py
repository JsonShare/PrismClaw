from __future__ import annotations

import hashlib
from typing import Any

from prismclaw.schema.agent.data_types import TextBlock
from prismclaw.schema.content.content_types import ToolResult
from prismclaw.schema.content.context import ContextBlock


def _result_text(result: ToolResult) -> str:
    return "".join(block.text for block in result.content if isinstance(block, TextBlock))


def _result_text_len(result: ToolResult) -> int:
    return len(_result_text(result))


def _preview_text(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = max(120, int(limit * 0.7))
    tail = max(40, limit - head)
    return f"{text[:head]}\n...\n{text[-tail:]}"


def _content_digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _reference_id(tool_call: dict[str, Any], material_type: str) -> str:
    function_payload = (tool_call or {}).get("function")
    tool_name = (
        function_payload.get("name") if isinstance(function_payload, dict) else None
    ) or "tool"
    tool_call_id = (tool_call or {}).get("id") or "unknown"
    digest = hashlib.sha1(f"{tool_call_id}:{tool_name}:{material_type}".encode()).hexdigest()
    return f"rc_{digest[:12]}"


def _context_block_reference_id(block: ContextBlock, material_type: str) -> str:
    digest = hashlib.sha1(
        f"{block.id}:{block.source}:{material_type}:{len(block.content)}".encode()
    ).hexdigest()
    return f"cb_{digest[:12]}"
