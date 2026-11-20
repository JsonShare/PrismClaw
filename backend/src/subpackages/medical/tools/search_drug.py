"""药品检索工具 — 检索药品说明书信息、适应症与注意事项。

符合 Tool Protocol：name / description / parameters / tool_class /
side_effect / effect_class + async execute()。

数据源：`pc_medical_kb_drugs`（西药 / 中成药三级分类目录 + 说明书要点），
由 bundle 在注册时注入 KbStore 实例。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .._protocols import ToolContext, text_result, error_result


@dataclass
class SearchDrugTool:
    """药品检索工具。

    支持：
    - 按药品名 / 通用名检索
    - 适应症与禁忌查询
    - 不良反应与相互作用查询
    """

    name: str = "search_drug"
    description: str = "药品检索工具 — 检索知识库中药品说明书的适应症、用法用量、禁忌与不良反应"
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "药品名 / 通用名",
            },
            "action": {
                "type": "string",
                "enum": ["info", "indication", "interaction"],
                "description": "查询类型：info=基本信息, indication=适应症与禁忌, interaction=相互作用",
                "default": "info",
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
        """执行药品检索。"""
        call_id = args.get("_call_id", "")
        query = args.get("query", "")
        action = args.get("action") or "info"

        if not query:
            return error_result(call_id, "缺少查询药品名 query")
        if action not in ("info", "indication", "interaction"):
            return error_result(call_id, f"未知 action: {action}")
        if self.kb_store is None:
            return error_result(call_id, "KbStore 未注入，无法检索药品")

        limit = int(args.get("limit", 10) or 10)
        page = await self.kb_store.list_drugs(keyword=query, page=1, page_size=max(limit * 5, 50))

        results: list[dict[str, Any]] = []
        for d in page["items"]:
            base = {
                "name": d.get("name", ""),
                "generic_name": d.get("generic_name", ""),
                "category": d.get("category", ""),
                "drug_class": d.get("atc_l2", "") or d.get("atc_group", ""),
            }
            if action == "info":
                base.update({
                    "dosage": d.get("dosage", ""),
                    "mechanism": d.get("mechanism", ""),
                    "pregnancy_grade": d.get("pregnancy_grade", ""),
                })
            elif action == "indication":
                base.update({
                    "indications": d.get("indications_json") or [],
                    "contraindications": d.get("contraindications_json") or [],
                    "side_effects": d.get("side_effects_json") or [],
                })
            else:
                base["interactions"] = d.get("interactions_json") or []
            results.append(base)
            if len(results) >= limit:
                break

        payload: dict[str, Any] = {
            "query": query, "action": action,
            "results": results, "count": len(results),
            "total_matched": page["total"],
        }
        if not results:
            payload["message"] = "未找到匹配药品"
        return text_result(call_id, json.dumps(payload, ensure_ascii=False))
