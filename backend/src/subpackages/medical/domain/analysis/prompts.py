"""分析管线 LLM 契约：prompt、tool schema、输出归一化。

5 段调用，各管一件事：
1. director_plan    —— 主任规划（决定检索方向与优先级）
2. kb_retrieval     —— 知识检索（本地 KB 文献/指南/药品匹配）
3. expert_analysis  —— 专家分析（4 角色并行，每角色独立 system）
4. hypothesis       —— 探索性假设（围栏隔离，必须标注 [SPECULATIVE]）
5. synthesis        —— 合成简报（拼各路结果为 sections + citations）

归一化函数承担「模型说什么都不能信」的职责：列表长度裁剪、类型矫正、
引用 ID 格式校验。落库的字段一律先过这里。
"""
from __future__ import annotations

import json
from typing import Any


# 输出长度上限
MAX_FOCUS_AREAS = 6
MAX_RETRIEVAL_QUERIES = 8
MAX_EXPERT_FINDINGS = 10
MAX_CITATIONS_PER_EXPERT = 8
MAX_HYPOTHESES = 5
MAX_BRIEF_SECTIONS = 12
MAX_CITATIONS = 30
MAX_QUOTED_TEXT = 200
MAX_FIELD = 256

# 有效 source_type 枚举
_SOURCE_TYPES = frozenset({
    "report_section", "kb_literature", "kb_guideline", "kb_drug", "speculative",
})

# 有效 confidence 枚举
_CONFIDENCE_LEVELS = frozenset({"high", "medium", "low"})

# 专家角色
EXPERT_ROLES = ("molecular", "drug", "evidence", "trend")

# ══════════════════════════════════════════════════════════════
# 阶段 1：主任规划
# ══════════════════════════════════════════════════════════════

DIRECTOR_SYSTEM = (
    "你是临床研究规划主任。根据报告数据和患者画像，规划分析方向。"
    "只输出工具调用，不要输出解释。"
    "规则：focus_areas 是临床关注领域（如分子检测、用药方案、证据分级、检验趋势）；"
    "retrieval_queries 是知识库检索关键词；"
    "analysis_priorities 是分析优先级排序。"
    "不要编造原文没有的疾病或症状。"
)

DIRECTOR_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "director_plan",
        "description": "输出临床简报的分析计划。",
        "parameters": {
            "type": "object",
            "properties": {
                "focus_areas": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "临床关注领域列表",
                },
                "retrieval_queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "知识库检索关键词列表",
                },
                "analysis_priorities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "分析优先级排序",
                },
            },
            "required": ["focus_areas", "retrieval_queries", "analysis_priorities"],
        },
    },
}


def build_director_prompt(
    report_summaries: list[dict[str, Any]],
    patient_profile: dict[str, Any],
) -> str:
    """构造主任规划 prompt。

    report_summaries: [{id, diagnosis, summary, report_type, labs, medications}]
    patient_profile: {name, gender, age, primary_diagnosis, risk_level, ...}
    """
    parts = ["以下是患者的临床报告摘要：\n"]
    for r in report_summaries:
        parts.append(
            f"- 报告 {r.get('id', '?')}: {r.get('diagnosis', '')} "
            f"({r.get('report_type', '')}), 摘要: {r.get('summary', '')}"
        )
        if r.get("labs"):
            parts.append(f"  检验项: {_truncate_list(r['labs'], 5)}")
        if r.get("medications"):
            parts.append(f"  用药: {_truncate_list(r['medications'], 5)}")

    parts.append(f"\n患者画像：{_json_compact(patient_profile)}")
    parts.append(
        "\n请输出 focus_areas / retrieval_queries / analysis_priorities。"
    )
    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════
# 阶段 2：知识检索
# ══════════════════════════════════════════════════════════════

KB_RETRIEVAL_SYSTEM = (
    "你是临床知识检索助手。根据检索关键词，从提供的知识库条目中筛选相关内容。"
    "只输出工具调用，不要输出解释。"
    "规则：每个 hit 必须标注 source_type 和 source_id；"
    "snippet 不超过 100 字；relevance 为 high/medium/low。"
    "只返回与查询相关的条目，不要凑数。"
)

