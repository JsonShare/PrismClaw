from __future__ import annotations

from typing import Any

from prismclaw.engine.compression._material_policy import ResultMaterialType


def _semantic_compact_text(
    text: str,
    *,
    material_type: ResultMaterialType,
    max_chars: int,
) -> tuple[str, list[str]]:
    reducers: dict[ResultMaterialType, Any] = {
        "retrieval_list": _compact_retrieval_list_text,
        "long_web_page": _compact_web_page_text,
        "document_extraction": _compact_document_extraction_text,
        "structured_dataset_preview": _compact_structured_dataset_text,
    }
    reducer = reducers.get(material_type)
    if reducer is None or len(text) <= max_chars:
        return text, []
    compacted = reducer(text, max_chars=max_chars)
    if not compacted or len(compacted) >= len(text):
        return text, []
    return compacted, [f"semantic_reduce:{material_type}"]


def _compact_retrieval_list_text(text: str, *, max_chars: int) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return text
    kept: list[str] = []
    seen: set[str] = set()
    for line in lines:
        normalized = line.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        kept.append(line)
        if len("\n".join(kept)) >= max_chars:
            break
        if len(kept) >= 12:
            break
    summary = f"[检索结果列表摘要]\n保留条目：{len(kept)} / {len(lines)}\n" + "\n".join(
        f"- {line[:160]}" for line in kept
    )
    return summary[:max_chars]


def _compact_web_page_text(text: str, *, max_chars: int) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    headings = [
        line for line in lines if line.startswith("#") or line.endswith(":") or line.istitle()
    ][:10]
    paragraphs = [line for line in lines if len(line) > 60][:4]
    summary_lines = ["[网页内容摘要]"]
    if headings:
        summary_lines.append("标题：")
        summary_lines.extend(f"- {line[:120]}" for line in headings)
    if paragraphs:
        summary_lines.append("关键段落：")
        summary_lines.extend(f"- {line[:180]}" for line in paragraphs)
    compacted = "\n".join(summary_lines)
    return compacted[:max_chars] if compacted.strip() else text[:max_chars]


def _compact_document_extraction_text(text: str, *, max_chars: int) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    title = lines[0][:160] if lines else ""
    sections = [line for line in lines[1:] if line.endswith(":") or line.startswith(("1.", "2.", "3."))][:8]
    evidence = [line for line in lines if len(line) > 40][:5]
    summary_lines = ["[文档抽取摘要]"]
    if title:
        summary_lines.append(f"标题：{title}")
    if sections:
        summary_lines.append("章节：")
        summary_lines.extend(f"- {line[:140]}" for line in sections)
    if evidence:
        summary_lines.append("高信息量行：")
        summary_lines.extend(f"- {line[:180]}" for line in evidence)
    compacted = "\n".join(summary_lines)
    return compacted[:max_chars] if compacted.strip() else text[:max_chars]


def _compact_structured_dataset_text(text: str, *, max_chars: int) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return text
    header = lines[0][:200]

    # Heuristic: keep first few lines and last few lines for context
    sample_rows = lines[1:5]
    if len(lines) > 5:
        sample_rows.append("...")
        sample_rows.extend(lines[-2:])

    summary_lines = [
        "[结构化数据集摘要]",
        f"表头：{header}",
        f"总行数：{len(lines) - 1}",
    ]
    summary_lines.extend(f"- {row[:180]}" for row in sample_rows)
    compacted = "\n".join(summary_lines)
    return compacted[:max_chars]
