"""病历/报告服务 — 报告 CRUD、队列、上传流水线。

从 routes/record.py 提取的业务逻辑。
"""
from __future__ import annotations

import uuid
from typing import Any

from ..models.enums import ReportStatus
from ..domain.parsing.match import normalize_name, age_from_birth
from ..persistence.medical_store import MedicalStore
from ..persistence.storage import save_upload_bytes


async def list_records(
    store: MedicalStore,
    *,
    keyword: str = "",
    page: int = 1,
    page_size: int = 6,
    create_by: str | None = None,
    report_type: str = "",
    status: str = "",
) -> tuple[list[dict[str, Any]], int]:
    """报告检索分页列表。create_by: 行级过滤。返回 (items, total)。
    keyword: 患者名/诊断/摘要/文件名；report_type/status: 元数据过滤。
    """
    return await store.list_reports(
        keyword=keyword, page=page, page_size=page_size,
        create_by=create_by, report_type=report_type, status=status,
    )


async def list_inbox(
    store: MedicalStore,
    *,
    page: int = 1,
    page_size: int = 20,
    create_by: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """「待整理」队列：尚未归属到任何患者的文档。

    AI 识别不出归属、或解析流水线还没跑完时，文档就停在这里，
    由医生人工指派到患者。create_by 行级过滤。

    歧义归属（同名多人，match_reason == 'ambiguous'）的文档，额外把同名候选患者
    一并带出（id/name/gender/age），前端指派抽屉可直接点选，不必重新全局搜索。
    candidates 不入库、不跨请求缓存——每次拉取按当前患者全集实时算，避免脏候选。
    """
    items, total = await store.list_reports(
        page=page,
        page_size=page_size,
        create_by=create_by,
        unassigned=True,
    )
    if create_by:
        pool = await store.list_patient_candidates(create_by=create_by)
        for r in items:
            if r.get("match_reason") != "ambiguous":
                continue
            cands = _ambiguous_candidates(r, pool)
            if cands:
                r["candidates"] = cands
    return items, total


def _ambiguous_candidates(
    report: dict[str, Any],
    patient_pool: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """从患者全集里挑出与本报告同名的患者，供指派抽屉点选。

    与 match.py 同名归一逻辑保持一致（只比姓名，性别/年龄仅作展示，不收窄）。
    命中不足 2 人说明已不歧义，返回空让前端走搜索兜底。
    """
    target = normalize_name(report.get("patient_name_raw") or "")
    if not target:
        return []
    same_name = [
        c for c in patient_pool
        if normalize_name(str(c.get("name") or "")) == target
    ]
    if len(same_name) < 2:
        return []
    out: list[dict[str, Any]] = []
    for c in same_name[:5]:
        birth = str(c.get("birth_date") or "")
        out.append({
            "id": str(c.get("id") or ""),
            "name": str(c.get("name") or ""),
            "gender": str(c.get("gender") or ""),
            "age": age_from_birth(birth) or 0,
        })
    return out


async def assign_report(
    store: MedicalStore,
    report_id: str,
    patient_id: str,
    *,
    user_id: str,
) -> tuple[dict[str, Any] | None, str]:
    """把文档指派给患者。返回 (报告原始行, 错误码)，错误码为 "" 表示成功。

    错误码：not_found / not_owner / patient_not_found / patient_forbidden
    """
    report = await store.get_report(report_id)
    if report is None:
        return None, "not_found"
    if report.get("create_by") != user_id:
        return None, "not_owner"

    patient = await store.get_patient(patient_id)
    if patient is None:
        return None, "patient_not_found"
    if patient.get("create_by") != user_id:
        return None, "patient_forbidden"

    await store.update_report(
        report_id, {"patient_id": patient_id}, user_id=user_id,
    )
    updated = await store.get_report(report_id)
    if updated is not None:
        updated["patient_name"] = patient.get("name") or patient_id
    return updated, ""


async def get_queue(
    store: MedicalStore, *, create_by: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """获取处理队列 + 汇总。create_by: 行级过滤。"""
    return await store.get_queue(create_by=create_by)


async def create_reports_from_upload(
    store: MedicalStore,
    files: list[Any],
    *,
    create_by: str,
    patient_id: str | None = None,
) -> list[str]:
    """上传文件并创建临床文档。create_by: 写入报告的 create_by 字段。返回 report_ids。

    patient_id: 页内上传（在某个患者页里上传）时显式指定归属。给定即视为
    「医生已确认归属」，解析流水线不得再用识别结果改写它（见 parse_pipeline）。
    缺省 None = 全局上传，仍走上传后自动匹配归属的老路径。
    """
    report_ids: list[str] = []

    for f in files:
        report_id = str(uuid.uuid4())
        filename = f.filename or "unknown"
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        file_type = "text"
        if ext in ("png", "jpg", "jpeg", "bmp", "gif", "tif", "tiff", "pdf"):
            file_type = "image"
        elif ext in ("mp3", "wav", "m4a", "aac"):
            file_type = "audio"

        mime_type = getattr(f, "content_type", "") or ""

        # 落盘：不存字节的话 file_path / attachments 恒为空，原件预览无从谈起
        file_path = ""
        attachments: list[dict[str, Any]] = []
        try:
            payload = await f.read()
        except Exception:
            payload = b""
        if payload:
            file_path = save_upload_bytes(create_by, report_id, filename, payload)
            attachments = [{"path": file_path, "mime": mime_type, "page_no": 1}]

        await store.create_report(
            report_id,
            patient_id,
            {
                "file_name": filename,
                "file_type": file_type,
                "file_path": file_path,
                "mime_type": mime_type,
                "attachments_json": attachments,
                "status": ReportStatus.QUEUED.value,
                "raw_text": "",
                "summary": "待 AI 解析",
            },
            user_id=create_by,
        )
        report_ids.append(report_id)

    return report_ids
