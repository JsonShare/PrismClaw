"""文献检索工具 — 检索 PubMed / CNKI 等医学文献数据库。

符合 Tool Protocol：name / description / parameters / tool_class /
side_effect / effect_class + async execute()。

当前实现：基于内置示例数据，后续可对接 PubMed E-utilities API。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .._protocols import ToolContext, text_result, error_result

# 示例文献数据
_LITERATURE_DB: list[dict[str, Any]] = [
    {
        "pmid": "32123456",
        "title": "Osimertinib in EGFR-mutated NSCLC: 5-year follow-up of FLAURA trial",
        "authors": ["Ramalingam SS", "Vansteenkiste J", "Planchard D"],
        "journal": "Journal of Clinical Oncology",
        "year": 2020,
        "database": "pubmed",
        "abstract": "FLAURA 试验 5 年随访数据，奥希替尼一线治疗 EGFR 突变 NSCLC 的 PFS 和 OS 显著优于一代 EGFR-TKI。",
    },
    {
        "pmid": "33456789",
        "title": "Pembrolizumab plus chemotherapy for metastatic NSCLC: KEYNOTE-189 update",
        "authors": ["Gandhi L", "Rodríguez-Abreu D", "Gadgeel S"],
        "journal": "New England Journal of Medicine",
        "year": 2021,
        "database": "pubmed",
        "abstract": "KEYNOTE-189 更新数据，帕博利珠单抗联合化疗显著延长非鳞 NSCLC 总生存期。",
    },
    {
        "pmid": "CN20230101",
        "title": "中国非小细胞肺癌免疫治疗真实世界研究",
        "authors": ["张三", "李四", "王五"],
        "journal": "中华肿瘤杂志",
        "year": 2023,
        "database": "cnki",
        "abstract": "基于中国人群的 NSCLC 免疫治疗真实世界数据，PD-1 抑制剂 ORR 约 25%。",
    },
]


@dataclass
class SearchLiteratureTool:
    """文献检索工具。

    支持：
    - 关键词检索文献
    - 按年份/期刊过滤
    - 返回摘要与引用信息
    """

    name: str = "search_literature"
    description: str = "医学文献检索工具（仅用于医学文献检索，不用于一般网络搜索）— 检索 PubMed / CNKI 等医学文献数据库。企业资料、公司信息、新闻等通用网络检索请用 web-search-mcp:search"
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "检索关键词",
            },
            "database": {
                "type": "string",
                "enum": ["pubmed", "cnki"],
                "description": "目标数据库",
                "default": "pubmed",
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
        """执行文献检索。"""
        call_id = args.get("_call_id", "")
        query = args.get("query", "")
        if not query:
            return error_result(call_id, "缺少检索关键词 query")

        database = args.get("database", "pubmed")
        year_from = args.get("year_from")
        year_to = args.get("year_to")
        limit = args.get("limit", 10)

        query_lower = query.lower()

        results: list[dict[str, Any]] = []
        for lit in _LITERATURE_DB:
            # 数据库过滤
            if lit.get("database") != database:
                continue
            # 关键词匹配
            if not (query_lower in lit["title"].lower() or query_lower in lit.get("abstract", "").lower()):
                continue
            # 年份范围过滤
            if year_from and lit.get("year", 0) < year_from:
                continue
            if year_to and lit.get("year", 0) > year_to:
                continue
            results.append(lit)
            if len(results) >= limit:
                break

        return text_result(
            call_id,
            json.dumps({"query": query, "database": database, "results": results, "count": len(results)}, ensure_ascii=False),
        )
