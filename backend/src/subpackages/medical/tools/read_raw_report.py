"""原始报告查询工具 — 返回 raw_text 全文。

数据权威层级：抽取字段定位，原文下结论。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .._protocols import ToolContext, text_result, error_result


@dataclass
class ReadRawReportTool:
    """查询原始报告全文。

    返回 raw_text 全文（数据权威层级：抽取字段定位，原文下结论）。
    仅在咨询会话中可用：查 consultations 域表验证归属。
    """

    name: str = "read_raw_report"
    description: str = (
        "查询原始报告全文。参数为 report_id（从 read_patient_reports 获取）。"
        "数据权威层级：抽取字段定位，原文下结论。"
    )
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "report_id": {
                "type": "string",
                "description": "报告 ID（从 read_patient_reports 返回的 report_id）",
            },
        },
        "required": ["report_id"],
    })
    tool_class: str = "read"
    side_effect: bool = False
    effect_class: str = ""

    # 由 bundle 注入
    store: Any = None

    async def execute(self, args: dict[str, Any], context: ToolContext) -> Any:
        """执行查询。"""
        call_id = args.get("_call_id", "")
        report_id = args.get("report_id", "")

        if self.store is None:
            return error_result(call_id, "MedicalStore 未注入，无法执行操作")

        if not report_id:
            return error_result(call_id, "需要 report_id 参数")

        # 入口校验：查 consultations 域表验证归属
        consultation = await self.store.get_consultation_by_session(context.session_id)
        if consultation is None:
            return text_result(call_id, "请在该患者的咨询会话中提问")

        patient_id = consultation["patient_id"]

        # 查报告
        report = await self.store.get_report(report_id)
        if report is None:
            return error_result(call_id, f"报告不存在: {report_id}")

        # 校验报告归属该患者
        if report.get("patient_id") != patient_id:
            return error_result(call_id, "该报告不属于当前咨询的患者")

        raw_text = report.get("raw_text", "")
        if not raw_text:
            status = report.get("status", "排队中")
            return text_result(
                call_id,
                json.dumps({
                    "report_id": report_id,
                    "status": status,
                    "note": "原始文本为空，可能尚未完成解析",
                }, ensure_ascii=False),
            )

        return text_result(
            call_id,
            json.dumps({
                "report_id": report_id,
                "raw_text": raw_text,
            }, ensure_ascii=False),
        )
