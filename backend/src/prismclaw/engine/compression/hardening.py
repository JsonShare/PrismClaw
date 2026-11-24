from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

# Single source of truth — imported from _preservation_rules.py.
# Do NOT redefine here.
from prismclaw.engine.compression._preservation_rules import IDENTIFIER_PRESERVATION_INSTRUCTIONS

HARDENED_SUMMARIZER_SYSTEM_PROMPT = (
    "请为接班的 AI 简洁地总结此前的工作进展。"
    "1. 目标是什么？ 2. 已完成或发现了什么？ 3. 下一个立即执行的步骤是什么？\n\n"
    f"{IDENTIFIER_PRESERVATION_INSTRUCTIONS}\n\n"
    "不要写客套话。返回纯文本；除非能提升清晰度，否则不要使用标题或项目符号。"
)

_NON_CONVERSATIONAL_ROLES = {"system"}
_HEARTBEAT_MARKERS = ("heartbeat", "ping", "pong", "[heartbeat]", "[system]")


def _extract_text(content: object) -> str:
    from prismclaw.schema.shared import extract_text_from_content

    return extract_text_from_content(content)


def has_real_conversation(messages: list[dict[str, Any]]) -> bool:
    for msg in messages:
        role = msg.get("role")
        if role in _NON_CONVERSATIONAL_ROLES:
            continue
        text = _extract_text(msg.get("content"))
        lowered = text.strip().lower()
        if not lowered:
            if msg.get("tool_calls"):
                return True
            continue
        if any(marker in lowered for marker in _HEARTBEAT_MARKERS):
            continue
        if role in ("user", "assistant", "tool"):
            return True
    return False


