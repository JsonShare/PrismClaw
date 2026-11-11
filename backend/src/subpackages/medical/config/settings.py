"""医疗域配置常量 — 由各层引用，单一真相源。

分组：
- ADVISORY_LOCK_KEYS: DDL Advisory Lock 键区间 (742200-742206)
- MEDICAL_TOOL_PREFIXES: 医疗域工具名称前缀（PHI 脱敏钩子用）
"""

# ── Advisory Lock Key 区间 ──────────────────────────────────
# 与 persistence/schema.py DDL 执行一一对应

ADVISORY_LOCK_KEYS: dict[str, int] = {
    "patients": 742200,
    "patient_reports": 742201,
    "consultations": 742205,
    "kb_literatures": 742202,
    "kb_guidelines": 742203,
    "kb_drugs": 742204,
    "briefs": 742206,
}

# ── 医疗域工具名称前缀 ──────────────────────────────────────
# PHI 脱敏钩子仅对这些工具生效
# 注意：read_patient_reports / read_raw_report / read_patient_profile
# 不能加入此表，否则工具返回的内容落库前会被改坏

MEDICAL_TOOL_PREFIXES: tuple[str, ...] = (
    "smart_medical_record",
    "medical_translate",
    "search_guideline",
    "search_drug",
    "search_literature",
)
