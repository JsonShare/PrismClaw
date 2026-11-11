"""咨询上下文注入钩子 — SESSION_START 事件。

会话启动时查询咨询登记表匹配当前 session_id：
- 匹配到 → 注入该患者的报告清单摘要（标题/类型/日期，不含正文）
- 不匹配 → 跳过（普通会话，非咨询场景）

摘要格式：
  [患者咨询上下文] 患者ID={pid}，共 {n} 份报告：
  1. {file_name} ({report_type}, {visit_date}, 诊断: {diagnosis})
  2. ...

此摘要作为 additional_context 注入首轮对话（transient，不入历史）。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..._protocols import HookHandler, HookContext, HookResult, HookOutcome

if TYPE_CHECKING:
    from ...persistence.medical_store import MedicalStore

logger = logging.getLogger(__name__)

# 摘要中报告条数上限，避免注入过长上下文
_MAX_REPORTS_IN_SUMMARY = 20


class ConsultationContextHook(HookHandler):
    """SESSION_START 钩子 — 注入咨询患者的报告清单摘要。"""

    def __init__(self) -> None:
        self._store: MedicalStore | None = None

    @property
    def name(self) -> str:
        return "consultation_context"

    @property
    def event(self) -> str:
        return "session_start"

    async def execute(self, context: HookContext) -> HookResult:
        """查咨询登记 → 注入报告清单摘要。"""
        if not self._store:
            return HookResult.success()

        session_id = context.session_id
        if not session_id:
            return HookResult.success()

        try:
            consultation = await self._store.get_consultation_by_session(session_id)
        except Exception:
            logger.exception("查询咨询登记失败: session=%s", session_id)
            return HookResult(
                outcome=HookOutcome.NON_BLOCKING_ERROR,
                system_message="咨询登记查询异常",
            )

        if not consultation:
            # 非咨询会话，跳过
            return HookResult.success()

        patient_id: str = consultation["patient_id"]
        doctor_id: str = consultation["doctor_id"]

        try:
            summary = await self._build_report_summary(patient_id, doctor_id)
        except Exception:
            logger.exception(
                "构建报告摘要失败: patient=%s doctor=%s", patient_id, doctor_id
            )
            return HookResult(
                outcome=HookOutcome.NON_BLOCKING_ERROR,
                system_message="报告摘要构建异常",
            )

        if not summary:
            return HookResult(
                additional_context=(
                    f"[患者咨询上下文] 患者ID={patient_id}，暂无报告记录。"
                ),
            )

        return HookResult(additional_context=summary)

    async def _build_report_summary(
        self, patient_id: str, doctor_id: str
    ) -> str | None:
        """构建报告清单摘要文本。"""
        from sqlalchemy import select

        from ...models.tables import patient_reports

        if not self._store:
            return None

        async with self._store._engine.connect() as conn:
            stmt = (
                select(
                    patient_reports.c.file_name,
                    patient_reports.c.report_type,
                    patient_reports.c.visit_date,
                    patient_reports.c.diagnosis,
                )
                .where(
                    (patient_reports.c.patient_id == patient_id)
                    & (patient_reports.c.create_by == doctor_id)
                    & (patient_reports.c.is_deleted == 0)
                )
                .order_by(patient_reports.c.create_at.desc())
                .limit(_MAX_REPORTS_IN_SUMMARY)
            )
            result = await conn.execute(stmt)
            rows = result.fetchall()

        if not rows:
            return None

        lines = [f"[患者咨询上下文] 患者ID={patient_id}，共 {len(rows)} 份报告："]
        for i, row in enumerate(rows, 1):
            file_name = row.file_name or "未命名"
            report_type = row.report_type or "未知类型"
            visit_date = str(row.visit_date) if row.visit_date else "未知日期"
            diagnosis = row.diagnosis or "无"
            lines.append(
                f"  {i}. {file_name} ({report_type}, {visit_date}, 诊断: {diagnosis})"
            )

        return "\n".join(lines)
