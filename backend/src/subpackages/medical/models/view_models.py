"""医疗域 ViewModel 转换器 — DB snake_case 行 → 前端 camelCase dict。

集中管理 8 个转换函数：
- patient_to_vm:       患者档案 → PatientProfile
- report_to_vm:        临床文档 → PatientReport
- queue_task_to_vm:    队列任务 → QueueTask
- consultation_to_vm:  咨询登记 → ConsultationEntry
- literature_to_vm:    文献 → LiteratureEntry
- guideline_to_vm:     指南 → GuidelineEntry
- drug_to_vm:          药品 → DrugEntry
- drug_option_to_vm:   药品选项 → DrugOption
"""
from __future__ import annotations

import json
from datetime import datetime, date
from typing import Any


def _coerce_json(value: Any, default: Any) -> Any:
    """把 JSONB 列安全地规范成 dict/list。

    DB 中这些列可能以两种形态存在：(1) 正常 JSONB → psycopg 已解析为 dict/list；
    (2) 曾被字符串字面量写入（如 default="{}"）→ 读回是 JSON 字符串。
    前端 PatientProfile 始终声明为解析后的结构，故在此统一归一，避免 .map/.length 在字符串上抛错。
    """
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError, TypeError):
            return default
        return parsed if isinstance(parsed, (dict, list)) else default
    return default


def patient_to_vm(row: dict[str, Any]) -> dict[str, Any]:
    """将 DB 行转为 camelCase 患者视图模型。"""
    birth_date = row.get("birth_date", "")
    age = 0
    if birth_date:
        try:
            bd = datetime.strptime(str(birth_date), "%Y-%m-%d").date()
            today = date.today()
            age = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
        except (ValueError, TypeError):
            pass

    return {
        "id": row.get("id", ""),
        "name": row.get("name", ""),
        "reportCount": int(row.get("report_count") or 0),
        "age": age,
        "gender": row.get("gender", ""),
        "idType": row.get("id_type", ""),
        "idNumber": row.get("id_number_masked", ""),
        "phone": row.get("phone_masked", ""),
        "department": row.get("department", ""),
        "diagnosis": row.get("primary_diagnosis", ""),
        "stage": row.get("tnm_stage", "") or row.get("clinical_stage", ""),
        "status": row.get("treatment_status", ""),
        "cycle": row.get("current_cycle", ""),
        "risk": row.get("risk_level", ""),
        "vitals": _coerce_json(row.get("vitals_json"), {}),
        "biomarkers": _coerce_json(row.get("biomarkers_json"), {}),
        "allergies": _coerce_json(row.get("allergies_json"), []),
        "history": _coerce_json(row.get("history_json"), []),
        "medications": _coerce_json(row.get("medications_json"), []),
        "labs": _coerce_json(row.get("labs_json"), []),
        # timeline 由 service 组装在 patient 字典上，统一在此输出（前端 PatientProfile 需要）
        "timeline": row.get("timeline") or [],
    }


def report_to_vm(row: dict[str, Any]) -> dict[str, Any]:
    """将 DB 行转为 camelCase 临床文档视图模型。"""
    report_type = row.get("report_type", "outpatient")
    file_type = row.get("file_type", "text")
    icon_map = {"image": "image", "audio": "audio"}
    icon = icon_map.get(file_type, "text")

    visit = row.get("visit_date")
    date_str = ""
    if visit:
        if isinstance(visit, (datetime, date)):
            date_str = visit.strftime("%Y-%m-%d")
        else:
            date_str = str(visit)

    status = row.get("status", "排队中")
    state = "已解析" if status == "已解析" else "解析中"

    return {
        "id": row.get("id", ""),
        "patientId": row.get("patient_id", "") or "",
        # 已归档取档案姓名；尚未归档时退到 AI 识别出的姓名，
        # 待整理队列要靠它告诉医生「这份大概是张三的」
        "patientName": (
            row.get("patient_name")
            or row.get("patient_name_raw")
            or row.get("patient_id")
            or ""
        ),
        "matchReason": row.get("match_reason", "") or "",
        # 歧义归属时的同名候选患者（list_inbox 实时算出，.assign 复用抽屉也读它）
        "candidates": row.get("candidates") or [],
        "reportType": report_type,
        "name": row.get("file_name", ""),
        "date": date_str,
        "state": state,
        "icon": icon,
        "fileName": row.get("file_name", ""),
        "filePath": row.get("file_path", ""),
        "mimeType": row.get("mime_type", ""),
        "attachments": _coerce_json(row.get("attachments_json"), []),
        "hospitalName": row.get("hospital_name", ""),
        "doctorName": row.get("doctor_name", ""),
        "status": status,
        "progress": row.get("progress", 0),
        "errorMessage": row.get("error_message", "") or "",
        "rawText": row.get("raw_text", ""),
        "visitDate": visit.isoformat() if isinstance(visit, (datetime, date)) else str(visit or ""),
        "department": row.get("department", ""),
        "diagnosis": row.get("diagnosis", ""),
        "narrative": _coerce_json(row.get("narrative_json"), {}),
        "labs": _coerce_json(row.get("labs_json"), []),
        "medications": _coerce_json(row.get("medications_json"), []),
        "tags": _coerce_json(row.get("tags_json"), []),
        "summary": row.get("summary", ""),
    }


