"""病历解析流水线编排。

四阶段状态机（对应 domain/parsing 的三个纯函数模块）：

    排队中 → OCR识别(20) → 结构化提取(40) → 结构化提取(70) → 归属(90) → 已解析(100)

设计取舍：
- 抽不出原文 → 直接失败。留一条空原文的「已解析」记录比明确失败更糟，
  医生会以为系统读过了。
- 元数据结构化失败 → 降级为空，不阻断。文档类型/医院这些是锦上添花，
  但患者姓名抽不到就归不了档，所以姓名缺失时记录会停在待整理。
- 临床结构化失败 → 也降级为空，只保住原文。原文在，医生还能自己看。
- LLM 完全不可用（抛异常）→ 整条失败并记 error_message，可人工重解析。

进度写入用 update_report，user_id 一律 "system"：后台任务没有用户上下文。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime
from typing import Any

from ..domain.parsing import extract as extract_mod
from ..domain.parsing.match import MATCH_REASON_TEXT, match_patient
from ..domain.parsing.prompts import (
    METADATA_SYSTEM,
    METADATA_TOOL,
    STRUCTURE_SYSTEM,
    STRUCTURE_TOOL,
    build_metadata_prompt,
    build_structure_prompt,
    normalize_metadata,
    normalize_structure,
    to_visit_date,
)
from ..integration.llm_access import complete_json, ocr_image
from ..models.enums import ReportStatus
from ..persistence.medical_store import MedicalStore

logger = logging.getLogger(__name__)

# 失败原因写库前截断，避免异常堆栈撑爆字段
ERROR_LIMIT = 500
# 送进 prompt 的原文上限（extract 里也截了一次，这里再兜一层）
PROMPT_TEXT_LIMIT = 24_000


async def run_parse_pipeline(
    store: MedicalStore,
    report_id: str,
    *,
    llm: Any | None = None,
    vision: Any | None = None,
    ocr_sem: asyncio.Semaphore | None = None,
    llm_sem: asyncio.Semaphore | None = None,
) -> None:
    """推进单份病历到终态。任何未捕获异常都会被记成「失败」。

    llm / vision 必须在调用前从请求上下文取出后传入——后台任务里
    request 已经失效，不能指望在里面再访问 app.state。
    ocr_sem / llm_sem 为 None 时行为不变（向后兼容）。
    """
    try:
        await _run(store, report_id, llm=llm, vision=vision,
                   ocr_sem=ocr_sem, llm_sem=llm_sem)
    except Exception as exc:  # noqa: BLE001
        logger.exception("病历解析失败 report_id=%s", report_id)
        await _fail(store, report_id, f"{type(exc).__name__}: {exc}")


async def _run(
    store: MedicalStore,
    report_id: str,
    *,
    llm: Any | None,
    vision: Any | None,
    ocr_sem: asyncio.Semaphore | None,
    llm_sem: asyncio.Semaphore | None,
) -> None:
    row = await store.get_report(report_id)
    if row is None:
        logger.warning("报告不存在，跳过解析: %s", report_id)
        return

    # ── 阶段 1：文本抽取（带 OCR 页级进度 + Semaphore）
    await _advance(store, report_id, ReportStatus.OCR.value, 20, start=True)
    filename = str(row.get("file_name") or "")
    data = _read_bytes(row)
    ocr = (lambda b, m: ocr_image(vision, b, m)) if vision is not None else None

    # OCR Semaphore：默认 Semaphore(3)
    _ocr_sem = ocr_sem or asyncio.Semaphore(3)

    # OCR 页级进度回调：映射到 20-40 区间
    async def _on_page_done(page: int, total: int) -> None:
        ocr_progress = 20 + int(20 * page / total)
        await _advance(store, report_id, ReportStatus.OCR.value, ocr_progress)

    raw_text, err = await extract_mod.extract_text(
        data, filename, ocr=ocr, ocr_sem=_ocr_sem, on_page_done=_on_page_done,
    )
    if err:
        await _fail(store, report_id, err)
        return
    await store.update_report(report_id, {"raw_text": raw_text}, user_id="system")

    prompt_text = raw_text[:PROMPT_TEXT_LIMIT]

    # LLM guarded 调用：Semaphore 保护，None 时直接调用
    async def _guarded_json(*args, **kwargs):
        if llm_sem:
            async with llm_sem:
                return await complete_json(*args, **kwargs)
        return await complete_json(*args, **kwargs)

    # ── 阶段 2：元数据与患者身份（失败降级为空，不阻断）
    await _advance(store, report_id, ReportStatus.EXTRACT.value, 40)
    meta_raw = await _guarded_json(
        llm,
        system=METADATA_SYSTEM,
        prompt=build_metadata_prompt(prompt_text),
        tool=METADATA_TOOL,
        tool_name="report_metadata",
    )
    meta = normalize_metadata(meta_raw or {})
    logger.info(
        "报告 %s 元数据：type=%s date=%s patient=%s",
        report_id, meta["report_type"], meta["visit_date"], meta["patient_name"],
    )

    # ── 阶段 3：临床结构化（失败降级为空，保住原文）
    await _advance(store, report_id, ReportStatus.EXTRACT.value, 70)
    struct_raw = await _guarded_json(
        llm,
        system=STRUCTURE_SYSTEM,
        prompt=build_structure_prompt(prompt_text, meta["title"]),
        tool=STRUCTURE_TOOL,
        tool_name="report_structure",
    )
    struct = normalize_structure(struct_raw or {})

    # ── 阶段 4：患者归属
    await _advance(store, report_id, ReportStatus.EXTRACT.value, 90)
    candidates = await store.list_patient_candidates(
        create_by=str(row.get("create_by") or "")
    )
    # 患者姓名：LLM 提取优先，为空时从文件名补充
    patient_name = meta["patient_name"]
    if not patient_name:
        filename = str(row.get("file_name") or "")
        patient_name = _extract_name_from_filename(filename)
        if patient_name:
            logger.info("报告 %s 从文件名补充患者姓名：%s", report_id, patient_name)
    matched = match_patient(
        candidates,
        name=patient_name,
        gender=meta["patient_gender"],
        age=meta["patient_age"],
    )
    logger.info(
        "报告 %s 归属判定：%s (%s)", report_id, matched["reason"],
        MATCH_REASON_TEXT.get(matched["reason"], ""),
    )

    # 页内上传已显式指定归属：匹配只用来「对账」，不改写归属。
    # 反过来（用识别结果覆盖医生选择）会把报告悄悄挪到另一个人名下，
    # 这正是要杜绝的静默错误；对不上就升级成 bound_conflict 交人工核验。
    bound_patient_id = str(row.get("patient_id") or "")
    match_reason = matched["reason"]
    if bound_patient_id:
        if matched["patient_id"] and matched["patient_id"] != bound_patient_id:
            match_reason = "bound_conflict"
        elif not matched["patient_id"]:
            match_reason = "bound_anonymous"

    patch: dict[str, Any] = {
        "report_type": meta["report_type"],
        "visit_date": to_visit_date(meta["visit_date"]),
        "hospital_name": meta["hospital_name"],
        "department": meta["department"],
        "doctor_name": meta["doctor_name"],
        "diagnosis": meta["diagnosis"] or str(row.get("diagnosis") or ""),
        "narrative_json": struct["narrative"],
        "labs_json": struct["labs"],
        "medications_json": struct["medications"],
        "tags_json": struct["tags"],
        "summary": struct["summary"],
        "patient_name_raw": patient_name,
        "match_reason": match_reason,
    }
    if not bound_patient_id and matched["patient_id"]:
        patch["patient_id"] = matched["patient_id"]

    await _finish(store, report_id, patch)


# ══════════════════════════════════════════════════════════════
# 内部辅助
# ══════════════════════════════════════════════════════════════

# 从文件名提取可能的中文姓名：2-4个汉字，后面跟分隔符（日期/括号/下划线/点）
_FILENAME_NAME_RE = re.compile(r"^([\u4e00-\u9fa5]{2,4})(?:\s*\d{4}|\s*[（(]|_|\.)")


def _extract_name_from_filename(filename: str) -> str:
    """从文件名中尝试提取患者姓名。

    匹配模式：姓名 + 年份/括号/下划线/点，如：
    - 张三2024血常规.pdf → 张三
    - 李四（门诊记录）.docx → 李四
    """
    base = filename.rsplit(".", 1)[0]  # 去扩展名
    m = _FILENAME_NAME_RE.match(base)
    return m.group(1).strip() if m else ""


def _read_bytes(row: dict[str, Any]) -> bytes:
    """取原始文件字节。落盘路径优先，其次附件里的路径。"""
    path = str(row.get("file_path") or "")
    if not path:
        for att in row.get("attachments_json") or []:
            if isinstance(att, dict) and att.get("path"):
                path = str(att["path"])
                break
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"原始文件缺失: {path or '未记录路径'}")
    with open(path, "rb") as fh:
        return fh.read()


async def _advance(
    store: MedicalStore,
    report_id: str,
    status: str,
    progress: int,
    *,
    start: bool = False,
) -> None:
    data: dict[str, Any] = {"status": status, "progress": progress}
    if start:
        data["started_at"] = datetime.utcnow()
    await store.update_report(report_id, data, user_id="system")


async def _fail(store: MedicalStore, report_id: str, reason: str) -> None:
    await store.update_report(
        report_id,
        {
            "status": ReportStatus.FAILED.value,
            "progress": 100,
            "error_message": reason[:ERROR_LIMIT],
            "finished_at": datetime.utcnow(),
        },
        user_id="system",
    )


async def _finish(store: MedicalStore, report_id: str, patch: dict[str, Any]) -> None:
    patch = dict(patch)
    patch.update({
        "status": ReportStatus.PARSED.value,
        "progress": 100,
        "error_message": "",
        "retry_count": 0,
        "finished_at": datetime.utcnow(),
    })
    await store.update_report(report_id, patch, user_id="system")
