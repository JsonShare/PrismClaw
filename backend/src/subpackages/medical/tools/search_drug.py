"""药品检索工具 — 检索药品信息、适应症与相互作用。

符合 Tool Protocol：name / description / parameters / tool_class /
side_effect / effect_class + async execute()。

当前实现：基于内置药品数据库进行检索。
后续可接入 DrugBank / 国家药监局 API。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .._protocols import ToolContext, text_result, error_result

# 内置药品数据库（示例数据）
_DRUG_DB: list[dict[str, Any]] = [
    {
        "generic_name": "Osimertinib",
        "brand_names": ["Tagrisso", "泰瑞沙"],
        "category": "靶向药",
        "indications": ["EGFR T790M 突变阳性非小细胞肺癌", "EGFR Ex19del/L858R 一线治疗"],
        "contraindications": ["对奥希替尼过敏者"],
        "interactions": ["CYP3A4 强诱导剂降低血药浓度", "与华法林合用增加出血风险"],
    },
    {
        "generic_name": "Pembrolizumab",
        "brand_names": ["Keytruda", "帕博利珠单抗"],
        "category": "免疫检查点抑制剂",
        "indications": ["PD-L1 高表达 NSCLC 一线", "黑色素瘤", "MSI-H/dMMR 实体瘤"],
        "contraindications": ["严重自身免疫性疾病", "器官移植受者"],
        "interactions": ["免疫抑制剂降低疗效", "与其他免疫治疗联用增加免疫相关不良事件"],
    },
    {
        "generic_name": "Capecitabine",
        "brand_names": ["Xeloda", "希罗达"],
        "category": "化疗药（抗代谢类）",
        "indications": ["结直肠癌", "乳腺癌", "胃癌"],
        "contraindications": ["DPD 酶缺乏", "严重肝肾功能不全"],
        "interactions": ["与华法林合用显著增加 INR", "亚叶酸钙增强毒性"],
    },
    {
        "generic_name": "Amlodipine",
        "brand_names": ["Norvasc", "络活喜"],
        "category": "钙通道阻滞剂",
        "indications": ["高血压", "心绞痛"],
        "contraindications": ["严重主动脉瓣狭窄", "心源性休克"],
        "interactions": ["CYP3A4 抑制剂增加血药浓度", "与辛伐他汀合用限制辛伐他汀剂量≤20mg"],
    },
]


@dataclass
class SearchDrugTool:
    """药品检索工具。

    支持：
    - 按药品名/通用名检索
    - 适应症与禁忌查询
    - 药品相互作用查询
    """

    name: str = "search_drug"
    description: str = "药品检索工具 — 检索药品信息、适应症与相互作用"
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "药品名/通用名/商品名",
            },
            "action": {
                "type": "string",
                "enum": ["info", "indication", "interaction"],
                "description": "查询类型：info=基本信息, indication=适应症, interaction=相互作用",
            },
            "drug_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "相互作用查询的药品列表（interaction 时必填）",
            },
        },
        "required": ["query", "action"],
    })
    tool_class: str = "read"
    side_effect: bool = False
    effect_class: str = "search"

    async def execute(self, args: dict[str, Any], context: ToolContext) -> Any:
        """执行药品检索。"""
        call_id = args.get("_call_id", "")
        query = args.get("query", "")
        action = args.get("action", "")

        if not query:
            return error_result(call_id, "缺少查询药品名 query")
        if not action:
            return error_result(call_id, "缺少查询类型 action")

        query_lower = query.lower()

        # 匹配药品
        matched = []
        for drug in _DRUG_DB:
            names = [drug["generic_name"].lower()] + [n.lower() for n in drug.get("brand_names", [])]
            if any(query_lower in n for n in names):
                matched.append(drug)

        if not matched:
            return text_result(call_id, json.dumps({"query": query, "results": [], "message": "未找到匹配药品"}, ensure_ascii=False))

        if action == "info":
            results = [{"generic_name": d["generic_name"], "brand_names": d["brand_names"], "category": d["category"]} for d in matched]
        elif action == "indication":
            results = [{"generic_name": d["generic_name"], "indications": d["indications"], "contraindications": d["contraindications"]} for d in matched]
        elif action == "interaction":
            drug_names = args.get("drug_names", [])
            results = []
            for d in matched:
                entry = {"generic_name": d["generic_name"], "interactions": d["interactions"]}
                if drug_names:
                    entry["query_drug_names"] = drug_names
                results.append(entry)
        else:
            return error_result(call_id, f"未知 action: {action}")

        return text_result(call_id, json.dumps({"query": query, "action": action, "results": results}, ensure_ascii=False))
