"""患者画像查询工具 — 过敏史/用药/既往史等，逐条带 source 标记。

仅在咨询会话中可用：查 consultations 域表得 patient_id。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .._protocols import ToolContext, text_result, error_result


@dataclass
class ReadPatientProfileTool:
    """查询当前咨询患者的画像。

    画像维度：过敏史/用药/既往史等，逐条带 source 标记（patient=患者自报）。
    仅在咨询会话中可用。
    """

    name: str = "read_patient_profile"
    description: str = (
        "查询当前咨询患者的画像：过敏史/用药/既往史等，逐条带 source 标记（patient=患者自报）。"
        "不返回报告全文，要下临床结论请先用 read_patient_reports 定位再用 read_raw_report 回查原文。"
    )
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {},
        "required": [],
    })
    tool_class: str = "read"
    side_effect: bool = False
    effect_class: str = ""

    # 由 bundle 注入
    store: Any = None

    async def execute(self, args: dict[str, Any], context: ToolContext) -> Any:
        """执行查询。"""
        call_id = args.get("_call_id", "")

        if self.store is None:
            return error_result(call_id, "MedicalStore 未注入，无法执行操作")

        # 入口校验：查 consultations 域表得 patient_id
        consultation = await self.store.get_consultation_by_session(context.session_id)
        if consultation is None:
            return text_result(call_id, "请在该患者的咨询会话中提问")

        patient_id = consultation["patient_id"]

        # 查患者画像
        patient = await self.store.get_patient(patient_id)
        if patient is None:
            return error_result(call_id, f"患者不存在: {patient_id}")

        # 构建画像：逐条带 source 标记
        profile: dict[str, Any] = {
            "patient_id": patient.get("id", ""),
            "name": patient.get("name", ""),
            "gender": patient.get("gender", ""),
            "age": _calc_age(patient.get("birth_date", "")),
            "primary_diagnosis": patient.get("primary_diagnosis", ""),
            "treatment_status": patient.get("treatment_status", ""),
            "current_regimen": patient.get("current_regimen", ""),
        }

        # 画像维度带 source 标记
        profile["allergies"] = _with_source(patient.get("allergies_json", []), "medical_record")
        profile["medications"] = _with_source(patient.get("medications_json", []), "medical_record")
        profile["history"] = _with_source(patient.get("history_json", []), "medical_record")
        profile["labs"] = _with_source(patient.get("labs_json", []), "medical_record")
        profile["vitals"] = _with_source(patient.get("vitals_json", {}), "medical_record")
        profile["biomarkers"] = _with_source(patient.get("biomarkers_json", {}), "medical_record")

        return text_result(
            call_id,
            json.dumps(profile, ensure_ascii=False, default=str),
        )


def _calc_age(birth_date_str: str) -> int:
    """从出生日期字符串计算年龄。"""
    if not birth_date_str:
        return 0
    try:
        from datetime import datetime, date
        bd = datetime.strptime(str(birth_date_str), "%Y-%m-%d").date()
        today = date.today()
        return today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
    except (ValueError, TypeError):
        return 0


def _with_source(data: Any, source: str) -> Any:
    """给画像数据添加 source 标记。列表项逐条标记，字典整体标记。"""
    if isinstance(data, list):
        return [{"value": item, "source": source} for item in data]
    if isinstance(data, dict):
        return {k: {"value": v, "source": source} for k, v in data.items()}
    return data
