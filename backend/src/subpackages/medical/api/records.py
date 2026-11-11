"""病历/报告路由 — 病历页列表、整理队列、上传。

端点：
- GET /         分页列表（按 create_by = 当前用户 过滤），返回 PatientReport[]
- GET /inbox    「待整理」队列：尚未归属患者的文档
- GET /queue    整理队列 + 页脚汇总
- POST /upload  multipart 上传，随即启动解析→分析链式流水线
- POST /{report_id}/reparse  重新解析（失败重试 / 换了模型后重跑）
- POST /{report_id}/assign   把文档指派给患者
- GET /{report_id}/attachments/{index}
              按附件下标取原始文件（PDF/图片），供时间线查看原始报告

全部带 Depends(get_current_user) 鉴权。

解析→分析对接（§6.3）：
上传/重解析的 BackgroundTask 不再只调 run_parse_pipeline，而是调
_run_parse_then_maybe_analysis——解析成功且已归属患者后自动触发分析流水线。
分析失败不影响解析结果（报告已存库），用户可稍后在简报 API 手动重试。
"""
from __future__ import annotations

import asyncio
import logging
import os

from typing import Any

from fastapi import (
    APIRouter, Body, Depends, HTTPException, Request, Query, BackgroundTasks,
)
from fastapi.responses import FileResponse

from prismclaw.app.auth.auth import get_current_user

from ..integration.llm_access import MEDICAL_LLM_SEMAPHORE, get_medical_llm, get_medical_vision
from ..models.enums import ReportStatus
from ..models.view_models import report_to_vm, queue_task_to_vm
from ..persistence.helpers import get_brief_store, get_kb_store, get_medical_store
from ..services.analysis_pipeline import trigger_analysis_for_patient
from ..services.parse_pipeline import run_parse_pipeline
from ..services.patient_service import get_patient_detail
from ..services.record_service import (
    list_records as _list_records,
    list_inbox as _list_inbox,
    assign_report as _assign_report,
    get_queue as _get_queue,
    create_reports_from_upload,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["medical-record"])


# ══════════════════════════════════════════════════════════════
# 解析→分析链式流水线
# ══════════════════════════════════════════════════════════════


async def _run_parse_then_maybe_analysis(
    store,
    brief_store,
    kb_store,
    report_id: str,
    *,
    user_id: str = "system",
    llm: Any | None = None,
    vision: Any | None = None,
) -> None:
    """先跑解析流水线，成功且已归属患者则自动触发分析。

    设计取舍（架构文档 §6.3）：
    - 不在 parse_pipeline._finish 里直接调分析——BackgroundTask 运行时
      request 已死，且分析是独立管线，不应与解析耦合。
    - 分析失败不阻塞解析结果：报告已存库，医生可稍后手动重试。
    - report_ids=None 让 trigger_analysis_for_patient 自动检测该患者
      所有未分析报告（包含本次刚解析的）。
    """
    # 1. 解析流水线——传入 Semaphore（内部已处理全部异常）
    await run_parse_pipeline(
        store, report_id, llm=llm, vision=vision,
        ocr_sem=asyncio.Semaphore(3),          # OCR 并发上限
        llm_sem=MEDICAL_LLM_SEMAPHORE,         # 全局 LLM 并发上限
    )

    # 2. 检查解析是否成功 + 是否已归属患者
    report = await store.get_report(report_id)
    if report is None:
        return
    if str(report.get("status") or "") != ReportStatus.PARSED.value:
        return  # 解析失败，不触发分析
    patient_id = str(report.get("patient_id") or "")
    if not patient_id:
        return  # 未归属患者（停在待整理队列），不触发分析

    # 3. 自动触发分析（异常不阻塞，记日志即可）
    try:
        await trigger_analysis_for_patient(
            store, brief_store, kb_store, patient_id,
            user_id=user_id, llm=llm, vision=vision,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "解析后自动分析失败 report_id=%s patient_id=%s", report_id, patient_id,
        )


