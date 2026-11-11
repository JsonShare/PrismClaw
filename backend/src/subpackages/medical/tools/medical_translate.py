"""医学翻译工具 — 医学术语与文献的专业翻译。

符合 Tool Protocol：name / description / parameters / tool_class /
side_effect / effect_class + async execute()。

当前实现：返回翻译请求的结构化描述，由 LLM 在推理时完成实际翻译。
后续可接入专业医学翻译 API（如 DeepL Medical）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .._protocols import ToolContext, text_result, error_result

# 医学子域术语表（核心术语对照）
_MEDICAL_GLOSSARY: dict[str, dict[str, str]] = {
    "oncology": {
        "chemotherapy": "化疗", "radiotherapy": "放疗", "immunotherapy": "免疫治疗",
        "targeted_therapy": "靶向治疗", "neoadjuvant": "新辅助", "adjuvant": "辅助",
        "metastasis": "转移", "remission": "缓解", "recurrence": "复发",
    },
    "cardiology": {
        "myocardial_infarction": "心肌梗死", "arrhythmia": "心律失常",
        "hypertension": "高血压", "stent": "支架", "angioplasty": "血管成形术",
    },
    "pharmacology": {
        "contraindication": "禁忌症", "adverse_reaction": "不良反应",
        "pharmacokinetics": "药代动力学", "bioavailability": "生物利用度",
        "half_life": "半衰期",
    },
}


@dataclass
class MedicalTranslateTool:
    """医学翻译工具。

    支持：
    - 中英医学术语互译
    - 医学文献段落翻译
    - 药品名称翻译
    """

    name: str = "medical_translate"
    description: str = "医学翻译工具 — 医学术语与文献的专业翻译"
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "待翻译文本",
            },
            "source_lang": {
                "type": "string",
                "enum": ["zh", "en"],
                "description": "源语言",
            },
            "target_lang": {
                "type": "string",
                "enum": ["zh", "en"],
                "description": "目标语言",
            },
            "domain": {
                "type": "string",
                "enum": ["general", "oncology", "cardiology", "pharmacology"],
                "description": "医学子域，提升翻译专业度",
            },
        },
        "required": ["text", "source_lang", "target_lang"],
    })
    tool_class: str = "read"
    side_effect: bool = False
    effect_class: str = "translation"

    async def execute(self, args: dict[str, Any], context: ToolContext) -> Any:
        """执行医学翻译。"""
        call_id = args.get("_call_id", "")
        text = args.get("text", "")
        source_lang = args.get("source_lang", "")
        target_lang = args.get("target_lang", "")
        domain = args.get("domain", "general")

        if not text:
            return error_result(call_id, "缺少待翻译文本 text")
        if source_lang == target_lang:
            return error_result(call_id, "源语言与目标语言不能相同")

        # 收集相关术语对照
        glossary_terms: dict[str, str] = {}
        if domain != "general":
            glossary_terms = _MEDICAL_GLOSSARY.get(domain, {})

        result = {
            "text": text,
            "source_lang": source_lang,
            "target_lang": target_lang,
            "domain": domain,
            "glossary": glossary_terms,
            "instruction": (
                f"请将以下{source_lang}医学文本翻译为{target_lang}，"
                f"子域为{domain}。请确保术语准确，遵循医学表达规范。"
            ),
        }
        return text_result(call_id, json.dumps(result, ensure_ascii=False))
