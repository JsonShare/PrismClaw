"""Tool output truncation utilities.

Extracted from engine/agent/run/tool_loop/turn_utils.py so that
compression modules can import them without depending on the agent layer.
"""

from __future__ import annotations

import json

from prismclaw.schema.content.content_types import ToolResult
from prismclaw.schema.agent.data_types import ContentBlock, ImageBlock, TextBlock


def _text_length(blocks: list[ContentBlock]) -> int:
    total = 0
    for block in blocks:
        if isinstance(block, TextBlock):
            total += len(block.text)
    return total


def _head_tail_budget(max_chars: int, *, prefer_tail: bool) -> tuple[int, int]:
    if max_chars <= 0:
        return 0, 0
    head_ratio = 0.25 if prefer_tail else 0.4
    head_len = int(max_chars * head_ratio)
    tail_len = max_chars - head_len
    return head_len, tail_len


def truncate_tool_result(result: ToolResult, max_chars: int) -> ToolResult:
    if max_chars <= 0:
        return result

    total_text_len = _text_length(result.content)
    if total_text_len <= max_chars:
        return result

    # Extract all text blocks
    text_blocks = [b for b in result.content if isinstance(b, TextBlock)]
    image_blocks = [b for b in result.content if isinstance(b, ImageBlock)]

    full_text = "".join(b.text for b in text_blocks)

    omitted_chars = max(0, len(full_text) - max_chars)

    head_len, tail_len = _head_tail_budget(max_chars, prefer_tail=result.is_error)
    kept_head = full_text[:head_len]
    kept_tail = full_text[-tail_len:] if tail_len > 0 else ""

    marker = (
        "\n\n[tool_result_truncated]\n"
        f"original_chars: {len(full_text)}\n"
        f"omitted_chars: {omitted_chars}\n"
        f"kept_head_chars: {head_len}\n"
        f"kept_tail_chars: {tail_len}\n"
        "[/tool_result_truncated]\n\n"
    )

    kept_text = kept_head + marker + kept_tail

    is_json = False
    try:
        parsed = json.loads(full_text)
        is_json = isinstance(parsed, (dict, list))
    except Exception:
        pass

    if is_json:
        snipped_text = json.dumps({
            "truncated": True,
            "message": "因长度超限被截断（保留头部与尾部）",
            "raw_preview": kept_head + "\n...[omitted]...\n" + kept_tail,
            "omitted_chars": omitted_chars,
            "kept_head_chars": head_len,
            "kept_tail_chars": tail_len,
        }, ensure_ascii=False)
        new_blocks: list[ContentBlock] = [TextBlock(text=snipped_text)]
    else:
        new_blocks = [TextBlock(text=kept_text)]
    new_blocks.extend(image_blocks)

    return ToolResult(
        tool_call_id=result.tool_call_id,
        content=new_blocks,
        is_error=result.is_error,
    )


def resolve_max_output_chars(tool, default_cap: int) -> int:
    override = getattr(tool, "max_output_chars", None)
    if override is None:
        return default_cap
    try:
        value = int(override)
    except (TypeError, ValueError):
        return default_cap
    return value if value >= 0 else default_cap
