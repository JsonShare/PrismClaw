"""研究简报路由 — 列表、详情、创建、重新分析、删除。

端点：
- GET /             分页列表（按 patient_id + create_by 过滤）
- GET /{brief_id}   简报详情（含 sections + citations）
- POST /            手动创建简报（指定 patient_id + report_ids）
- POST /{brief_id}/reanalyze  重新分析（version+1）
- DELETE /{brief_id}  逻辑删除

全部带 Depends(get_current_user) 鉴权 + create_by 归属校验。
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import (
    APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request,
)
from pydantic import BaseModel

from prismclaw.app.auth.auth import get_current_user

from ..integration.llm_access import get_medical_llm, get_medical_vision
from ..models.enums import BriefStatus
from ..persistence.helpers import get_brief_store, get_kb_store, get_medical_store
from ..services.analysis_pipeline import run_analysis_pipeline

router = APIRouter(tags=["medical-brief"])


# ── 请求体 ────────────────────────────────────────────────


class CreateBriefRequest(BaseModel):
    patient_id: str
    report_ids: list[str] | None = None  # 空=取全部已解析报告


# ── 辅助 ────────────────────────────────────────────────────


def _check_brief_owner(brief: dict[str, Any], user_id: str) -> None:
    """校验简报归属。不属于当前用户则抛 403。"""
    if brief.get("create_by") != user_id:
        raise HTTPException(status_code=403, detail="无权访问该简报")


def _brief_to_vm(brief: dict[str, Any]) -> dict[str, Any]:
    """简报行→前端视图模型。"""
    return {
        "id": brief.get("id", ""),
        "patientId": brief.get("patient_id", ""),
        "reportIds": brief.get("report_ids_json", []),
        "status": brief.get("status", ""),
        "version": brief.get("version", 1),
        "errorMessage": brief.get("error_message", ""),
        "startedAt": _iso(brief.get("started_at")),
        "finishedAt": _iso(brief.get("finished_at")),
        "createAt": _iso(brief.get("create_at")),
    }


def _brief_detail_to_vm(brief: dict[str, Any]) -> dict[str, Any]:
    """简报详情→前端视图模型（含 sections + citations + audit）。"""
    vm = _brief_to_vm(brief)
    vm["directorPlan"] = brief.get("director_plan_json", {})
    vm["sections"] = brief.get("sections_json", [])
    vm["citations"] = brief.get("citations_json", [])
    vm["auditResult"] = brief.get("audit_result_json", {})
    return vm


def _iso(value: Any) -> str:
    """datetime → ISO 字符串。"""
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


# ── 端点 ────────────────────────────────────────────────────


@router.get("/")
async def list_briefs(
    patient_id: str = Query("", description="按患者过滤"),
    status: str = Query("", description="按状态过滤"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    http_request: Request = None,
    user_id: str = Depends(get_current_user),
) -> dict:
    """简报分页列表（仅当前用户创建的简报）。"""
    brief_store = get_brief_store(http_request)
    items, total = await brief_store.list_briefs(
        patient_id=patient_id, status=status, create_by=user_id,
        page=page, page_size=pageSize,
    )
    return {
        "items": [_brief_to_vm(b) for b in items],
        "total": total,
        "page": page,
        "pageSize": pageSize,
    }


@router.get("/{brief_id}")
async def get_brief(
    brief_id: str,
    http_request: Request = None,
    user_id: str = Depends(get_current_user),
) -> dict:
    """简报详情（含 sections + citations + audit_result）。"""
    brief_store = get_brief_store(http_request)
    brief = await brief_store.get_brief(brief_id)
    if brief is None:
        raise HTTPException(status_code=404, detail="简报不存在")
    _check_brief_owner(brief, user_id)
    return _brief_detail_to_vm(brief)


@router.post("/")
async def create_brief(
    body: CreateBriefRequest,
    background_tasks: BackgroundTasks,
    http_request: Request = None,
    user_id: str = Depends(get_current_user),
) -> dict:
    """手动创建简报并触发分析。

    report_ids 为空时自动取该患者全部已解析但未分析的报告。
    """
    brief_store = get_brief_store(http_request)
    store = get_medical_store(http_request)
    kb_store = get_kb_store(http_request)

    # 校验患者归属
    patient = await store.get_patient(body.patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="患者不存在")
    if patient.get("create_by") != user_id:
        raise HTTPException(status_code=403, detail="无权操作该患者")

    # 确定报告列表
    report_ids = body.report_ids
    if not report_ids:
        report_ids = await brief_store.list_unanalyzed_reports(body.patient_id)
    if not report_ids:
        raise HTTPException(status_code=400, detail="该患者没有待分析的已解析报告")

    # 创建简报
    brief_id = str(uuid.uuid4())
    await brief_store.create_brief(brief_id, body.patient_id, report_ids, user_id=user_id)

    # 后台触发分析
    llm = get_medical_llm(http_request)
    vision = get_medical_vision(http_request)
    background_tasks.add_task(
        run_analysis_pipeline, store, brief_store, kb_store, brief_id,
        llm=llm, vision=vision,
    )

    # 返回完整 VM（与 _brief_to_vm 对齐）
    brief = await brief_store.get_brief(brief_id)
    return _brief_to_vm(brief) if brief else {"id": brief_id, "status": "分析中"}


@router.post("/{brief_id}/reanalyze")
async def reanalyze_brief(
    brief_id: str,
    background_tasks: BackgroundTasks,
    http_request: Request = None,
    user_id: str = Depends(get_current_user),
) -> dict:
    """重新分析（version+1）。检查当前状态不是「分析中」（防重入）。"""
    brief_store = get_brief_store(http_request)
    store = get_medical_store(http_request)
    kb_store = get_kb_store(http_request)

    brief = await brief_store.get_brief(brief_id)
    if brief is None:
        raise HTTPException(status_code=404, detail="简报不存在")
    _check_brief_owner(brief, user_id)
    if brief.get("status") == BriefStatus.ANALYZING.value:
        raise HTTPException(status_code=409, detail="简报正在分析中，请稍后再试")

    new_version = await brief_store.increment_version(brief_id, user_id=user_id)

    # 后台触发分析
    llm = get_medical_llm(http_request)
    vision = get_medical_vision(http_request)
    background_tasks.add_task(
        run_analysis_pipeline, store, brief_store, kb_store, brief_id,
        llm=llm, vision=vision,
    )

    # 返回完整 VM（与 create_brief 对齐）
    brief = await brief_store.get_brief(brief_id)
    return _brief_to_vm(brief) if brief else {"id": brief_id, "version": new_version, "status": "分析中"}


@router.delete("/{brief_id}")
async def delete_brief(
    brief_id: str,
    http_request: Request = None,
    user_id: str = Depends(get_current_user),
) -> dict:
    """逻辑删除简报。"""
    brief_store = get_brief_store(http_request)
    brief = await brief_store.get_brief(brief_id)
    if brief is None:
        raise HTTPException(status_code=404, detail="简报不存在或已删除")
    _check_brief_owner(brief, user_id)
    deleted = await brief_store.delete_brief(brief_id, user_id=user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="简报不存在或已删除")
    return {"id": brief_id, "deleted": True}