def queue_task_to_vm(row: dict[str, Any]) -> dict[str, Any]:
    """将 DB 行转为队列任务视图模型。"""
    file_name = row.get("file_name", "")
    ext = file_name.rsplit(".", 1)[-1].upper() if "." in file_name else "FILE"

    # elapsed 计算
    status = row.get("status", "排队中")
    started_at = row.get("started_at")
    finished_at = row.get("finished_at")
    now = datetime.utcnow()

    if status == "已解析" and started_at and finished_at:
        elapsed_seconds = int((finished_at - started_at).total_seconds())
    elif started_at and status not in ("排队中", "失败"):
        elapsed_seconds = int((now - started_at).total_seconds())
    else:
        elapsed_seconds = 0

    m, s = divmod(elapsed_seconds, 60)
    h, m = divmod(m, 60)
    elapsed = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

    return {
        "id": row.get("id", ""),
        "file": file_name,
        "type": ext,
        "stage": status,
        "progress": row.get("progress", 0),
        "elapsed": elapsed,
    }


def literature_to_vm(row: dict[str, Any]) -> dict[str, Any]:
    """文献 DB 行 → 前端 LiteratureEntry (camelCase)。"""
    return {
        "id": row.get("id", ""),
        "title": row.get("title", ""),
        "authors": row.get("authors", ""),
        "journal": row.get("journal", ""),
        "year": row.get("year") or 0,
        "category": row.get("category", ""),
        "abstract": row.get("abstract", ""),
        "keywords": row.get("keywords_json", []),
        "citations": row.get("citations") or 0,
        "doi": row.get("doi", ""),
    }


def guideline_to_vm(row: dict[str, Any]) -> dict[str, Any]:
    """指南 DB 行 → 前端 GuidelineEntry (camelCase)。"""
    return {
        "id": row.get("id", ""),
        "title": row.get("title", ""),
        "org": row.get("org", ""),
        "year": row.get("year") or 0,
        "cat": row.get("category", ""),
        "ver": row.get("version", ""),
        "abstract": row.get("abstract", ""),
        "points": row.get("points_json", []),
    }


def drug_to_vm(row: dict[str, Any]) -> dict[str, Any]:
    """药品 DB 行 → 前端 DrugEntry (camelCase)。"""
    return {
        "id": row.get("id", ""),
        "name": row.get("name", ""),
        "generic": row.get("generic_name", ""),
        "cat": row.get("category", ""),
        "dosage": row.get("dosage", ""),
        "indications": row.get("indications_json", []),
        "mechanism": row.get("mechanism", ""),
        "sideEffects": row.get("side_effects_json", []),
        "contraindications": row.get("contraindications_json", []),
        "interactions": row.get("interactions_json", []),
        "pregnancy": row.get("pregnancy_grade", ""),
        "essential": row.get("is_essential") or False,
    }


def drug_option_to_vm(row: dict[str, Any]) -> dict[str, Any]:
    """药品选项 → 前端 DrugOption (camelCase)。"""
    return {
        "id": row.get("id", ""),
        "name": row.get("name", ""),
        "cat": row.get("cat") or row.get("category", ""),
    }


def consultation_to_vm(row: dict[str, Any]) -> dict[str, Any]:
    """咨询登记 DB 行 → 前端 ConsultationEntry (camelCase)。"""
    created_at = row.get("created_at")
    return {
        "id": row.get("id", ""),
        "sessionId": row.get("session_id", ""),
        "patientId": row.get("patient_id", ""),
        "doctorId": row.get("doctor_id", ""),
        "title": row.get("title", ""),
        "createdAt": created_at.isoformat() if isinstance(created_at, datetime) else str(created_at or ""),
    }