@router.get("/")
async def list_records(
    keyword: str = Query("", description="搜索关键词（患者名/诊断/摘要/文件名）"),
    reportType: str = Query("", description="报告类型过滤（report_type 原始值）"),
    status: str = Query("", description="解析状态过滤（已解析/解析中/失败）"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(6, ge=1, le=100),
    http_request: Request = None,
    user_id: str = Depends(get_current_user),
) -> dict:
    """报告检索分页列表（按 create_by = 当前用户 过滤）。
    keyword + reportType + status 组合即 Phase 1 的「元数据 + 关键词」检索。
    """
    store = get_medical_store(http_request)
    items_raw, total = await _list_records(
        store, keyword=keyword, page=page, page_size=pageSize,
        create_by=user_id, report_type=reportType, status=status,
    )
    items = [report_to_vm(r) for r in items_raw]
    return {"items": items, "total": total, "page": page, "pageSize": pageSize}


@router.get("/inbox")
async def list_inbox(
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    http_request: Request = None,
    user_id: str = Depends(get_current_user),
) -> dict:
    """「待整理」队列：尚未归属到患者的文档（按 create_by 行级过滤）。

    AI 识别不出归属时由医生在此人工指派，避免上传的病历凭空消失。
    """
    store = get_medical_store(http_request)
    items_raw, total = await _list_inbox(
        store, page=page, page_size=pageSize, create_by=user_id,
    )
    return {
        "items": [report_to_vm(r) for r in items_raw],
        "total": total,
        "page": page,
        "pageSize": pageSize,
    }


@router.post("/{report_id}/assign")
async def assign_report(
    report_id: str,
    http_request: Request,
    background_tasks: BackgroundTasks,
    body: dict[str, Any] = Body(..., description="目标患者：{ patientId: string }"),
    user_id: str = Depends(get_current_user),
) -> dict:
    """把文档指派给患者（报告与患者均须属于当前用户）。

    指派成功后自动触发分析流水线——整理队列里的报告已解析完成，
    归属患者后即可启动分析。
    """
    store = get_medical_store(http_request)
    patient_id = str(body.get("patientId") or "")
    if not patient_id:
        raise HTTPException(status_code=400, detail="patientId 必填")
    updated, err = await _assign_report(
        store, report_id, patient_id, user_id=user_id,
    )
    if err:
        raise HTTPException(
            status_code={
                "not_found": 404,
                "not_owner": 403,
                "patient_not_found": 404,
                "patient_forbidden": 403,
            }[err],
            detail={
                "not_found": "报告不存在",
                "not_owner": "无权操作该报告",
                "patient_not_found": "患者不存在",
                "patient_forbidden": "无权访问该患者",
            }[err],
        )

    # 指派成功 → 后台触发分析（报告已解析，只需启动分析）
    report_status = str(updated.get("status") or "") if isinstance(updated, dict) else ""
    if report_status == ReportStatus.PARSED.value:
        try:
            from ..services.analysis_pipeline import trigger_analysis_for_patient as _trigger
            brief_store = get_brief_store(http_request)
            kb_store = get_kb_store(http_request)
            llm = get_medical_llm(http_request)
            vision = get_medical_vision(http_request)
            background_tasks.add_task(
                _trigger,
                store, brief_store, kb_store, patient_id,
                user_id=user_id, llm=llm, vision=vision,
            )
        except Exception:  # noqa: BLE001
            logger.exception("指派后触发分析失败 report_id=%s", report_id)

    return report_to_vm(updated)


@router.get("/{report_id}/attachments/{index}")
async def get_report_attachment(
    report_id: str,
    index: int,
    http_request: Request,
    user_id: str = Depends(get_current_user),
):
    """按附件下标返回原始文件（PDF / 图片）。

    安全：只接受 attachments_json 的数组下标，不接受外部传入的路径——
    结构决定了无法越权读文件，无需再做路径校验兜底。
    鉴权：按报告自身的 create_by 判定归属（不是患者档案的 create_by）。
    原因：上传流程不关联患者（patient_id 为 NULL），用患者归属会把
    「刚上传、尚未归入档案」的报告全部挡在 403 外；而报告只能由本人上传，
    create_by 是更直接且不更弱的归属信号。
    """
    store = get_medical_store(http_request)
    report = await store.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")

    if report.get("create_by") != user_id:
        raise HTTPException(status_code=403, detail="无权访问该报告")

    attachments = report.get("attachments_json") or []
    if not isinstance(attachments, list) or index < 0 or index >= len(attachments):
        raise HTTPException(status_code=404, detail="附件不存在")

    att = attachments[index]
    path = att.get("path") if isinstance(att, dict) else None
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="附件文件缺失")

    return FileResponse(
        path,
        media_type=att.get("mime") or "application/octet-stream",
        filename=os.path.basename(path),
    )