KB_RETRIEVAL_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "kb_retrieval_result",
        "description": "输出经过筛选的知识库命中项。",
        "parameters": {
            "type": "object",
            "properties": {
                "hits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "source_type": {"type": "string", "description": "kb_literature/kb_guideline/kb_drug"},
                            "source_id": {"type": "string"},
                            "title": {"type": "string"},
                            "snippet": {"type": "string"},
                            "relevance": {"type": "string", "description": "high/medium/low"},
                        },
                        "required": ["source_type", "source_id", "title", "relevance"],
                    },
                },
            },
            "required": ["hits"],
        },
    },
}


def build_kb_retrieval_prompt(
    retrieval_queries: list[str],
    kb_literatures: list[dict[str, Any]],
    kb_guidelines: list[dict[str, Any]],
    kb_drugs: list[dict[str, Any]],
) -> str:
    """构造知识检索 prompt。"""
    parts = [f"检索关键词：{', '.join(retrieval_queries)}\n"]
    if kb_literatures:
        parts.append("文献库：")
        for lit in kb_literatures[:20]:
            parts.append(f"  [{lit.get('id', '?')}] {lit.get('title', '')} ({lit.get('year', '')})")
    if kb_guidelines:
        parts.append("指南库：")
        for g in kb_guidelines[:20]:
            parts.append(f"  [{g.get('id', '?')}] {g.get('title', '')} - {g.get('org', '')}")
    if kb_drugs:
        parts.append("药品库：")
        for d in kb_drugs[:20]:
            parts.append(f"  [{d.get('id', '?')}] {d.get('name', '')} ({d.get('category', '')})")
    parts.append("\n筛选与检索关键词相关的条目，输出 hits。")
    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════
# 阶段 3：专家分析（4 角色）
# ══════════════════════════════════════════════════════════════

_EXPERT_SYSTEM_TEMPLATES = {
    "molecular": (
        "你是分子病理专家。分析分子检测和生物标志物结果。"
        "只输出工具调用，不要输出解释。"
        "规则：每个 finding 必须有 citation；confidence 为 high/medium/low；"
        "不要编造原文没有的突变或标志物。"
    ),
    "drug": (
        "你是临床药师。审查用药方案，检查药物相互作用。"
        "只输出工具调用，不要输出解释。"
        "规则：每个 finding 必须有 citation；confidence 为 high/medium/low；"
        "不要推荐未在药品库中列出的药物。"
    ),
    "evidence": (
        "你是循证医学专家。评估证据等级，匹配临床指南。"
        "只输出工具调用，不要输出解释。"
        "规则：每个 finding 必须有 citation；confidence 为 high/medium/low；"
        "证据等级必须基于文献或指南，不要凭空评级。"
    ),
    "trend": (
        "你是检验科医师。分析检验指标趋势和数值变化。"
        "只输出工具调用，不要输出解释。"
        "规则：每个 finding 必须有 citation；confidence 为 high/medium/low；"
        "趋势判断必须基于实际数值，不要推测未检验的指标。"
    ),
}

EXPERT_ANALYSIS_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "expert_findings",
        "description": "输出带引用的专家分析发现。",
        "parameters": {
            "type": "object",
            "properties": {
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "statement": {"type": "string", "description": "分析结论"},
                            "detail": {"type": "string", "description": "详细说明"},
                            "citation_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "引用 citation_id 列表",
                            },
                        },
                        "required": ["statement", "citation_ids"],
                    },
                },
                "citations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "citation_id": {"type": "string"},
                            "source_type": {"type": "string"},
                            "source_id": {"type": "string"},
                            "section_heading": {"type": "string"},
                            "quoted_text": {"type": "string"},
                            "confidence": {"type": "string"},
                        },
                        "required": ["citation_id", "source_type", "source_id"],
                    },
                },
                "confidence": {"type": "string", "description": "整体置信度 high/medium/low"},
            },
            "required": ["findings", "citations"],
        },
    },
}


def get_expert_system(role: str) -> str:
    """获取专家角色的 system prompt。"""
    return _EXPERT_SYSTEM_TEMPLATES.get(role, _EXPERT_SYSTEM_TEMPLATES["molecular"])


