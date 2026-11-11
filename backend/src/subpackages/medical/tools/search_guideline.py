"""临床指南检索工具 — 检索循证医学临床实践指南。

符合 Tool Protocol：name / description / parameters / tool_class /
side_effect / effect_class + async execute()。

当前实现：基于内置指南索引进行关键词匹配。
后续可接入 NICE / SIGN / CMA 等指南数据库 API。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .._protocols import ToolContext, text_result, error_result

# 内置指南索引（示例数据，后续替换为数据库或外部 API）
_GUIDELINE_INDEX: list[dict[str, Any]] = [
    {
        "id": "NICE-NG101",
        "title": "Lung cancer: diagnosis and management",
        "organization": "NICE",
        "year": 2019,
        "evidence_level": "A",
        "keywords": ["lung cancer", "NSCLC", "SCLC", "肺癌"],
        "summary": "涵盖非小细胞肺癌和小细胞肺癌的诊断、分期及治疗推荐。",
    },
    {
        "id": "CSCO-2024-Breast",
        "title": "乳腺癌诊疗指南（2024版）",
        "organization": "CSCO",
        "year": 2024,
        "evidence_level": "A",
        "keywords": ["breast cancer", "乳腺癌", "HER2", "HR+"],
        "summary": "中国临床肿瘤学会乳腺癌诊疗指南，涵盖早期到晚期全流程管理。",
    },
    {
        "id": "ESMO-Colorectal",
        "title": "Pan-Asian adapted ESMO consensus guidelines for colorectal cancer",
        "organization": "ESMO",
        "year": 2022,
        "evidence_level": "A",
        "keywords": ["colorectal cancer", "结直肠癌", "CRC", "mCRC"],
        "summary": "泛亚洲适应版结直肠癌诊疗共识指南。",
    },
    {
        "id": "AHA-Hypertension",
        "title": "ACC/AHA Guideline for the Prevention, Detection, Evaluation, and Management of High Blood Pressure in Adults",
        "organization": "AHA/ACC",
        "year": 2017,
        "evidence_level": "A",
        "keywords": ["hypertension", "高血压", "blood pressure"],
        "summary": "成人高血压预防、检测、评估和管理指南。",
    },
]


@dataclass
class SearchGuidelineTool:
    """临床指南检索工具。

    支持：
    - 按疾病/关键词检索指南
    - 指南推荐等级过滤
    - 指南摘要提取
    """

    name: str = "search_guideline"
    description: str = "临床指南检索工具 — 检索循证医学临床实践指南"
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "检索关键词（疾病名、指南名等）",
            },
            "evidence_level": {
                "type": "string",
                "enum": ["A", "B", "C", "D"],
                "description": "证据等级过滤",
            },
            "year_from": {
                "type": "integer",
                "description": "发表年份下限",
            },
            "year_to": {
                "type": "integer",
                "description": "发表年份上限",
            },
            "limit": {
                "type": "integer",
                "description": "返回结果数量上限",
                "default": 10,
            },
        },
        "required": ["query"],
    })
    tool_class: str = "read"
    side_effect: bool = False
    effect_class: str = "search"

    async def execute(self, args: dict[str, Any], context: ToolContext) -> Any:
        """执行临床指南检索。"""
        call_id = args.get("_call_id", "")
        query = args.get("query", "")
        if not query:
            return error_result(call_id, "缺少检索关键词 query")

        evidence_level = args.get("evidence_level")
        year_from = args.get("year_from")
        year_to = args.get("year_to")
        limit = args.get("limit", 10)

        query_lower = query.lower()

        # 关键词匹配
        results: list[dict[str, Any]] = []
        for g in _GUIDELINE_INDEX:
            # 检查关键词或标题匹配
            if not (query_lower in g["title"].lower()
                    or any(query_lower in kw.lower() for kw in g["keywords"])):
                continue
            # 证据等级过滤
            if evidence_level and g.get("evidence_level") != evidence_level:
                continue
            # 年份范围过滤
            if year_from and g.get("year", 0) < year_from:
                continue
            if year_to and g.get("year", 0) > year_to:
                continue
            results.append(g)
            if len(results) >= limit:
                break

        return text_result(
            call_id,
            json.dumps({"query": query, "results": results, "count": len(results)}, ensure_ascii=False),
        )
