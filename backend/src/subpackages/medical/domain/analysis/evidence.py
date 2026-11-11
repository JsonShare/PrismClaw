"""医疗域证据推断——从核心 engine 迁移出的领域逻辑。

推断器通过 evidence_inference.register_inferencer() 注册，
核心 engine 只调用 infer_domain_fields()，不包含医疗领域知识。
"""
from __future__ import annotations

import re as _re

_MEDICAL_URL_PATTERNS: list[tuple[str, str]] = [
    (r"cochranelibrary\.com", "1a"),
    (r"clinicaltrials\.gov", "1b"),
    (r"meta\.analysis|systematic\.review", "1a"),
    (r"pubmed\.ncbi", "3"),
    (r"who\.int", "3"),
    (r"nhc\.gov\.cn", "3"),
    (r"guideline", "3"),
]

_MEDICAL_CONTENT_KEYWORDS: list[tuple[str, str]] = [
    ("Meta分析", "1a"), ("系统评价", "1a"), ("systematic review", "1a"),
    ("随机对照", "1b"), ("RCT", "1b"), ("randomized controlled", "1b"),
    ("队列研究", "2a"), ("病例对照", "2b"), ("横断面", "3"),
    ("专家共识", "4"), ("专家意见", "4"), ("expert opinion", "4"),
    ("推荐等级A", "1a"), ("推荐等级B", "2a"),
]


def medical_evidence_inferencer(
    source_type: str,
    source_path: str,
    result_text: str,
) -> dict[str, str]:
    """推断医疗循证扩展字段，key 带 medical. 前缀。

    由 MedicalBundle.on_build_runtime() 注册到 evidence_inference 注册表。
    """
    fields: dict[str, str] = {}
    path_lower = source_path.lower()
    text_lower = result_text[:5000].lower()

    if source_type == "web":
        for pattern, level in _MEDICAL_URL_PATTERNS:
            if _re.search(pattern, path_lower):
                fields["medical.evidence_level"] = level
                break

    if not fields.get("medical.evidence_level"):
        for keyword, level in _MEDICAL_CONTENT_KEYWORDS:
            if keyword.lower() in text_lower:
                fields["medical.evidence_level"] = level
                break

    return fields