def build_expert_prompt(
    role: str,
    plan: dict[str, Any],
    kb_hits: list[dict[str, Any]],
    report_data: list[dict[str, Any]],
) -> str:
    """构造专家分析 prompt。

    role: molecular/drug/evidence/trend
    plan: 主任规划输出 {focus_areas, retrieval_queries, analysis_priorities}
    kb_hits: 知识检索结果
    report_data: [{id, raw_text_summary, labs, medications, diagnosis}]
    """
    role_names = {
        "molecular": "分子病理",
        "drug": "临床药学",
        "evidence": "循证医学",
        "trend": "检验趋势",
    }
    parts = [
        f"分析方向：{role_names.get(role, role)}",
        f"关注领域：{', '.join(plan.get('focus_areas', []))}",
    ]
    if kb_hits:
        parts.append("\n知识库相关条目：")
        for h in kb_hits[:10]:
            parts.append(f"  [{h.get('source_type', '')}:{h.get('source_id', '')}] {h.get('snippet', '')}")

    parts.append("\n报告数据：")
    for r in report_data:
        parts.append(f"  报告 {r.get('id', '?')}: {r.get('diagnosis', '')}")
        if r.get("labs"):
            parts.append(f"    检验: {_truncate_list(r['labs'], 5)}")
        if r.get("medications"):
            parts.append(f"    用药: {_truncate_list(r['medications'], 5)}")

    parts.append("\n输出 findings 和 citations。每个 finding 必须有 citation 支撑。")
    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════
# 阶段 4：探索性假设
# ══════════════════════════════════════════════════════════════

HYPOTHESIS_SYSTEM = (
    "你是临床研究假设探索助手。基于已有分析结果，提出推测性假设。"
    "只输出工具调用，不要输出解释。"
    "规则：所有假设均为推测性，每个假设的 citation 的 source_type 必须是 speculative；"
    "假设必须标明不确定性；不要将推测性假设伪装成确定结论。"
    "如果数据不足以提出有价值的假设，输出空列表即可。"
)

HYPOTHESIS_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "hypothesis_result",
        "description": "输出带引用的推测性假设。",
        "parameters": {
            "type": "object",
            "properties": {
                "hypotheses": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "statement": {"type": "string"},
                            "rationale": {"type": "string"},
                            "citation_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["statement", "rationale"],
                    },
                },
                "citations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "citation_id": {"type": "string"},
                            "source_type": {"type": "string", "description": "必须为 speculative"},
                            "source_id": {"type": "string"},
                            "quoted_text": {"type": "string"},
                            "confidence": {"type": "string"},
                        },
                        "required": ["citation_id", "source_type"],
                    },
                },
            },
            "required": ["hypotheses", "citations"],
        },
    },
}


def build_hypothesis_prompt(
    wave1_results: list[dict[str, Any]],
    wave2_results: list[dict[str, Any]],
    plan: dict[str, Any],
) -> str:
    """构造探索性假设 prompt。"""
    parts = ["基于以下分析结果，提出推测性假设：\n"]

    parts.append("知识检索摘要：")
    for w1 in wave1_results:
        hits = w1.get("hits", [])
        if hits:
            parts.append(f"  找到 {len(hits)} 条相关知识")

    parts.append("\n专家分析摘要：")
    for w2 in wave2_results:
        role = w2.get("role", "?")
        findings = w2.get("findings", [])
        parts.append(f"  {role}: {len(findings)} 条发现")

    parts.append("\n所有假设的 citation 的 source_type 必须为 speculative。")
    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════
# 阶段 5：合成简报
# ══════════════════════════════════════════════════════════════

SYNTHESIS_SYSTEM = (
    "你是临床研究简报合成助手。将多路分析结果合成为结构化简报。"
    "只输出工具调用，不要输出解释。"
    "规则：每个结论必须有 citation 支撑，citation_id 引用已有的引用表；"
    "推测性内容的 is_speculative 必须为 true，且只能引用 source_type=speculative 的 citation；"
    "段落按临床逻辑排序（病情概要→分子检测→用药→证据→趋势→推测假设）；"
    "不要编造原文没有的结论。"
)

