"""患者管理路由 — 患者列表、详情（含派生时间线）、报告卡、统计。

端点：
- GET /              分页列表（按 create_by 行级过滤），返回 PatientProfile[]
- GET /stats         统计条四项指标
- GET /{patientId}   详情（含 timeline）
- GET /{patientId}/reports  报告解析卡列表
- PATCH /{patientId}/reports/{reportId}
                医生修正 AI 摘要 / AI 建议 / 报告原文

全部带 Depends(get_current_user) 鉴权。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Query

from prismclaw.app.auth.auth import get_current_user

from ..models.view_models import patient_to_vm, report_to_vm
from ..persistence.helpers import get_medical_store
from ..services.patient_service import (
    list_patients as _list_patients,
    get_patient_detail,
    get_patient_stats,
    list_patient_reports as _list_patient_reports,
    correct_report as _correct_report,
    create_patient as _create_patient,
)

router = APIRouter(tags=["medical-patients"])


@router.post("/")
async def create_patient(
    http_request: Request,
    payload: dict[str, Any] = Body(..., description="name 必填；idType / idNumber 可选"),
    user_id: str = Depends(get_current_user),
) -> dict:
    """新建患者档案。仅写入 create_by = 当前用户（行级归属）。

    name 为空直接 400；idType / idNumber 可选，传入则脱敏后入库。
    返回新建患者完整视图模型（idNumber 为脱敏值）。
    """
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="姓名不能为空")
    store = get_medical_store(http_request)
    new_id = await _create_patient(
        store,
        name=name,
        id_type=payload.get("idType") or "",
        id_number=payload.get("idNumber") or "",
        user_id=user_id,
    )
    patient = await get_patient_detail(store, new_id, user_id=user_id)
    if patient is None:
        raise HTTPException(status_code=500, detail="患者创建失败")
    return patient_to_vm(patient)


@router.get("/")
async def list_patients(
    keyword: str = Query("", description="搜索关键词"),
    gender: str = Query("", description="性别筛选"),
    risk: str = Query("", description="风险等级筛选"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(8, ge=1, le=100),
    http_request: Request = None,
    user_id: str = Depends(get_current_user),
) -> dict:
    """患者分页列表（按 create_by = 当前用户 过滤）。"""
    store = get_medical_store(http_request)
    result = await _list_patients(
        store, keyword=keyword, page=page, page_size=pageSize, create_by=user_id,
    )
    result["items"] = [patient_to_vm(p) for p in result["items"]]
    return result


@router.get("/stats")
async def get_stats(
    http_request: Request,
    user_id: str = Depends(get_current_user),
) -> dict:
    """统计条四项指标（仅当前用户的患者）。"""
    store = get_medical_store(http_request)
    return await get_patient_stats(store, create_by=user_id)


@router.get("/{patient_id}")
async def get_patient(
    patient_id: str,
    http_request: Request,
    user_id: str = Depends(get_current_user),
) -> dict:
    """患者详情（含派生时间线）。仅可查看 create_by = 当前患者的患者。"""
    store = get_medical_store(http_request)
    patient = await get_patient_detail(store, patient_id, user_id=user_id)
    if patient is None:
        raise HTTPException(status_code=403, detail="无权访问该患者")
    return patient_to_vm(patient)


@router.get("/{patient_id}/reports")
async def list_patient_reports(
    patient_id: str,
    http_request: Request,
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    user_id: str = Depends(get_current_user),
) -> dict:
    """患者报告解析卡列表（服务端分页，仅当前用户的患者）。"""
    store = get_medical_store(http_request)
    result = await _list_patient_reports(
        store, patient_id, page=page, page_size=pageSize, user_id=user_id,
    )
    if result is None:
        raise HTTPException(status_code=403, detail="无权访问该患者")
    result["items"] = [report_to_vm(r) for r in result["items"]]
    return result


@router.patch("/{patient_id}/reports/{report_id}")
async def correct_patient_report(
    patient_id: str,
    report_id: str,
    http_request: Request,
    patch: dict[str, Any] = Body(..., description="可修正字段：narrative / summary / rawText"),
    user_id: str = Depends(get_current_user),
) -> dict:
    """医生修正报告内容。仅可改 create_by = 当前用户的患者名下的报告。"""
    store = get_medical_store(http_request)
    updated = await _correct_report(
        store, patient_id, report_id, patch, user_id=user_id,
    )
    if updated is None:
        raise HTTPException(status_code=403, detail="无权访问该患者")
    return report_to_vm(updated)
