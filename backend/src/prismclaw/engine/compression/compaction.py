from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from prismclaw.schema.config import (
    DEFAULT_COMPACTION_SAFETY_TIMEOUT_S,
    DEFAULT_KEEP_RECENT_TOKENS,
    DEFAULT_THRESHOLD,
)

def _image_token_estimate() -> int:
    """单张内联图片的 token 估算——唯一来源：models.yaml 的 vision.tokens_per_image。

    配置加载失败时回退 schema 默认（1024）。
    """
    try:
        from prismclaw.infra.settings import load_settings

        return int(load_settings().vision.tokens_per_image)
    except Exception:
        return 1024

_T = TypeVar("_T")


async def compact_with_safety_timeout(
    fn: Callable[[], Awaitable[_T]],
    *,
    timeout_s: float = DEFAULT_COMPACTION_SAFETY_TIMEOUT_S,
    abort_event: asyncio.Event | None = None,
    on_cancel: Callable[[], None] | None = None,
) -> _T:
    from prismclaw.engine.foundation.runtime_util import with_safety_timeout

    return await with_safety_timeout(
        fn,
        timeout_s=timeout_s,
        abort_event=abort_event,
        on_cancel=on_cancel,
        kind="compaction",
    )


def estimate_tokens(text: str) -> int:
    """Delegate to the token-counting plugin (historical import surface)."""
    from prismclaw.token_counting import count_text

    return count_text(text)


def estimate_message_tokens(msg: dict[str, Any]) -> int:
    """Delegate message accounting to the token-counting plugin."""
    from prismclaw.token_counting import count_message

    return count_message(msg, image_tokens_each=_image_token_estimate())


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    from prismclaw.token_counting import count_messages

    return count_messages(messages, image_tokens_each=_image_token_estimate())


@dataclass
class CompactionStrategy:
    should_compact: bool
    estimated_tokens: int
    cut_index: int | None = None
    kept_tokens: int = 0
    reason: str | None = None


def find_cut_point(
    messages: list[dict[str, Any]],
    *,
    keep_recent_tokens: int = DEFAULT_KEEP_RECENT_TOKENS,
) -> int | None:
    if len(messages) <= 1:
        return None

    accumulated = 0
    cut_index: int | None = None
    for i in range(len(messages) - 1, -1, -1):
        accumulated += estimate_message_tokens(messages[i])
        if accumulated >= keep_recent_tokens:
            cut_index = i
            break

    if cut_index is None or cut_index <= 0:
        return None

    cut_index = _adjust_for_tool_boundaries(messages, cut_index)
    return cut_index if cut_index > 0 else None


def _adjust_for_tool_boundaries(messages: list[dict[str, Any]], cut_index: int) -> int:
    idx = cut_index
    while idx > 0 and messages[idx].get("role") == "tool":
        idx -= 1
    while idx > 0:
        prev = messages[idx - 1]
        if prev.get("role") == "assistant" and prev.get("tool_calls"):
            break
        if idx >= len(messages):
            break
        if messages[idx].get("role") == "tool":
            idx -= 1
            continue
        break
    return idx


def build_compaction(
    messages: list[dict[str, Any]],
    *,
    context_window: int,
    threshold: float = DEFAULT_THRESHOLD,
    keep_recent_tokens: int = DEFAULT_KEEP_RECENT_TOKENS,
) -> CompactionStrategy:
    estimated = estimate_messages_tokens(messages)
    budget = int(context_window * threshold)
    if estimated <= budget:
        return CompactionStrategy(
            should_compact=False,
            estimated_tokens=estimated,
            reason="within-budget",
        )
    cut = find_cut_point(messages, keep_recent_tokens=keep_recent_tokens)
    if cut is None:
        return CompactionStrategy(
            should_compact=False,
            estimated_tokens=estimated,
            reason="no-safe-cut-point",
        )
    kept = estimate_messages_tokens(messages[cut:])
    return CompactionStrategy(
        should_compact=True,
        estimated_tokens=estimated,
        cut_index=cut,
        kept_tokens=kept,
    )


SUMMARIZER_SYSTEM_PROMPT = (
    "请简洁地总结此前的工作进展。"
    "1. 目标是什么？ 2. 已完成或发现了什么？ 3. 下一个立即执行的步骤是什么？"
    "不要写客套话。返回纯文本。"
)


def extract_identifiers(text: str) -> set[str]:
    """Extract physical identifiers (paths, UUIDs/Hashes) from text for immune preservation."""
    identifiers = set()
    # Match absolute paths
    path_matches = re.finditer(r'(/(?:[\w.-]+/)*[\w.-]+\.\w+)', text)
    for m in path_matches:
        identifiers.add(m.group(1))

    # Match UUIDs/Hashes (8 to 64 hex chars bounded by word boundaries)
    hash_matches = re.finditer(r'\b[0-9a-fA-F]{8,64}\b', text)
    for m in hash_matches:
        # filter out simple numbers
        if not m.group(0).isdigit():
            identifiers.add(m.group(0))

    return identifiers


def build_summarizer_payload(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    transcript_lines: list[str] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content")
        if isinstance(content, str) and content.strip():
            transcript_lines.append(f"{role}: {content}")
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    transcript_lines.append(f"{role}: {block.get('text', '')}")
        for call in m.get("tool_calls") or []:
            fn = (call or {}).get("function") or {}
            transcript_lines.append(
                f"{role}[tool_call]: {fn.get('name', '?')}({fn.get('arguments', '')})"
            )

    transcript = "\n".join(transcript_lines) if transcript_lines else "(empty)"
    user_prompt = (
        "请总结以下对话，使助手能够在不丢失上下文的情况下继续：\n\n"
        + transcript
    )
    return [
        {"role": "system", "content": SUMMARIZER_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
