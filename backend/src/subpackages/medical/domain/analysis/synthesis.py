"""简报合成辅助 — 纯函数，0 IO，0 LLM。

将各 Wave 产出的 findings / citations 汇聚成合成器可消费的结构。
也提供"降级填充"——某领域暂缺时产出占位段落。
"""
from __future__ import annotations

from typing import Any


# 缺失领域的占位文本
_DEGRADATION_MESSAGES = {
    "molecular": "分子检测分析暂缺",
    "drug": "用药审查分析暂缺",
    "evidence": "循证分级分析暂缺",
    "trend": "检验趋势分析暂缺",
}

# 推测性假设缺失不算降级（架构文档明确 Wave3 失败不影响简报）


def collect_all_citations(
    wave1_results: list[dict[str, Any]],
    wave2_results: list[dict[str, Any]],
    wave3_result: dict[str, Any],
) -> list[dict[str, Any]]:
    """汇聚全部 Wave 产出的引用，供合成器引用。

    去重依据 citation_id。
    """
    seen: set[str] = set()
    all_citations: list[dict[str, Any]] = []

    for w1 in wave1_results:
        for c in w1.get("hits", []):
            cid = c.get("source_id", "")
            key = f"kb:{cid}"
            if key not in seen and cid:
                seen.add(key)
                all_citations.append({
                    "citation_id": f"cit:{cid}",
                    "source_type": c.get("source_type", "kb_literature"),
                    "source_id": cid,
                    "title": c.get("title", ""),
                    "snippet": c.get("snippet", ""),
                    "confidence": c.get("relevance", "medium"),
                })

    for w2 in wave2_results:
        for c in w2.get("citations", []):
            cid = c.get("citation_id", "")
            if cid and cid not in seen:
                seen.add(cid)
                all_citations.append(c)

    for c in wave3_result.get("citations", []):
        cid = c.get("citation_id", "")
        if cid and cid not in seen:
            seen.add(cid)
            all_citations.append(c)

    return all_citations


def make_degradation_section(role: str) -> dict[str, Any]:
    """某领域缺失时产出占位段落。"""
    return {
        "heading": _DEGRADATION_MESSAGES.get(role, f"{role} 分析暂缺"),
        "body": "由于数据或分析能力不足，该领域分析暂缺，请结合临床判断补充。",
        "citation_ids": [],
        "is_speculative": False,
    }


def extract_kb_ids_from_hits(hits: list[dict[str, Any]]) -> set[str]:
    """从 KB 检索结果提取 source_id 集合，供审计用。"""
    return {h.get("source_id", "") for h in hits if h.get("source_id")}


def build_raw_texts_map(
    reports: list[dict[str, Any]],
) -> dict[str, str]:
    """构建 report_id → raw_text 映射，供审计引文定位用。"""
    return {
        r.get("id", ""): r.get("raw_text", "")
        for r in reports
        if r.get("id") and r.get("raw_text")
    }