SYNTHESIS_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "brief_draft",
        "description": "输出结构化的临床简报草稿。",
        "parameters": {
            "type": "object",
            "properties": {
                "sections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "heading": {"type": "string"},
                            "body": {"type": "string"},
                            "citation_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "is_speculative": {"type": "boolean", "default": False},
                        },
                        "required": ["heading", "body", "citation_ids"],
                    },
                },
                "citations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "citation_id": {"type": "string"},
                            "source_type": {"type": "string"},
                            "source_id": {"type": "string"},
                            "section_heading": {"type": "string"},
                            "quoted_text": {"type": "string"},
                            "confidence": {"type": "string"},
                        },
                        "required": ["citation_id", "source_type", "source_id"],
                    },
                },
            },
            "required": ["sections", "citations"],
        },
    },
}


def build_synthesis_prompt(
    wave1_results: list[dict[str, Any]],
    wave2_results: list[dict[str, Any]],
    wave3_result: dict[str, Any],
    plan: dict[str, Any],
    all_citations: list[dict[str, Any]],
) -> str:
    """构造合成简报 prompt。

    all_citations: 各 Wave 产出的全部引用，供合成器引用。
    """
    parts = ["将以下分析结果合成为结构化简报：\n"]

    parts.append("主任规划关注领域：" + ", ".join(plan.get("focus_areas", [])))

    for w2 in wave2_results:
        role = w2.get("role", "?")
        findings = w2.get("findings", [])
        if findings:
            parts.append(f"\n{role} 分析：")
            for f in findings[:5]:
                parts.append(f"  - {f.get('statement', '')}")

    hyps = wave3_result.get("hypotheses", [])
    if hyps:
        parts.append(f"\n推测性假设 ({len(hyps)} 条)：")
        for h in hyps[:3]:
            parts.append(f"  - {h.get('statement', '')}")

    if all_citations:
        parts.append(f"\n已有引用表 ({len(all_citations)} 条)：")
        for c in all_citations[:15]:
            parts.append(
                f"  {c.get('citation_id', '?')}: [{c.get('source_type', '')}] "
                f"{c.get('quoted_text', '')[:60]}"
            )

    parts.append("\n输出 sections 和 citations。推测性段落 is_speculative=true。")
    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════
# 归一化函数
# ══════════════════════════════════════════════════════════════


def _str(value: Any, limit: int = MAX_FIELD) -> str:
    return str(value or "").strip()[:limit]


def _truncate_list(items: list[Any], limit: int) -> str:
    """列表摘要，超出显示省略。"""
    if not items:
        return "(无)"
    text = ", ".join(str(i) for i in items[:limit])
    if len(items) > limit:
        text += f" ...等{len(items)}项"
    return text


def _json_compact(obj: Any) -> str:
    """紧凑 JSON，用于 prompt 内联。"""
    try:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))[:500]
    except (TypeError, ValueError):
        return str(obj)[:500]


def normalize_director_plan(raw: dict[str, Any]) -> dict[str, Any]:
    """收敛主任规划输出。"""
    focus = [_str(a, 64) for a in (raw.get("focus_areas") or [])[:MAX_FOCUS_AREAS] if a]
    queries = [_str(q, 128) for q in (raw.get("retrieval_queries") or [])[:MAX_RETRIEVAL_QUERIES] if q]
    priorities = [_str(p, 64) for p in (raw.get("analysis_priorities") or [])[:MAX_FOCUS_AREAS] if p]
    return {
        "focus_areas": focus,
        "retrieval_queries": queries,
        "analysis_priorities": priorities,
    }