@router.get("/queue")
async def get_queue(
    http_request: Request,
    user_id: str = Depends(get_current_user),
) -> dict:
    """整理队列 + 页脚汇总（仅当前用户的报告）。"""
    store = get_medical_store(http_request)
    tasks_raw, summary = await _get_queue(store, create_by=user_id)
    tasks = [queue_task_to_vm(t) for t in tasks_raw]
    return {"tasks": tasks, "summary": summary}


@router.post("/upload", status_code=202)
async def upload_records(
    http_request: Request,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user),
) -> dict:
    """上传文件并创建临床文档（create_by = 当前用户），随即启动解析→分析链式流水线。

    模型客户端在这里取出后作为参数传进去：BackgroundTask 跑的时候
    request 已经结束，那时候再访问 app.state 拿不到东西。
    解析成功且归属患者后自动触发分析，分析失败不影响解析结果。
    """
    form = await http_request.form()
    files = form.getlist("files")
    if not files:
        return {"reportIds": [], "error": "未上传文件"}

    store = get_medical_store(http_request)
    brief_store = get_brief_store(http_request)
    kb_store = get_kb_store(http_request)
    llm = get_medical_llm(http_request)
    vision = get_medical_vision(http_request)

    # 页内上传：前端在某个患者页里上传时带上 patientId，归属就此确定。
    # 必须做行级归属校验 —— 否则传别人的 patientId 就能把文档塞进他人档案。
    patient_id = str(form.get("patientId") or "").strip() or None
    if patient_id is not None:
        owner = await get_patient_detail(store, patient_id, user_id=user_id)
        if owner is None:
            raise HTTPException(status_code=403, detail="无权向该患者上传报告")

    report_ids = await create_reports_from_upload(
        store, files, create_by=user_id, patient_id=patient_id,
    )

    for report_id in report_ids:
        background_tasks.add_task(
            _run_parse_then_maybe_analysis,
            store, brief_store, kb_store, report_id,
            user_id=user_id, llm=llm,
            vision=vision,
        )

    return {"reportIds": report_ids}


@router.post("/{report_id}/reparse", status_code=202)
async def reparse_record(
    report_id: str,
    http_request: Request,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user),
) -> dict:
    """重新解析一份文档（解析失败后重试，或换模型后重跑）。

    只受理属于自己的文档；已在解析中的不重复排队。
    重新解析成功后同样会触发分析流水线。
    """
    store = get_medical_store(http_request)
    report = await store.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="报告不存在")
    if report.get("create_by") != user_id:
        raise HTTPException(status_code=403, detail="无权操作该报告")
    current = str(report.get("status") or "")
    if current not in (ReportStatus.PARSED.value, ReportStatus.FAILED.value):
        raise HTTPException(status_code=409, detail="文档正在解析中")

    await store.update_report(
        report_id,
        {
            "status": ReportStatus.QUEUED.value,
            "progress": 0,
            "error_message": "",
            "retry_count": int(report.get("retry_count") or 0) + 1,
            "finished_at": None,
        },
        user_id=user_id,
    )
    llm = get_medical_llm(http_request)
    vision = get_medical_vision(http_request)
    background_tasks.add_task(
        _run_parse_then_maybe_analysis,
        get_medical_store(http_request),
        get_brief_store(http_request),
        get_kb_store(http_request),
        report_id,
        user_id=user_id,
        llm=llm,
        vision=vision,
    )
    return {"reportId": report_id, "status": ReportStatus.QUEUED.value}
