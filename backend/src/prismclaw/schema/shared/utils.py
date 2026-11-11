"""Shared utilities for content block handling.

This module consolidates the previously-duplicated "extract text from
possibly-list content" logic scattered across compaction/dedup.py,
compaction/hardening.py, sop_extraction.py, and
memory_archive.py.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

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
# Coefficients calibrated 2026-08-21 against the real Qwen3.5-27B
# tokenizer over 101 production chat messages (scripts/calibration_
# report.json): R²=0.9978. The legacy ``len(text) // 4`` heuristic
# underestimated Chinese-heavy content by 2.28x (independently confirmed
# by the 50-round web test: real input climbed to 136,792 tokens on a
# 131,072-token window while the estimator reported ~60k), so history
# compaction never triggered.
#
# Rollback switch: PRISMCLAW_COMPACTION_ESTIMATOR_V2=false restores the
# legacy len//4 behavior (read once at import; restart to apply).

_CJK_TOKENS_PER_CHAR = 0.75      # fit: 0.7499 tokens per CJK character
# 注意：2.72 是 2026-08-21 校准 101 条 chat messages 的 R²=0.9978 拟合值，
# 但那批数据是生产对话（中文为主），英文 JSON/system-prompt/tool-schema 占比低。
# 漂移 sentinel 在含大量英文 JSON 的场景下暴露了问题：2.72 chars/token ≈ 2.72 B/token
# 远低于实际 provider tokenizer（对英文/JSON 通常 4-5 B/token），导致 estimator 对英文
# 内容**系统性高估 60-85%**——history_tokens/accounted 持续比 real 大 6-7K tokens。
# 调至 4.0 chars/token ≈ 4 B/token 英文（gpt-4o/qwen 等真实 tokenizer 英文基准），
# 同时 CJK 0.75 ≈ 4 B/token 保持不变（两者在 UTF-8 字节维度上对齐到 ~4 B/token，
# 是大多数 tokenizer 的实际字节效率）。值 4.0 是两者（CJK 和 ASCII）在字节空间的自然交汇点。
_OTHER_CHARS_PER_TOKEN = 4.0

_CJK_RE = re.compile(
    "[\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]"
)


def _estimator_v2_enabled() -> bool:
    raw = os.environ.get("PRISMCLAW_COMPACTION_ESTIMATOR_V2", "true")
    return str(raw).strip().lower() not in ("false", "0", "no")


def _estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    if not _estimator_v2_enabled():
        return max(1, len(text) // 4)
    # sub() runs in C: cjk = len − len(after removing CJK chars)
    cjk = len(text) - len(_CJK_RE.sub("", text))
    other = len(text) - cjk
    return max(1, int(cjk * _CJK_TOKENS_PER_CHAR + other / _OTHER_CHARS_PER_TOKEN))


def estimate_tokens(text: str, *, image_tokens_each: int = 0) -> int:
    """Estimate LLM tokens for a text (CJK-weighted, calibrated).

    CJK characters count ~0.75 tokens each; everything else ~1 token per
    2.72 characters (markdown/punctuation-heavy chat content tokenizes
    denser than natural English prose). Empty text returns 0.

    ``image_tokens_each``（默认 0）使图片占位符（``IMAGE_PLACEHOLDER``）
    按每张图固定 token 计而不做占位符文本零化；0 保持旧行为。
    """
    if not text:
        return 0
    if image_tokens_each > 0:
        image_count = text.count(IMAGE_PLACEHOLDER)
        if image_count:
            remainder = text.replace(IMAGE_PLACEHOLDER, "")
            return _estimate_text_tokens(remainder) + image_count * int(image_tokens_each)
    return _estimate_text_tokens(text)


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
