"""Shared utilities for content block handling.

This module consolidates the previously-duplicated "extract text from
possibly-list content" logic scattered across compaction/dedup.py,
compaction/hardening.py, sop_extraction.py, and
memory_archive.py.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from prismclaw import token_counting as _token_counting
from prismclaw.token_counting import count_text as _count_text

# 历史常量名保留为可导入的再导出（测试与外部调用方依赖）。
_CJK_TOKENS_PER_CHAR = _token_counting.CJK_TOKENS_PER_CHAR
_OTHER_CHARS_PER_TOKEN = _token_counting.OTHER_CHARS_PER_TOKEN
_estimator_v2_enabled = _token_counting._estimator_v2_enabled

IMAGE_PLACEHOLDER = "[图片]"
FILE_PLACEHOLDER = "[附件]"

# 输入框内联卡片标记：<|kind|percent-encoded-value|>。这是编辑器与后端之间的
# 传输编码，落树的 display 正文里会原样保留（前端据此渲染卡片）；任何面向
# 文本的消费方（记忆归档、摘要、SOP 抽取等）都必须看到可读的 @名称。
_INLINE_MARKER_RE = re.compile(r"<\|(skill|plugin|file)\|([^|<>]*)\|>")


def decode_inline_markers(text: str) -> str:
    """把正文里的内联卡片标记还原成可读的 ``@名称``。"""
    if "<|" not in text:
        return text

    def _sub(match: re.Match[str]) -> str:
        kind = match.group(1)
        raw = match.group(2)
        try:
            value = unquote(raw)
        except Exception:  # noqa: BLE001 - 畸形的百分号编码按原样保留
            value = raw
        if kind == "file":
            value = value.replace("\\", "/").rsplit("/", 1)[-1] or value
        return f"@{value}"

    return _INLINE_MARKER_RE.sub(_sub, text)


# ══════════════════════════════════════════════════════════════════
# Token estimation (SDD docs/context-governance-sdd.md W1 / S0-S1)
# ══════════════════════════════════════════════════════════════════
# 权威实现已抽离到独立插件包 ``prismclaw.token_counting``（可替换 provider、
# 系数集中配置）。本模块保留历史 import 面，仅做委托。


def estimate_tokens(text: str, *, image_tokens_each: int = 0) -> int:
    """Estimate LLM tokens for a text (delegates to the token-counting plugin).

    ``image_tokens_each``（默认 0）使图片占位符（``IMAGE_PLACEHOLDER``）
    按每张图固定 token 计而不做占位符文本零化；0 保持旧行为。
    """
    if not text:
        return 0
    return _count_text(
        text,
        image_tokens_each=image_tokens_each,
        image_placeholder=IMAGE_PLACEHOLDER if image_tokens_each > 0 else "",
    )


def count_image_parts(messages: Any) -> int:
    """统计消息列表中内联图片 part 数量（``image``/``image_url`` 块）。"""
    if not isinstance(messages, (list, tuple)):
        return 0
    total = 0
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") in ("image", "image_url"):
                total += 1
    return total


def truncate_by_tokens(text: str, token_budget: int) -> str:
    """CJK 感知 token 截断：按 token 预算从左截断，截断尾部附「…」。

    单一权威实现（原 system_prompt._truncate_by_tokens 迁移至此），
    L1 预览与 system prompt 侧共用。
    """
    if estimate_tokens(text) <= token_budget:
        return text
    total = 0
    for i, ch in enumerate(text):
        total += estimate_tokens(ch)
        if total > token_budget:
            return text[:i].rstrip() + "…"
    return text


def file_reference_text(
    path: str,
    *,
    name: str | None = None,
    mime_type: str | None = None,
) -> str:
    display_name = (name or "").strip() or Path(path).name or "file"
    parts = [f"{FILE_PLACEHOLDER} {display_name}", f"({path})"]
    if mime_type:
        parts.append(f"[{mime_type}]")
    return " ".join(parts)


def extract_text_from_content(content: Any) -> str:
    """Extract text from content that may be str, list[ContentBlock], or list[dict].

    Handles three shapes:
    - str: returned as-is
    - list[ContentBlock]: Pydantic TextBlock/ImageBlock objects
    - list[dict]: LLM wire format (OpenAI `image_url`, Anthropic `image`, `text`)

    ImageBlock / image_url / image blocks are replaced with a placeholder
    so downstream consumers (L4 archive summaries, SOP extraction) know
    the message had images without carrying base64 data. File blocks preserve
    their accessible workspace path so tools/skills can still use them.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return decode_inline_markers(content)
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            btype = block.get("type")
            if btype == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif btype in ("image", "image_url"):
                parts.append(IMAGE_PLACEHOLDER)
                source_path = block.get("source_path")
                if isinstance(source_path, str) and source_path.strip():
                    parts.append(file_reference_text(source_path.strip()))
            elif btype == "file":
                path = block.get("path")
                if isinstance(path, str) and path.strip():
                    parts.append(
                        file_reference_text(
                            path.strip(),
                            name=block.get("name") if isinstance(block.get("name"), str) else None,
                            mime_type=(
                                block.get("mime_type")
                                if isinstance(block.get("mime_type"), str)
                                else None
                            ),
                        )
                    )
            continue

        text_attr = getattr(block, "text", None)
        if isinstance(text_attr, str):
            parts.append(text_attr)
            continue

        btype_attr = getattr(block, "type", None)
        if btype_attr == "image":
            parts.append(IMAGE_PLACEHOLDER)
            source_path_attr = getattr(block, "source_path", None)
            if isinstance(source_path_attr, str) and source_path_attr.strip():
                parts.append(file_reference_text(source_path_attr.strip()))
        elif btype_attr == "file":
            path_attr = getattr(block, "path", None)
            if isinstance(path_attr, str) and path_attr.strip():
                parts.append(
                    file_reference_text(
                        path_attr.strip(),
                        name=getattr(block, "name", None),
                        mime_type=getattr(block, "mime_type", None),
                    )
                )

    return decode_inline_markers("\n".join(parts))
