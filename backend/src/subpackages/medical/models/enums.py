"""医疗域枚举类型 — StrEnum 确保序列化值为字符串。

- ReportType: 文档类型（contract §2.1.1）
- ReportStatus: 文档生命周期状态（contract §2.3）
- BriefStatus: 研究简报生命周期状态
"""
from __future__ import annotations

from enum import StrEnum


class ReportType(StrEnum):
    """文档类型判别列（contract §2.1.1 枚举）。"""
    OUTPATIENT = "outpatient"
    INPATIENT = "inpatient"
    DISCHARGE = "discharge"
    SURGICAL = "surgical"
    PATHOLOGY = "pathology"
    IMAGING = "imaging"
    LAB = "lab"
    PRESCRIPTION = "prescription"
    CONSULTATION = "consultation"
    NURSING = "nursing"
    ANESTHESIA = "anesthesia"
    BLOOD_TRANSFUSION = "blood_transfusion"
    ADMISSION = "admission"
    TRANSFER = "transfer"
    EMERGENCY = "emergency"
    OTHER = "other"


class ReportStatus(StrEnum):
    """文档生命周期状态（contract §2.3）。"""
    QUEUED = "排队中"
    OCR = "OCR识别"
    EXTRACT = "结构化提取"
    TRANSCRIBE = "语音转写"
    PARSED = "已解析"
    FAILED = "失败"


class BriefStatus(StrEnum):
    """研究简报生命周期状态。"""
    ANALYZING = "分析中"
    PASSED = "已通过"
    NEEDS_FIX = "待修正"
    ABANDONED = "已废弃"
