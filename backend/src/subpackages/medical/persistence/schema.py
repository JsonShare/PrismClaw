"""医疗域 Schema 确保 — DDL 创建与 Advisory Lock 保护。

Advisory Lock Key 区间：742200-742206（由 config/settings.py 集中定义）
DDL 全部使用 IF NOT EXISTS，确保幂等执行。
表名统一带 pc_medical_ 前缀（跨域防碰撞）。

最终表集（7 表 + 1 视图）：
- pc_medical_patients            患者档案 + 画像
- pc_medical_patient_reports     统一临床文档表
- pc_medical_consultations       咨询会话登记
- pc_medical_kb_literatures / kb_guidelines / kb_drugs  知识库三表
- pc_medical_briefs              研究简报
- v_patient_timeline            派生视图
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ..config.settings import ADVISORY_LOCK_KEYS

logger = logging.getLogger(__name__)

# ── DDL 语句（IF NOT EXISTS 保证幂等）────────────────────────────

# 患者档案 + 画像
_DDL_PATIENTS = """
CREATE TABLE IF NOT EXISTS pc_medical_patients (
    id                VARCHAR(64)  PRIMARY KEY,
    name              VARCHAR(64)  DEFAULT '',
    gender            VARCHAR(8)   DEFAULT '',
    birth_date        VARCHAR(16)  DEFAULT '',
    blood_type        VARCHAR(8)   DEFAULT '',
    id_number_masked  VARCHAR(32)  DEFAULT '',
    phone_masked      VARCHAR(32)  DEFAULT '',
    id_number_hash    VARCHAR(64)  DEFAULT '',
    phone_hash        VARCHAR(64)  DEFAULT '',
    department        VARCHAR(64)  DEFAULT '',
    primary_diagnosis VARCHAR(256) DEFAULT '',
    tnm_stage         VARCHAR(32)  DEFAULT '',
    clinical_stage    VARCHAR(32)  DEFAULT '',
    ecog_score        INTEGER      DEFAULT 0,
    treatment_status  VARCHAR(32)  DEFAULT '',
    current_regimen   VARCHAR(128) DEFAULT '',
    current_cycle     VARCHAR(32)  DEFAULT '',
    risk_level        VARCHAR(16)  DEFAULT '',
    vitals_json       JSONB        DEFAULT '{}',
    biomarkers_json   JSONB        DEFAULT '{}',
    allergies_json    JSONB        DEFAULT '[]',
    history_json      JSONB        DEFAULT '[]',
    medications_json  JSONB        DEFAULT '[]',
    labs_json         JSONB        DEFAULT '[]',
    attending_doctor  VARCHAR(64)  DEFAULT '',
    tags_json         JSONB        DEFAULT '[]',
    create_at         TIMESTAMPTZ    DEFAULT NOW(),
    create_by         VARCHAR(64)  DEFAULT 'default',
    update_at         TIMESTAMPTZ    DEFAULT NOW(),
    update_by         VARCHAR(64)  DEFAULT 'default',
    is_deleted        SMALLINT     DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_pc_medical_patients_department      ON pc_medical_patients (department);
CREATE INDEX IF NOT EXISTS ix_pc_medical_patients_risk_level      ON pc_medical_patients (risk_level);
CREATE INDEX IF NOT EXISTS ix_pc_medical_patients_id_number_hash  ON pc_medical_patients (id_number_hash);
CREATE INDEX IF NOT EXISTS ix_pc_medical_patients_phone_hash      ON pc_medical_patients (phone_hash);
CREATE INDEX IF NOT EXISTS ix_pc_medical_patients_is_deleted      ON pc_medical_patients (is_deleted);
"""

# 统一临床文档表
_DDL_PATIENT_REPORTS = """
CREATE TABLE IF NOT EXISTS pc_medical_patient_reports (
    id               VARCHAR(64)  PRIMARY KEY,
    patient_id       VARCHAR(64),
    report_type      VARCHAR(32)  DEFAULT 'outpatient',
    file_name        VARCHAR(256) NOT NULL,
    file_type        VARCHAR(16)  DEFAULT 'text',
    file_path        TEXT         DEFAULT '',
    mime_type        VARCHAR(128) DEFAULT '',
    attachments_json JSONB        DEFAULT '[]',
    hospital_name    VARCHAR(128) DEFAULT '',
    doctor_name      VARCHAR(64)  DEFAULT '',
    status           VARCHAR(16)  DEFAULT '排队中',
    progress         SMALLINT     DEFAULT 0,
    retry_count      SMALLINT     DEFAULT 0,
    error_message    TEXT         DEFAULT '',
    raw_text         TEXT         DEFAULT '',
    visit_date       DATE,
    department       VARCHAR(64)  DEFAULT '',
    diagnosis        VARCHAR(256) DEFAULT '',
    narrative_json   JSONB        DEFAULT '[]',
    labs_json        JSONB        DEFAULT '[]',
    medications_json JSONB        DEFAULT '[]',
    tags_json        JSONB        DEFAULT '[]',
    summary          TEXT         DEFAULT '',
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ,
    create_at        TIMESTAMPTZ    DEFAULT NOW(),
    create_by        VARCHAR(64)  DEFAULT 'default',
    update_at        TIMESTAMPTZ    DEFAULT NOW(),
    update_by        VARCHAR(64)  DEFAULT 'default',
    is_deleted       SMALLINT     DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_pc_medical_patient_reports_patient_id   ON pc_medical_patient_reports (patient_id);
CREATE INDEX IF NOT EXISTS ix_pc_medical_patient_reports_status      ON pc_medical_patient_reports (status);
CREATE INDEX IF NOT EXISTS ix_pc_medical_patient_reports_report_type ON pc_medical_patient_reports (report_type);
CREATE INDEX IF NOT EXISTS ix_pc_medical_patient_reports_is_deleted  ON pc_medical_patient_reports (is_deleted);
"""

# 咨询会话登记
_DDL_CONSULTATIONS = """
CREATE TABLE IF NOT EXISTS pc_medical_consultations (
    id          VARCHAR(64)   PRIMARY KEY,
    session_id  VARCHAR(128)  NOT NULL UNIQUE,
    patient_id  VARCHAR(64)   NOT NULL,
    doctor_id   VARCHAR(64)   NOT NULL,
    title       VARCHAR(256)  DEFAULT '',
    created_at  TIMESTAMPTZ     DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_med_cons_patient
    ON pc_medical_consultations (patient_id, created_at DESC);
"""

# 知识库 — 文献
_DDL_KB_LITERATURES = """
CREATE TABLE IF NOT EXISTS pc_medical_kb_literatures (
    id            VARCHAR(64) PRIMARY KEY,
    title         VARCHAR(512) NOT NULL,
    authors       VARCHAR(512) DEFAULT '',
    journal       VARCHAR(128) DEFAULT '',
    year          INTEGER,
    category      VARCHAR(32)  DEFAULT '',
    abstract      TEXT         DEFAULT '',
    keywords_json JSONB        DEFAULT '[]',
    citations     INTEGER      DEFAULT 0,
    doi           VARCHAR(128) DEFAULT '',
    source        VARCHAR(32)  DEFAULT 'seed',
    create_at     TIMESTAMPTZ    DEFAULT NOW(),
    create_by     VARCHAR(64)  DEFAULT 'default',
    update_at     TIMESTAMPTZ    DEFAULT NOW(),
    update_by     VARCHAR(64)  DEFAULT 'default',
    is_deleted    SMALLINT     DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_pc_medical_kb_literatures_category   ON pc_medical_kb_literatures (category);
CREATE INDEX IF NOT EXISTS ix_pc_medical_kb_literatures_year       ON pc_medical_kb_literatures (year);
CREATE INDEX IF NOT EXISTS ix_pc_medical_kb_literatures_is_deleted ON pc_medical_kb_literatures (is_deleted);
"""

# 知识库 — 指南
_DDL_KB_GUIDELINES = """
CREATE TABLE IF NOT EXISTS pc_medical_kb_guidelines (
    id          VARCHAR(64) PRIMARY KEY,
    title       VARCHAR(512) NOT NULL,
    org         VARCHAR(32)  DEFAULT '',
    category    VARCHAR(32)  DEFAULT '',
    year        INTEGER,
    version     VARCHAR(32)  DEFAULT '',
    abstract    TEXT         DEFAULT '',
    points_json JSONB        DEFAULT '[]',
    source      VARCHAR(32)  DEFAULT 'seed',
    create_at   TIMESTAMPTZ    DEFAULT NOW(),
    create_by   VARCHAR(64)  DEFAULT 'default',
    update_at   TIMESTAMPTZ    DEFAULT NOW(),
    update_by   VARCHAR(64)  DEFAULT 'default',
    is_deleted  SMALLINT     DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_pc_medical_kb_guidelines_category   ON pc_medical_kb_guidelines (category);
CREATE INDEX IF NOT EXISTS ix_pc_medical_kb_guidelines_year       ON pc_medical_kb_guidelines (year);
CREATE INDEX IF NOT EXISTS ix_pc_medical_kb_guidelines_is_deleted ON pc_medical_kb_guidelines (is_deleted);
"""

# 知识库 — 药品
_DDL_KB_DRUGS = """
CREATE TABLE IF NOT EXISTS pc_medical_kb_drugs (
    id                   VARCHAR(64) PRIMARY KEY,
    name                 VARCHAR(128) NOT NULL,
    generic_name         VARCHAR(256) DEFAULT '',
    category             VARCHAR(32)  DEFAULT '',
    dosage               VARCHAR(128) DEFAULT '',
    indications_json     JSONB       DEFAULT '[]',
    mechanism            TEXT         DEFAULT '',
    side_effects_json    JSONB       DEFAULT '[]',
    contraindications_json JSONB     DEFAULT '[]',
    interactions_json    JSONB       DEFAULT '[]',
    pregnancy_grade      VARCHAR(8)   DEFAULT '',
    is_essential         BOOLEAN      DEFAULT false,
    source               VARCHAR(32)  DEFAULT 'seed',
    create_at            TIMESTAMPTZ    DEFAULT NOW(),
    create_by            VARCHAR(64)  DEFAULT 'default',
    update_at            TIMESTAMPTZ    DEFAULT NOW(),
    update_by            VARCHAR(64)  DEFAULT 'default',
    is_deleted           SMALLINT     DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_pc_medical_kb_drugs_category   ON pc_medical_kb_drugs (category);
CREATE INDEX IF NOT EXISTS ix_pc_medical_kb_drugs_is_deleted ON pc_medical_kb_drugs (is_deleted);
"""

# 研究简报（分析管线产出）
_DDL_BRIEFS = """
CREATE TABLE IF NOT EXISTS pc_medical_briefs (
    id                VARCHAR(64)  PRIMARY KEY,
    patient_id        VARCHAR(64)  NOT NULL,
    report_ids_json   JSONB        DEFAULT '[]',
    status            VARCHAR(16)  DEFAULT '分析中',
    version           SMALLINT     DEFAULT 1,
    director_plan_json JSONB       DEFAULT '{}',
    sections_json     JSONB        DEFAULT '[]',
    citations_json    JSONB        DEFAULT '[]',
    audit_result_json JSONB        DEFAULT '{}',
    error_message     TEXT         DEFAULT '',
    started_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ,
    create_at         TIMESTAMPTZ    DEFAULT NOW(),
    create_by         VARCHAR(64)  DEFAULT 'default',
    update_at         TIMESTAMPTZ    DEFAULT NOW(),
    update_by         VARCHAR(64)  DEFAULT 'default',
    is_deleted        SMALLINT     DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_pc_medical_briefs_patient_id
    ON pc_medical_briefs (patient_id);
CREATE INDEX IF NOT EXISTS ix_pc_medical_briefs_status
    ON pc_medical_briefs (status);
CREATE INDEX IF NOT EXISTS ix_pc_medical_briefs_is_deleted
    ON pc_medical_briefs (is_deleted);
"""

# 派生视图 — 患者时间线
_DDL_TIMELINE_VIEW = """
CREATE OR REPLACE VIEW v_patient_timeline AS
SELECT
    patient_id,
    id              AS event_id,
    report_type     AS event_type,
    COALESCE(visit_date, create_at::date) AS event_date,
    diagnosis       AS title,
    summary         AS detail
FROM pc_medical_patient_reports
WHERE is_deleted = 0;
"""


# 增量列：存量库的表早已建好，建表语句不会再生效，靠这里补齐。
# ADD COLUMN IF NOT EXISTS 幂等，每次启动执行无副作用。
_DDL_REPORT_PARSING_COLUMNS = """
ALTER TABLE pc_medical_patient_reports ADD COLUMN IF NOT EXISTS patient_name_raw VARCHAR(64) DEFAULT '';
ALTER TABLE pc_medical_patient_reports ADD COLUMN IF NOT EXISTS match_reason VARCHAR(32) DEFAULT '';
ALTER TABLE pc_medical_patient_reports ALTER COLUMN mime_type TYPE VARCHAR(128);
"""

# 患者档案增量列：id_type（新建患者时登记证件类型）。
# 存量库的表早已建好，靠这里补齐；ADD COLUMN IF NOT EXISTS 幂等。
_DDL_PATIENT_EXTRA_COLUMNS = """
ALTER TABLE pc_medical_patients ADD COLUMN IF NOT EXISTS id_type VARCHAR(16) DEFAULT '';
"""


async def _execute_with_advisory_lock(
    engine: AsyncEngine,
    lock_key: int,
    ddl: str,
) -> None:
    """在 Advisory Lock 保护下执行 DDL。"""
    async with engine.begin() as conn:
        await conn.execute(text(f"SELECT pg_advisory_lock({lock_key})"))
        try:
            for stmt in ddl.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    await conn.execute(text(stmt))
            logger.info("DDL 执行完成 (lock_key=%d)", lock_key)
        finally:
            await conn.execute(text(f"SELECT pg_advisory_unlock({lock_key})"))


async def ensure_medical_schema(engine: AsyncEngine) -> None:
    """确保医疗域全部数据库 Schema 就绪。

    分五组 Advisory Lock 依次创建，并创建派生视图。
    """
    logger.info("开始确保医疗域 Schema...")

    await _execute_with_advisory_lock(engine, ADVISORY_LOCK_KEYS["patients"], _DDL_PATIENTS)
    await _execute_with_advisory_lock(engine, ADVISORY_LOCK_KEYS["patients"], _DDL_PATIENT_EXTRA_COLUMNS)
    await _execute_with_advisory_lock(engine, ADVISORY_LOCK_KEYS["patient_reports"], _DDL_PATIENT_REPORTS)
    await _execute_with_advisory_lock(engine, ADVISORY_LOCK_KEYS["patient_reports"], _DDL_REPORT_PARSING_COLUMNS)
    await _execute_with_advisory_lock(engine, ADVISORY_LOCK_KEYS["consultations"], _DDL_CONSULTATIONS)
    await _execute_with_advisory_lock(engine, ADVISORY_LOCK_KEYS["kb_literatures"], _DDL_KB_LITERATURES)
    await _execute_with_advisory_lock(engine, ADVISORY_LOCK_KEYS["kb_guidelines"], _DDL_KB_GUIDELINES)
    await _execute_with_advisory_lock(engine, ADVISORY_LOCK_KEYS["kb_drugs"], _DDL_KB_DRUGS)
    await _execute_with_advisory_lock(engine, ADVISORY_LOCK_KEYS["briefs"], _DDL_BRIEFS)

    # 视图无锁竞争，直接建
    async with engine.begin() as conn:
        for stmt in _DDL_TIMELINE_VIEW.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                await conn.execute(text(stmt))
    logger.info("医疗域 Schema 确保完成")
