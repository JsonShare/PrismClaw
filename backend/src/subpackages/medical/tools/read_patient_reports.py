"""患者报告查询工具 — 按元数据+关键词过滤该患者的报告。

仅在咨询会话中可用：查 consultations 域表得 patient_id。
患者不从参数来，从域表查——读错患者、模型传错 id，从结构上不存在。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .._protocols import ToolContext, text_result, error_result


@dataclass
class ReadPatientReportsTool:
    """查询该患者的临床报告列表。

    按元数据（类型/日期/医院）+ 关键词过滤。
    返回抽取字段的概览，每条带 report_id 供 read_raw_report 引用。
    未解析完的报告明说"尚未完成解析"，不返回空。
    下临床结论前由模型再调 read_raw_report 回查原文。
    """

    name: str = "read_patient_reports"
    description: str = (
        "查询当前咨询患者的临床报告列表。按元数据（类型/日期/医院）+ 关键词过滤。"
        "返回抽取字段的概览，每条带 report_id 供 read_raw_report 引用。"
        "未解析完的报告标注'尚未完成解析'。下临床结论前须再调 read_raw_report 回查原文。"
    )
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词（命中诊断/文件名/摘要）",
            },
            "report_type": {
                "type": "string",
                "description": "报告类型过滤（如 outpatient/inspection/exam）",
            },
        },
        "required": ["query"],
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
        query = args.get("query", "")
        report_type = args.get("report_type")

        # 查该患者的全部报告
        criteria: dict[str, Any] = {"patient_id": patient_id}
        if report_type:
            criteria["report_type"] = report_type

        reports = await self.store.search_reports(criteria)

        # 按关键词过滤（命中诊断/文件名/摘要）
        if query:
            q = query.lower()
            reports = [
                r for r in reports
                if q in (r.get("diagnosis", "") or "").lower()
                or q in (r.get("file_name", "") or "").lower()
                or q in (r.get("summary", "") or "").lower()
            ]

        # 构建概览：抽取字段，带 report_id，未解析的明说
        overview = []
        for r in reports:
            status = r.get("status", "排队中")
            entry = {
                "report_id": r.get("id", ""),
                "report_type": r.get("report_type", ""),
                "file_name": r.get("file_name", ""),
                "diagnosis": r.get("diagnosis", ""),
                "visit_date": str(r.get("visit_date", "") or ""),
                "hospital_name": r.get("hospital_name", ""),
                "status": status,
            }
            if status != "已解析":
                entry["note"] = "尚未完成解析，不可用于下临床结论"
            overview.append(entry)

        return text_result(
            call_id,
            json.dumps({"patient_id": patient_id, "reports": overview, "total": len(overview)}, ensure_ascii=False, default=str),
        )
