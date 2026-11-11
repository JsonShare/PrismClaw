"""医疗域 PHI 模式 — 由 MedicalBundle 在 on_build_runtime() 中注册到核心。

HIPAA Safe Harbor 方法中医疗特定的标识符模式：
  - 姓名（医疗上下文前缀：患者/病人/医生/Patient/Doctor）
  - MRN 病历号
  - 床号
  - 入院/出院/就诊日期
  - 执业证号 + 医疗编码前缀（MD-/RN-）
  - 医疗 URL（含 /patient/record/mrn/phi/health/medical/ 路径）
  - 医疗上下文 UUID
"""
from __future__ import annotations

import re

from ..._protocols import PhiPatternEntry


MEDICAL_PHI_PATTERNS: list[PhiPatternEntry] = [
    # 1. 姓名：医疗上下文前缀
    ("NAME_MEDICAL", re.compile(
        r'(?:患者|病人|医生|医师|护士|主治)[：:]\s*[\u4e00-\u9fff]{2,4}'
        r'|'
        r'(?:Patient|Doctor|Nurse)\s*[:：]\s*[A-Z][a-z]+\s+[A-Z][a-z]+',
        re.IGNORECASE
    ), "[PHI_REDACTED_NAME]"),

    # 2. MRN 病历号
    ("MRN", re.compile(
        r'\b(?:MRN|mrn|病历号|病案号|医疗记录号)[-\s:]?\d{4,12}\b'
    ), "[PHI_REDACTED_MRN]"),

    # 3. 床号
    ("BED", re.compile(
        r'(?:床号|病床号|Bed Number|Bed No)[：:]\s*\w{1,20}'
    ), "[PHI_REDACTED_BED]"),

    # 4. 医疗日期（入院/出院/就诊）
    ("MEDICAL_DATE", re.compile(
        r'(?:入院日期|出院日期|就诊日期|Admission Date|Discharge Date|Service Date|Visit Date)[：:]\s*'
        r'\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?'
    ), "[PHI_REDACTED_DATE]"),

    # 5. 执业证号 + 医疗编码前缀（MD-/RN-）
    ("MEDICAL_CERT", re.compile(
        r'(?:执业证号)[-\s:]?\d{4,20}'
        r'|'
        r'\b[A-Z]{2,4}[-]?\d{6,12}\b',
        re.IGNORECASE
    ), "[PHI_REDACTED_CERT]"),

    # 6. 医疗 URL
    ("MEDICAL_URL", re.compile(
        r'https?://[^\s<>"{}|\\^`\[\]]+/(?:patient|record|mrn|phi|health|medical)/[^\s<>"{}|\\^`\[\]]+',
        re.IGNORECASE
    ), "[PHI_REDACTED_URL]"),

    # 7. 医疗上下文 UUID
    ("MEDICAL_UUID", re.compile(
        r'(?:患者ID|病历ID|PHI|patient[_-]?id|record[_-]?id|mrn[_-]?id)[-\s:]*'
        r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b',
        re.IGNORECASE
    ), "[PHI_REDACTED_UUID]"),
]
