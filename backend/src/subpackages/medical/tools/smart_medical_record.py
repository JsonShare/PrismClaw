"""智能病历工具 — 结构化病历录入、查询与摘要生成。

符合 Tool Protocol：name / description / parameters / tool_class /
side_effect / effect_class + async execute()。
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from .._protocols import ToolContext, text_result, error_result


@dataclass
class SmartMedicalRecordTool:
    """智能病历工具。

    支持：
    - 病历结构化录入
    - 病历条件查询
    - 病历摘要自动生成
    """

    name: str = "smart_medical_record"
    description: str = "智能病历工具 — 结构化录入、查询与摘要生成"
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "query", "summarize"],
                "description": "操作类型：create=录入, query=查询, summarize=摘要",
            },
            "patient_id": {
                "type": "string",
                "description": "患者 ID",
            },
            "record_data": {
                "type": "object",
                "description": "病历数据（录入时必填）",
            },
            "query_criteria": {
                "type": "object",
                "description": "查询条件（查询时必填）",
            },
        },
        "required": ["action"],
    })
    tool_class: str = "write"
    side_effect: bool = True
    effect_class: str = "medical_record"

    # 由 bundle 注入
    store: Any = None

    async def execute(self, args: dict[str, Any], context: ToolContext) -> Any:
        """执行智能病历操作。"""
        call_id = args.get("_call_id", "")
        action = args.get("action", "")
        user_id = context.user_id or "system"

        if self.store is None:
            return error_result(call_id, "MedicalStore 未注入，无法执行操作")

        try:
            if action == "create":
                return await self._handle_create(args, call_id, user_id)
            elif action == "query":
                return await self._handle_query(args, call_id)
            elif action == "summarize":
                return await self._handle_summarize(args, call_id)
            else:
                return error_result(call_id, f"未知 action: {action}")
        except Exception as exc:
            return error_result(call_id, f"智能病历操作失败: {exc}")

    async def _handle_create(self, args: dict[str, Any], call_id: str, user_id: str) -> Any:
        """录入病历。"""
        patient_id = args.get("patient_id", "")
        record_data = args.get("record_data", {})
        if not patient_id:
            return error_result(call_id, "录入病历需要 patient_id")

        record_id = str(uuid.uuid4())

        # 若 record_data 包含患者信息，同步 upsert 患者
        if any(k in record_data for k in ("gender", "birth_year", "blood_type", "allergies")):
            await self.store.upsert_patient(patient_id, record_data, user_id=user_id)

        await self.store.create_report(record_id, patient_id, record_data, user_id=user_id)

        return text_result(
            call_id,
            json.dumps({"record_id": record_id, "patient_id": patient_id, "status": "created"}, ensure_ascii=False),
        )

    async def _handle_query(self, args: dict[str, Any], call_id: str) -> Any:
        """查询病历。"""
        criteria = args.get("query_criteria", {})
        if not criteria:
            return error_result(call_id, "查询需要 query_criteria")

        records = await self.store.search_reports(criteria)
        return text_result(
            call_id,
            json.dumps({"records": records, "count": len(records)}, ensure_ascii=False, default=str),
        )

    async def _handle_summarize(self, args: dict[str, Any], call_id: str) -> Any:
        """生成病历摘要。"""
        patient_id = args.get("patient_id", "")
        if not patient_id:
            return error_result(call_id, "摘要生成需要 patient_id")

        # 查询该患者所有文档
        records = await self.store.search_reports({"patient_id": patient_id})
        if not records:
            return text_result(call_id, json.dumps({"summary": "未找到病历记录"}, ensure_ascii=False))

        # 基于文档数据生成结构化摘要（新 schema：diagnosis / report_type / summary）
        diagnoses = set()
        report_types = set()
        for rec in records:
            if rec.get("diagnosis"):
                diagnoses.add(rec["diagnosis"])
            if rec.get("report_type"):
                report_types.add(rec["report_type"])

        summary = {
            "patient_id": patient_id,
            "total_reports": len(records),
            "report_types": sorted(report_types),
            "diagnoses": sorted(diagnoses),
            "latest_summary": records[0].get("summary", "") if records else "",
        }
        return text_result(call_id, json.dumps({"summary": summary}, ensure_ascii=False, default=str))
