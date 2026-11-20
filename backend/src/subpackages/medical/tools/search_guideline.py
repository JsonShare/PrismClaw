"""临床指南检索工具 — 检索知识库中的临床实践指南。

符合 Tool Protocol：name / description / parameters / tool_class /
side_effect / effect_class + async execute()。

数据源：`pc_medical_kb_guidelines`（NCCN / CSCO / 中华医学会等，由知识库导入脚本写入），
由 bundle 在注册时注入 KbStore 实例。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .._protocols import ToolContext, text_result, error_result


@dataclass
class SearchGuidelineTool:
    """临床指南检索工具。

    支持：
    - 按疾病/关键词检索指南（匹配中英文标题与作者）
    - 发表年份区间过滤
    - 返回机构、学科、版本、摘要与核心推荐
    """

    name: str = "search_guideline"
    description: str = "临床指南检索工具 — 检索知识库中的 NCCN / CSCO / 中华医学会等临床实践指南"
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "检索关键词（疾病名、指南名、发布机构等）",
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

    # 由 bundle 注入
    kb_store: Any = None

    async def execute(self, args: dict[str, Any], context: ToolContext) -> Any:
        """执行临床指南检索。"""
        call_id = args.get("_call_id", "")
        query = args.get("query", "")
        if not query:
            return error_result(call_id, "缺少检索关键词 query")
        if self.kb_store is None:
            return error_result(call_id, "KbStore 未注入，无法检索指南")

        year_from = args.get("year_from")
        year_to = args.get("year_to")
        limit = int(args.get("limit", 10) or 10)

        # 多取一些再按年份区间过滤，保证过滤后仍有足够结果
        page = await self.kb_store.list_guidelines(
            keyword=query, page=1, page_size=max(limit * 5, 50),
        )

        results: list[dict[str, Any]] = []
        for g in page["items"]:
            year = g.get("year") or 0
            if year_from and year < year_from:
                continue
            if year_to and year > year_to:
                continue
            results.append({
                "title": g.get("title", ""),
                "title_cn": g.get("title_cn", ""),
                "organization": g.get("org", ""),
                "doc_type": g.get("doc_type", ""),
                "specialty": g.get("specialty", ""),
                "region": g.get("region", ""),
                "year": year,
                "version": g.get("version", ""),
                "authors": g.get("authors", ""),
                "summary": g.get("abstract", ""),
                "key_recommendations": g.get("points_json") or [],
            })
            if len(results) >= limit:
                break

        return text_result(
            call_id,
            json.dumps({"query": query, "results": results, "count": len(results),
                        "total_matched": page["total"]}, ensure_ascii=False),
        )