def normalize_kb_retrieval(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """收敛知识检索输出 → hits 列表。"""
    hits = []
    for h in (raw.get("hits") or [])[:20]:
        if not isinstance(h, dict):
            continue
        st = _str(h.get("source_type"), 32)
        if st not in ("kb_literature", "kb_guideline", "kb_drug"):
            continue
        hits.append({
            "source_type": st,
            "source_id": _str(h.get("source_id"), 64),
            "title": _str(h.get("title"), 256),
            "snippet": _str(h.get("snippet"), 200),
            "relevance": _str(h.get("relevance"), 8) if _str(h.get("relevance")) in _CONFIDENCE_LEVELS else "medium",
        })
    return hits


def normalize_expert_findings(raw: dict[str, Any], role: str) -> dict[str, Any]:
    """收敛专家分析输出 → {role, findings, citations, confidence}。"""
    findings = []
    for f in (raw.get("findings") or [])[:MAX_EXPERT_FINDINGS]:
        if not isinstance(f, dict):
            continue
        findings.append({
            "statement": _str(f.get("statement"), 500),
            "detail": _str(f.get("detail"), 1000),
            "citation_ids": [str(c) for c in (f.get("citation_ids") or [])[:MAX_CITATIONS_PER_EXPERT]],
        })

    citations = []
    for c in (raw.get("citations") or [])[:MAX_CITATIONS_PER_EXPERT]:
        if not isinstance(c, dict):
            continue
        st = _str(c.get("source_type"), 32)
        if st not in _SOURCE_TYPES:
            st = "report_section"
        citations.append({
            "citation_id": _str(c.get("citation_id"), 64),
            "source_type": st,
            "source_id": _str(c.get("source_id"), 64),
            "section_heading": _str(c.get("section_heading"), 128),
            "quoted_text": _str(c.get("quoted_text"), MAX_QUOTED_TEXT),
            "confidence": _str(c.get("confidence"), 8) if _str(c.get("confidence")) in _CONFIDENCE_LEVELS else "medium",
        })

    conf = _str(raw.get("confidence"), 8)
    if conf not in _CONFIDENCE_LEVELS:
        conf = "medium"

    return {
        "role": role,
        "findings": findings,
        "citations": citations,
        "confidence": conf,
    }


def normalize_hypothesis(raw: dict[str, Any]) -> dict[str, Any]:
    """收敛探索性假设输出。所有 citation 的 source_type 强制为 speculative。"""
    hypotheses = []
    for h in (raw.get("hypotheses") or [])[:MAX_HYPOTHESES]:
        if not isinstance(h, dict):
            continue
        hypotheses.append({
            "statement": _str(h.get("statement"), 500),
            "rationale": _str(h.get("rationale"), 500),
            "citation_ids": [str(c) for c in (h.get("citation_ids") or [])[:5]],
        })

    citations = []
    for c in (raw.get("citations") or [])[:MAX_HYPOTHESES * 2]:
        if not isinstance(c, dict):
            continue
        citations.append({
            "citation_id": _str(c.get("citation_id"), 64),
            "source_type": "speculative",  # 强制：假设引用必须是 speculative
            "source_id": _str(c.get("source_id"), 64),
            "quoted_text": _str(c.get("quoted_text"), MAX_QUOTED_TEXT),
            "confidence": "low",  # 推测性假设默认低置信度
        })

    return {"hypotheses": hypotheses, "citations": citations}


def normalize_brief(raw: dict[str, Any]) -> dict[str, Any]:
    """收敛简报输出 → {sections, citations}。"""
    sections = []
    for s in (raw.get("sections") or [])[:MAX_BRIEF_SECTIONS]:
        if not isinstance(s, dict):
            continue
        sections.append({
            "heading": _str(s.get("heading"), 128),
            "body": _str(s.get("body"), 4000),
            "citation_ids": [str(c) for c in (s.get("citation_ids") or [])[:10]],
            "is_speculative": bool(s.get("is_speculative")),
        })

    citations = []
    seen_ids: set[str] = set()
    for c in (raw.get("citations") or [])[:MAX_CITATIONS]:
        if not isinstance(c, dict):
            continue
        cid = _str(c.get("citation_id"), 64)
        if not cid or cid in seen_ids:
            continue
        seen_ids.add(cid)
        st = _str(c.get("source_type"), 32)
        if st not in _SOURCE_TYPES:
            st = "report_section"
        citations.append({
            "citation_id": cid,
            "source_type": st,
            "source_id": _str(c.get("source_id"), 64),
            "section_heading": _str(c.get("section_heading"), 128),
            "quoted_text": _str(c.get("quoted_text"), MAX_QUOTED_TEXT),
            "confidence": _str(c.get("confidence"), 8) if _str(c.get("confidence")) in _CONFIDENCE_LEVELS else "medium",
        })

    return {"sections": sections, "citations": citations}
