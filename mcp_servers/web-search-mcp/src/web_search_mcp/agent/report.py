"""Extractive markdown report synthesis for the retrieval agent.

No LLM inside the service: the report is assembled deterministically from the
retrieved evidence (lead sentences + source list + process trace). The caller
(the PrismClaw engine LLM) is responsible for further reasoning on top of it.

Two output surfaces:

- ``build_markdown_report`` — the human/LLM-facing markdown document.
- ``machine_footer`` — a trailing ``<!-- wsm:{...} -->`` HTML comment holding a
  compact machine-readable result index. It is invisible when the markdown is
  rendered, cheap for the LLM, and lets thin clients (e.g. the PrismClaw
  timeline) recover the structured result card without full-text payloads.
"""

from __future__ import annotations

import json
import re
from typing import Any

# The first CJK match decides report language; everything else defaults to en.
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")

_SENTENCE_SPLIT_RE = re.compile(r"[。！？.!?]\s*|\n+")

_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")

_LABELS_ZH = {
    "title_suffix": "网络检索报告",
    "overview": "概览",
    "key_points": "关键要点",
    "sources": "来源",
    "process": "检索过程",
    "full_text": "全文抓取",
    "snippet_only": "仅摘要",
    "empty": "未检索到可用来源。",
    "truncated_note": "结果按预算截断，未覆盖全部命中。",
    "fetched_line": "已抓取 {fetched}/{total} 个来源全文",
    "hit_line": "共 {total} 个来源",
}
_LABELS_EN = {
    "title_suffix": "Web Retrieval Report",
    "overview": "Overview",
    "key_points": "Key Findings",
    "sources": "Sources",
    "process": "Retrieval Process",
    "full_text": "full text",
    "snippet_only": "snippet only",
    "empty": "No usable sources were found.",
    "truncated_note": "Results were truncated by budget; coverage is partial.",
    "fetched_line": "fetched full text for {fetched}/{total} sources",
    "hit_line": "{total} sources in total",
}

MACHINE_FOOTER_PREFIX = "<!-- wsm:"


def detect_report_language(query: str) -> str:
    return "zh" if _CJK_RE.search(query or "") else "en"


def _labels(lang: str) -> dict[str, str]:
    return _LABELS_ZH if lang == "zh" else _LABELS_EN


def _clean_excerpt(text: str, limit: int) -> str:
    """First-sentence extractive excerpt, markdown links flattened."""
    text = _MD_LINK_RE.sub(r"\1", text or "").strip()
    text = re.sub(r"\s+", " ", text)
    first = _SENTENCE_SPLIT_RE.split(text, maxsplit=1)[0].strip()
    if not first:
        first = text
    if len(first) > limit:
        first = first[: limit - 1].rstrip() + "…"
    return first


def _bullets(results: list[dict[str, Any]], limit: int) -> list[str]:
    """One deduplicated lead bullet per usable source."""
    seen: set[str] = set()
    bullets: list[str] = []
    for idx, item in enumerate(results, start=1):
        body = item.get("content", "") or ""
        if not body.strip():
            continue
        excerpt = _clean_excerpt(body, limit)
        if not excerpt or excerpt in seen:
            continue
        seen.add(excerpt)
        bullets.append(f"- [{idx}] {excerpt}")
    return bullets


def build_markdown_report(
    *,
    query: str,
    results: list[dict[str, Any]],
    rounds: list[dict[str, Any]],
    truncated: bool,
    excerpt_chars: int = 200,
) -> str:
    """Assemble the retrieval report. Pure function — safe for degraded input."""
    lang = detect_report_language(query)
    lab = _labels(lang)
    fetched = sum(1 for item in results if item.get("content_status") == "content")
    total = len(results)

    lines: list[str] = [f"# {query} — {lab['title_suffix']}", ""]

    if not results:
        lines += [f"> {lab['empty']}", ""]
        if rounds:
            lines += [f"## {lab['process']}", ""]
            lines += _process_lines(rounds)
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    lines.append(
        f"> {lab['hit_line'].format(total=total)} · "
        + lab["fetched_line"].format(fetched=fetched, total=total)
    )
    lines.append("")

    bullets = _bullets(results, excerpt_chars)
    if bullets:
        lines += [f"## {lab['key_points']}", "", *bullets, ""]

    lines += [f"## {lab['sources']}", ""]
    for idx, item in enumerate(results, start=1):
        title = (item.get("title") or item.get("url") or "").strip() or "?"
        url = item.get("url", "")
        is_full = item.get("content_status") == "content"
        status = lab["full_text"] if is_full else lab["snippet_only"]
        excerpt = _clean_excerpt(item.get("content", "") or "", excerpt_chars)
        line = f"{idx}. [{title}]({url})（{status}）"
        if excerpt and excerpt != title:
            line += f" — {excerpt}"
        lines.append(line)
    lines.append("")

    if rounds:
        lines += [f"## {lab['process']}", ""]
        lines += _process_lines(rounds)
        lines.append("")

    if truncated:
        lines += [f"> {lab['truncated_note']}", ""]

    return "\n".join(lines).rstrip() + "\n"


def _process_lines(rounds: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for rnd in rounds:
        queries = "、".join(f"`{q}`" for q in rnd.get("queries", []) if q)
        statuses = ", ".join(f"{k}: {v}" for k, v in rnd.get("statuses", {}).items())
        prefix = f"- R{rnd.get('round', '?')}: "
        parts = [prefix]
        if queries:
            parts.append(queries)
        if statuses:
            parts.append(f" ({statuses})")
        lines.append("".join(parts))
    return lines


def machine_footer(
    *,
    query: str,
    results: list[dict[str, Any]],
    result_count: int,
    truncated: bool,
    snippet_chars: int = 200,
) -> str:
    """Compact ``<!-- wsm:{...} -->`` index; the only machine block in the report."""
    compact = [
        {
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "source": item.get("source", "unknown"),
            "content": (item.get("content", "") or "")[:snippet_chars],
            "content_status": item.get("content_status", "snippet_only"),
        }
        for item in results
    ]
    blob = {
        "query": query,
        "result_count": result_count,
        "results": compact,
        "truncated": truncated,
    }
    dumped = json.dumps(blob, ensure_ascii=False, separators=(",", ":"))
    return f"{MACHINE_FOOTER_PREFIX}{dumped}-->"