def strip_tool_result_details(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for msg in messages:
        canonicalized = _canonicalize_result_compaction_message(msg)
        if canonicalized is not None:
            msg = canonicalized
        if msg.get("role") == "tool" and isinstance(msg.get("content"), list):
            new_content = []
            for block in msg["content"]:
                if isinstance(block, dict):
                    clean_block = {k: v for k, v in block.items() if k != "details"}
                    new_content.append(clean_block)
                else:
                    new_content.append(block)
            new_msg = dict(msg)
            new_msg["content"] = new_content
            cleaned.append(new_msg)
        elif "details" in msg:
            new_msg = {k: v for k, v in msg.items() if k != "details"}
            cleaned.append(new_msg)
        else:
            cleaned.append(msg)
    return cleaned


def filter_oversized_messages(
    messages: list[dict[str, Any]],
    *,
    context_window: int,
    oversized_fraction: float = 0.5,
) -> list[dict[str, Any]]:
    if context_window <= 0:
        return messages
    threshold_chars = int(context_window * oversized_fraction * 4)
    result: list[dict[str, Any]] = []
    for msg in messages:
        canonicalized = _canonicalize_result_compaction_message(msg)
        if canonicalized is not None:
            result.append(canonicalized)
            continue
        text = _extract_text(msg.get("content"))
        if len(text) > threshold_chars:
            role = msg.get("role", "unknown")
            result.append(
                {
                    "role": role,
                    "content": f"[已省略来自 {role} 的超大消息]",
                }
            )
        else:
            result.append(msg)
    return result


def _canonicalize_result_compaction_message(msg: dict[str, Any]) -> dict[str, Any] | None:
    if msg.get("role") != "tool":
        return None
    text = _extract_text(msg.get("content"))
    if not _looks_like_result_compaction_replacement(text):
        return None

    canonical_lines = []
    first_line = ""
    reference_id = ""
    original_chars = ""
    preview_lines = []
    in_preview = False

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not first_line and line.startswith("[compacted "):
            first_line = line
            continue
        if not reference_id and line.startswith("reference_id:"):
            reference_id = line
            continue
        if not original_chars and line.startswith("original_chars:"):
            original_chars = line
            continue
        if line.startswith("preview:"):
            in_preview = True
            preview_lines.append(line)
            continue
        if in_preview:
            preview_lines.append(line)

    canonical_lines = [line for line in (first_line, reference_id, original_chars) if line]
    if preview_lines:
        canonical_lines.extend(preview_lines)

    canonical_text = "\n".join(canonical_lines)

    new_msg = dict(msg)
    content = msg.get("content")
    if isinstance(content, list):
        new_msg["content"] = [{"type": "text", "text": canonical_text}]
    else:
        new_msg["content"] = canonical_text
    return new_msg


def _looks_like_result_compaction_replacement(text: str) -> bool:
    stripped = text.strip()
    if not stripped.startswith("[compacted "):
        return False
    return "reference_id:" in stripped


def _estimate_tokens(msg: dict[str, Any]) -> int:
    from prismclaw.engine.compression.compaction import estimate_message_tokens

    return estimate_message_tokens(msg)


def split_into_chunks(
    messages: list[dict[str, Any]],
    *,
    chunk_token_budget: int,
) -> list[list[dict[str, Any]]]:
    if chunk_token_budget <= 0:
        return [list(messages)]
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_tokens = 0
    for msg in messages:
        msg_tokens = _estimate_tokens(msg)
        if current and (current_tokens + msg_tokens) > chunk_token_budget:
            chunks.append(current)
            current = [msg]
            current_tokens = msg_tokens
        else:
            current.append(msg)
            current_tokens += msg_tokens
    if current:
        chunks.append(current)
    return chunks or [[]]


async def summarize_in_stages(
    messages: list[dict[str, Any]],
    *,
    summarizer: Callable[[list[dict[str, Any]]], Awaitable[str]],
    chunk_token_budget: int = 8_000,
) -> str:
    chunks = split_into_chunks(messages, chunk_token_budget=chunk_token_budget)

    if len(chunks) <= 1:
        return await summarizer(messages)

    stage_summaries: list[str] = []
    for chunk in chunks:
        summary = await summarizer(chunk)
        stage_summaries.append(summary)

    merge_payload: list[dict[str, Any]] = [
        {"role": "user", "content": f"[第 {i + 1} 块的摘要]\n{s}"}
        for i, s in enumerate(stage_summaries)
    ]
    return await summarizer(merge_payload)


def sanity_check_token_estimate(tokens_before: int, tokens_after: int | None) -> int | None:
    if tokens_after is None:
        return None
    if tokens_after > tokens_before:
        return None
    return tokens_after


# ──────────────────────────── 11-Section Carry-Forward ───────────────────────────


# Matches numbered section headers like "7. Pending Tasks" in the 11-section
# compact-summary format.  Handles optional markdown bold (**7. ...**) and
# multi-word titles.
_SECTION_HEADER_RE = re.compile(
    r"^\s*\*{0,2}\s*(\d+)\.\s+(.+?)\s*\*{0,2}\s*$",
    re.MULTILINE,
)

# 只匹配最外层的一对结构标记（锚定首尾）：正文里同形的字面量
# （用户原话、标识符）必须逐字保留，不能用全局替换删除。
_SUMMARY_WRAPPER_RE = re.compile(
    r"^\s*<\s*summary\s*>|<\s*/\s*summary\s*>\s*$", re.IGNORECASE
)

# Sections whose content is structurally mandatory for carry-forward.
# When the new summary is missing one of these sections that was present
# in the prior summary, the missing section is appended verbatim.
_CARRY_FORWARD_SECTION_NUMBERS = frozenset({7})

_VOIDED_MARKERS = frozenset({"voided", "superseded", "cancelled", "retracted", "replaced"})

# 空段声明识别：与 prompts/compact_summary.md 的「（无）」契约保持一致。
# WORDS 同时服务「原始 body 精确比对」与「归一后精确比对」；PREFIXES 只对
# 原始 body 做前缀匹配；WRAPPERS 仅供归一化剥离包裹符号。契约原文由
# test_prompt_template_consistency.py 的联动守卫从 md 读取校验，不在此抄写。
_EMPTY_DECLARATION_WORDS = frozenset({"", "none", "n/a", "nothing", "- none", "无"})
_EMPTY_DECLARATION_PREFIXES = ("no pending", "no current", "no active", "no outstanding")
_EMPTY_DECLARATION_WRAPPERS = "-*•· \t（）()[]【】「」"


def extract_sections(text: str) -> dict[int, str]:
    """Extract numbered sections from an 11-section compaction summary.

    Returns a mapping ``{section_number: section_content}`` where
    *section_content* includes the header line and all body text up to
    (but not including) the next section header.
    """
    if not text or not text.strip():
        return {}
    # 真实压缩输出经 strip_analysis_block 后仍带 <summary>/</summary>；
    # 只剥最外层这一对，否则闭合标签会并入最后一个 section 的正文，
    # 使「（无）」空段被误判为实质内容，W12 作废条目的结转被跳过。
    text = _SUMMARY_WRAPPER_RE.sub("", text)
    positions: list[tuple[int, int, str]] = []  # (start, number, header_line)
    for m in _SECTION_HEADER_RE.finditer(text):
        num = int(m.group(1))
        header_line = m.group(0).rstrip()
        positions.append((m.start(), num, header_line))
    if not positions:
        return {}
    sections: dict[int, str] = {}
    for i, (start, num, _header) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        content = text[start:end].rstrip()
        sections[num] = content
    return sections


def _is_voided_section(text: str) -> bool:
    """Heuristic: does this section body contain voided/superseded entries?"""
    lower = text.lower()
    return any(marker in lower for marker in _VOIDED_MARKERS)


def _normalize_empty_declaration(body: str) -> str:
    """把空段声明的包裹形态归一为规范词形。

    「（无）」/「- （无）」/「(无)。」→「无」；「None.」→「none」。
    归一结果**只用于精确词形比对**：英文短语前缀判定必须走原始 body，
    否则剥掉 "- " 后 "No pending appeal: ..." 会被误判为空声明。

    末尾句点须先剥：对「（无）。」先剥括号会残留「）」导致漏判。
    """
    normalized = body.strip().rstrip(".。 ").strip()
    return normalized.strip(_EMPTY_DECLARATION_WRAPPERS).strip()


def _section_is_substantive(text: str) -> bool:
    """A section is substantive if it has content beyond just the header
    and does not declare itself empty.

    One entry point, two checks:
    1. 原始 body 保留既有的英文空声明判定（精确词形 + "no pending/..." 前缀）。
       项目符号与括号前缀天然为前缀匹配挡住含实质指令的长句。
    2. 归一后的词形处理 bundled prompt 的中文契约
       (compact_summary.md: 「确无内容时该章节正文写「（无）」」) 及其包裹变体，
       **只做精确比对，不参与前缀匹配**。

    prompt-consistency 守卫测试把两处与 md 锁在一起。
    """
    lines = text.strip().splitlines()
    if len(lines) <= 1:
        return False
    body = "\n".join(lines[1:]).strip().lower()
    if not body:
        return False
    stripped = body.strip().rstrip(".")
    if stripped in _EMPTY_DECLARATION_WORDS:
        return False
    if stripped.startswith(_EMPTY_DECLARATION_PREFIXES):
        return False
    return _normalize_empty_declaration(body) not in _EMPTY_DECLARATION_WORDS


def carry_forward_missing_sections(
    new_summary: str,
    prior_summary: str,
) -> str:
    """Deterministic carry-forward: append sections from *prior_summary* that
    are missing or declared empty in *new_summary*.

    This is the structural guarantee that prevents progressive amnesia in
    chained compaction (C-1 / SDD context-governance W8/W12).  It does NOT
    depend on LLM behaviour — if the prior summary had a substantive section 7
    and the new summary has it empty or missing, the prior content is appended
    as ``[carry-forward from prior summary]``.

    Only sections listed in ``_CARRY_FORWARD_SECTION_NUMBERS`` are carried
    forward.  Additionally, any section in the prior summary that contains
    voided markers (W12) is always carried forward if missing from the new
    summary, regardless of section number.
    """
    if not prior_summary or not prior_summary.strip():
        return new_summary
    if not new_summary or not new_summary.strip():
        return prior_summary

    prior_sections = extract_sections(prior_summary)
    new_sections = extract_sections(new_summary)

    if not prior_sections:
        return new_summary

    # Determine which sections need carry-forward
    missing: list[tuple[int, str]] = []  # (section_number, content)
    for num, content in prior_sections.items():
        if not _section_is_substantive(content):
            continue
        # 新摘要里缺失或变得不实质，才需要结转
        if num in new_sections and _section_is_substantive(new_sections[num]):
            continue
        # 强制结转的编号，或被作废（voided）的章节
        if num in _CARRY_FORWARD_SECTION_NUMBERS or _is_voided_section(content):
            missing.append((num, content))

    if not missing:
        return new_summary

    # Append missing sections deterministically
    anchor = "\n\n[从既有摘要结转——以下各节必须保留]\n"
    appended_parts: list[str] = [content for _num, content in sorted(missing)]
    return new_summary.rstrip() + anchor + "\n\n".join(appended_parts) + "\n"
