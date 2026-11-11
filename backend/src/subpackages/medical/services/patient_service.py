"""患者服务 — 患者查询、统计、时间线组装。

从 routes/patients.py 提取的业务逻辑。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..persistence.medical_store import MedicalStore


async def list_patients(
    store: MedicalStore,
    *,
    keyword: str = "",
    page: int = 1,
    page_size: int = 8,
    create_by: str | None = None,
) -> dict[str, Any]:
    """患者分页列表。create_by: 行级过滤，仅返回该用户创建的患者。"""
    total = await store.count_patients(search=keyword, create_by=create_by)
    start = (page - 1) * page_size
    items = await store.list_patients(
        search=keyword, limit=page_size, offset=start, create_by=create_by,
    )
    # 列表卡片需显示该患者的报告份数，一次批量查询避免 N+1
    counts = await store.count_reports_by_patients(
        [p["id"] for p in items if p.get("id")]
    )
    # 列表项不含 timeline，补空数组以匹配前端 PatientProfile 类型
    for item in items:
        item["timeline"] = []
        item["report_count"] = counts.get(item.get("id"), 0)
    return {"items": items, "total": total, "page": page, "pageSize": page_size}


async def create_patient(
    store: MedicalStore,
    *,
    name: str,
    id_type: str,
    id_number: str,
    user_id: str,
) -> str:
    """新建患者档案，返回新患者 id。

    仅做行级归属（create_by = 当前用户）与脱敏入库；姓名非空校验在路由层完成。
    """
    return await store.create_patient(
        name=name, id_type=id_type, id_number=id_number, user_id=user_id,
    )


async def get_patient_detail(
    store: MedicalStore,
    patient_id: str,
    *,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    """患者详情（含派生时间线）。

    user_id 提供时执行行级归属校验：非该用户创建的患者返回 None。
    """
    patient = await store.get_patient(patient_id)
    if patient is None:
        return None
    if user_id is not None and patient.get("create_by") != user_id:
        return None
    timeline_raw = await store.get_timeline(patient_id)
    timeline = [
        {
            "date": t.get("event_date", "")[:10] if len(t.get("event_date", "")) >= 10 else t.get("event_date", ""),
            "event": t.get("title", "") or t.get("detail", ""),
            "detail": t.get("detail", ""),
            "eventType": t.get("event_type", ""),
        }
        for t in timeline_raw
    ]
    patient["timeline"] = timeline
    patient["report_count"] = (
        await store.count_reports_by_patients([patient_id])
    ).get(patient_id, 0)
    return patient


async def get_patient_stats(
    store: MedicalStore, *, create_by: str | None = None,
) -> dict[str, Any]:
    """统计条四项指标。create_by: 行级过滤。"""
    return await store.get_stats(create_by=create_by)


async def list_patient_reports(
    store: MedicalStore,
    patient_id: str,
    *,
    page: int = 1,
    page_size: int = 20,
    user_id: str | None = None,
) -> dict[str, Any] | None:
    """患者「报告解析」卡列表（服务端分页）。

    user_id 提供时执行行级归属校验，非该用户的患者返回 None。
    """
    patient = await store.get_patient(patient_id)
    if patient is None or (user_id is not None and patient.get("create_by") != user_id):
        return None
    total = (await store.count_reports_by_patients([patient_id])).get(patient_id, 0)
    start = (page - 1) * page_size
    reports = await store.list_patient_reports(
        patient_id, limit=page_size, offset=start,
    )
    return {
        "items": reports,
        "total": total,
        "page": page,
        "pageSize": page_size,
    }


# 医生可手工修正的字段白名单。设计稿 v3 的「修正」覆盖 AI 摘要（叙事段）、
# AI 建议、报告原文正文与原文元信息（机构/科室/日期/医师）。
# 其余列（patient_id / status / file_path 等）一律不接受外部改写。
CORRECTABLE_KEYS: dict[str, str] = {
    "narrative": "narrative_json",
    "summary": "summary",
    "rawText": "raw_text",
    "hospitalName": "hospital_name",
    "doctorName": "doctor_name",
    "department": "department",
}


async def correct_report(
    store: MedicalStore,
    patient_id: str,
    report_id: str,
    patch: dict[str, Any],
    *,
    user_id: str,
) -> dict[str, Any] | None:
    """修正报告的结构化内容（AI 摘要 / AI 建议 / 报告原文）。

    返回更新后的原始行；报告不存在或不属该患者返回 None。
    """
    report = await store.get_report(report_id)
    if report is None or report.get("patient_id") != patient_id:
        return None
    patient = await store.get_patient(patient_id)
    if patient is None or patient.get("create_by") != user_id:
        return None
    data = {
        column: patch[key]
        for key, column in CORRECTABLE_KEYS.items()
        if key in patch
    }
    # visit_date 是 Date 列：非法日期直接丢弃，不让脏值进库
    if "visitDate" in patch:
        try:
            data["visit_date"] = datetime.strptime(
                str(patch["visitDate"]), "%Y-%m-%d"
            ).date()
        except (ValueError, TypeError):
            pass
    if not data:
        return report
    await store.update_report(report_id, data, user_id=user_id)
    updated = await store.get_report(report_id)
    if updated is not None:
        # get_report 不 join patients，补齐 patient_name 与列表视图一致
        patient = await store.get_patient(patient_id)
        updated["patient_name"] = (patient or {}).get("name") or patient_id
    return updated
