"""咨询会话登记路由 — 创建咨询、查询历史。

端点：
- POST /           创建咨询登记（前端先调核心建会话，再调此接口登记）
- GET /             查询当前医生的咨询列表
- GET /{session_id} 按 session_id 查询单条咨询（工具入口校验用）
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from prismclaw.app.auth.auth import get_current_user

from ..models.schemas import CreateConsultationRequest
from ..persistence.helpers import get_medical_store
from ..models.view_models import consultation_to_vm

router = APIRouter(tags=["medical-consultations"])


@router.post("/")
async def create_consultation(
    body: CreateConsultationRequest,
    http_request: Request,
    doctor_id: str = Depends(get_current_user),
) -> dict:
    """创建咨询登记。

    前端编排：先调核心 POST /sessions 建会话拿到 session_id，
    再调此接口登记。接口内部校验档案归属（create_by = 当前用户）。
    """
    store = get_medical_store(http_request)

    # 校验档案归属：该患者必须是当前医生创建的
    patient = await store.get_patient(body.patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="患者不存在")
    if patient.get("create_by") != doctor_id:
        raise HTTPException(status_code=403, detail="无权访问该患者档案")

    # 校验 session_id 未被登记（幂等保护）
    existing = await store.get_consultation_by_session(body.session_id)
    if existing is not None:
        return consultation_to_vm(existing)

    # 插入登记行
    import uuid
    consultation_id = str(uuid.uuid4())
    await store.insert_consultation(
        consultation_id=consultation_id,
        session_id=body.session_id,
        patient_id=body.patient_id,
        doctor_id=doctor_id,
        title=body.title,
    )

    consultation = await store.get_consultation_by_session(body.session_id)
    return consultation_to_vm(consultation)


@router.get("/")
async def list_consultations(
    patient_id: str | None = None,
    http_request: Request = None,
    doctor_id: str = Depends(get_current_user),
) -> dict:
    """查询当前医生的咨询列表，可选按患者过滤。"""
    store = get_medical_store(http_request)
    items = await store.list_consultations(doctor_id, patient_id=patient_id)
    return {"items": [consultation_to_vm(c) for c in items]}


@router.get("/{session_id}")
async def get_consultation(
    session_id: str,
    http_request: Request,
    doctor_id: str = Depends(get_current_user),
) -> dict:
    """按 session_id 查询单条咨询（工具入口校验 + 前端跳转验证用）。"""
    store = get_medical_store(http_request)
    consultation = await store.get_consultation_by_session(session_id)
    if consultation is None:
        raise HTTPException(status_code=404, detail="咨询不存在")
    if consultation.get("doctor_id") != doctor_id:
        raise HTTPException(status_code=403, detail="无权访问该咨询")
    return consultation_to_vm(consultation)
