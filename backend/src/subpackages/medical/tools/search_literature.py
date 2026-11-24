"""文献检索工具 — 检索知识库中的医学文献。

符合 Tool Protocol：name / description / parameters / tool_class /
side_effect / effect_class + async execute()。

数据源：`pc_medical_kb_literatures`（含中英标题、研究类型、期刊、影响因子、发布时间），
由 bundle 在注册时注入 KbStore 实例。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .._protocols import ToolContext, error_result, text_result


@dataclass
class SearchLiteratureTool:
    """文献检索工具。

    支持：
    - 关键词检索文献（匹配中英文标题）
    - 按年份区间过滤
    - 返回期刊、影响因子、研究类型与摘要
    """

    name: str = "search_literature"
    description: str = (
        "医学文献检索工具（仅用于医学文献检索，不用于一般网络搜索）— "
        "检索知识库中的权威期刊文献。企业资料、公司信息、新闻等通用网络检索请用 "
        "web-search-mcp:search"
    )
    parameters: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索关键词（疾病名、药物名、研究主题等）",
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
        }
    )
    tool_class: str = "read"
    side_effect: bool = False
    effect_class: str = "search"

    # 由 bundle 注入
    kb_store: Any = None

    async def execute(self, args: dict[str, Any], context: ToolContext) -> Any:
        """执行文献检索。"""
        call_id = args.get("_call_id", "")
        query = args.get("query", "")
        if not query:
            return error_result(call_id, "缺少检索关键词 query")
        if self.kb_store is None:
            return error_result(call_id, "KbStore 未注入，无法检索文献")

        year_from = args.get("year_from")
        year_to = args.get("year_to")
        limit = int(args.get("limit", 10) or 10)

        page = await self.kb_store.list_literatures(
            keyword=query,
            page=1,
            page_size=max(limit * 5, 50),
        )

        results: list[dict[str, Any]] = []
        for lit in page["items"]:
            year = lit.get("year") or 0
            if year_from and year < year_from:
                continue
            if year_to and year > year_to:
                continue
            results.append(
                {
                    "title": lit.get("title_en") or lit.get("title", ""),
                    "title_cn": lit.get("title_cn", ""),
                    "authors": lit.get("authors", ""),
                    "journal": lit.get("journal", ""),
                    "journal_tags": lit.get("journal_tags_json") or [],
                    "impact_factor": lit.get("impact_factor") or 0,
                    "study_type": lit.get("study_type", ""),
                    "year": year,
                    "published_at": str(lit.get("published_at") or ""),
                    "doi": lit.get("doi", ""),
                    "abstract": lit.get("abstract", ""),
                }
            )
            if len(results) >= limit:
                break

        return text_result(
            call_id,
            json.dumps(
                {
                    "query": query,
                    "results": results,
                    "count": len(results),
                    "total_matched": page["total"],
                },
                ensure_ascii=False,
            ),
        )
