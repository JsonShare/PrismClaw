-- 医疗域表结构（域激活时由应用执行）。

CREATE TABLE IF NOT EXISTS pc_medical_patients (
    id                VARCHAR(64)  PRIMARY KEY,
    name              VARCHAR(64)  DEFAULT '',
    gender            VARCHAR(8)   DEFAULT '',
    birth_date        VARCHAR(16)  DEFAULT '',
    blood_type        VARCHAR(8)   DEFAULT '',
    id_type           VARCHAR(16)  DEFAULT '',
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
    create_at         TIMESTAMPTZ  DEFAULT NOW(),
    create_by         VARCHAR(64)  DEFAULT 'default',
    update_at         TIMESTAMPTZ  DEFAULT NOW(),
    update_by         VARCHAR(64)  DEFAULT 'default',
    is_deleted        SMALLINT     DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_pc_medical_patients_department      ON pc_medical_patients (department);
CREATE INDEX IF NOT EXISTS ix_pc_medical_patients_risk_level      ON pc_medical_patients (risk_level);
CREATE INDEX IF NOT EXISTS ix_pc_medical_patients_id_number_hash  ON pc_medical_patients (id_number_hash);
CREATE INDEX IF NOT EXISTS ix_pc_medical_patients_phone_hash      ON pc_medical_patients (phone_hash);
CREATE INDEX IF NOT EXISTS ix_pc_medical_patients_is_deleted      ON pc_medical_patients (is_deleted);

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
    patient_name_raw VARCHAR(64)  DEFAULT '',
    match_reason     VARCHAR(32)  DEFAULT '',
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
    create_at        TIMESTAMPTZ  DEFAULT NOW(),
    create_by        VARCHAR(64)  DEFAULT 'default',
    update_at        TIMESTAMPTZ  DEFAULT NOW(),
    update_by        VARCHAR(64)  DEFAULT 'default',
    is_deleted       SMALLINT     DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_pc_medical_patient_reports_patient_id   ON pc_medical_patient_reports (patient_id);
CREATE INDEX IF NOT EXISTS ix_pc_medical_patient_reports_status      ON pc_medical_patient_reports (status);
CREATE INDEX IF NOT EXISTS ix_pc_medical_patient_reports_report_type ON pc_medical_patient_reports (report_type);
CREATE INDEX IF NOT EXISTS ix_pc_medical_patient_reports_is_deleted  ON pc_medical_patient_reports (is_deleted);

CREATE TABLE IF NOT EXISTS pc_medical_consultations (
    id          VARCHAR(64)   PRIMARY KEY,
    session_id  VARCHAR(128)  NOT NULL UNIQUE,
    patient_id  VARCHAR(64)   NOT NULL,
    doctor_id   VARCHAR(64)   NOT NULL,
    title       VARCHAR(256)  DEFAULT '',
    created_at  TIMESTAMPTZ   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_med_cons_patient
    ON pc_medical_consultations (patient_id, created_at DESC);

CREATE TABLE IF NOT EXISTS pc_medical_kb_literatures (
    id            VARCHAR(64) PRIMARY KEY,
    title         VARCHAR(512) NOT NULL,
    title_en      VARCHAR(512) DEFAULT '',
    title_cn      VARCHAR(512) DEFAULT '',
    study_type    VARCHAR(16)  DEFAULT '',
    journal_tags_json JSONB    DEFAULT '[]',
    authors       VARCHAR(512) DEFAULT '',
    journal       VARCHAR(128) DEFAULT '',
    year          INTEGER,
    impact_factor DOUBLE PRECISION DEFAULT 0,
    published_at  TIMESTAMPTZ,
    category      VARCHAR(32)  DEFAULT '',
    abstract      TEXT         DEFAULT '',
    keywords_json JSONB        DEFAULT '[]',
    citations     INTEGER      DEFAULT 0,
    doi           VARCHAR(128) DEFAULT '',
    source        VARCHAR(32)  DEFAULT 'seed',
    create_at     TIMESTAMPTZ  DEFAULT NOW(),
    create_by     VARCHAR(64)  DEFAULT 'default',
    update_at     TIMESTAMPTZ  DEFAULT NOW(),
    update_by     VARCHAR(64)  DEFAULT 'default',
    is_deleted    SMALLINT     DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_pc_medical_kb_literatures_category   ON pc_medical_kb_literatures (category);
CREATE INDEX IF NOT EXISTS ix_pc_medical_kb_literatures_year       ON pc_medical_kb_literatures (year);
CREATE INDEX IF NOT EXISTS ix_pc_medical_kb_literatures_is_deleted ON pc_medical_kb_literatures (is_deleted);

CREATE TABLE IF NOT EXISTS pc_medical_kb_guidelines (
    id          VARCHAR(64) PRIMARY KEY,
    title       VARCHAR(512) NOT NULL,
    title_cn    VARCHAR(512) DEFAULT '',
    doc_type    VARCHAR(16)  DEFAULT '',
    authors     VARCHAR(512) DEFAULT '',
    specialty   VARCHAR(64)  DEFAULT '',
    region      VARCHAR(16)  DEFAULT '',
    published_at TIMESTAMPTZ,
    org         VARCHAR(32)  DEFAULT '',
    category    VARCHAR(32)  DEFAULT '',
    year        INTEGER,
    version     VARCHAR(32)  DEFAULT '',
    abstract    TEXT         DEFAULT '',
    points_json JSONB        DEFAULT '[]',
    source      VARCHAR(32)  DEFAULT 'seed',
    create_at   TIMESTAMPTZ  DEFAULT NOW(),
    create_by   VARCHAR(64)  DEFAULT 'default',
    update_at   TIMESTAMPTZ  DEFAULT NOW(),
    update_by   VARCHAR(64)  DEFAULT 'default',
    is_deleted  SMALLINT     DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_pc_medical_kb_guidelines_category   ON pc_medical_kb_guidelines (category);
CREATE INDEX IF NOT EXISTS ix_pc_medical_kb_guidelines_year       ON pc_medical_kb_guidelines (year);
CREATE INDEX IF NOT EXISTS ix_pc_medical_kb_guidelines_is_deleted ON pc_medical_kb_guidelines (is_deleted);

CREATE TABLE IF NOT EXISTS pc_medical_kb_drugs (
    id                   VARCHAR(64) PRIMARY KEY,
    name                 VARCHAR(128) NOT NULL,
    generic_name         VARCHAR(256) DEFAULT '',
    category             VARCHAR(32)  DEFAULT '',
    drug_kind            VARCHAR(8)   DEFAULT '西药',
    atc_l1               VARCHAR(64)  DEFAULT '',
    atc_l2               VARCHAR(64)  DEFAULT '',
    atc_group            VARCHAR(128) DEFAULT '',
    atc_group_desc       VARCHAR(256) DEFAULT '',
    dosage               VARCHAR(128) DEFAULT '',
    indications_json     JSONB       DEFAULT '[]',
    mechanism            TEXT         DEFAULT '',
    side_effects_json    JSONB       DEFAULT '[]',
    contraindications_json JSONB     DEFAULT '[]',
    interactions_json    JSONB       DEFAULT '[]',
    pregnancy_grade      VARCHAR(8)   DEFAULT '',
    is_essential         BOOLEAN      DEFAULT false,
    source               VARCHAR(32)  DEFAULT 'seed',
    create_at            TIMESTAMPTZ  DEFAULT NOW(),
    create_by            VARCHAR(64)  DEFAULT 'default',
    update_at            TIMESTAMPTZ  DEFAULT NOW(),
    update_by            VARCHAR(64)  DEFAULT 'default',
    is_deleted           SMALLINT     DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_pc_medical_kb_drugs_category   ON pc_medical_kb_drugs (category);
CREATE INDEX IF NOT EXISTS ix_pc_medical_kb_drugs_is_deleted ON pc_medical_kb_drugs (is_deleted);

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
    create_at         TIMESTAMPTZ  DEFAULT NOW(),
    create_by         VARCHAR(64)  DEFAULT 'default',
    update_at         TIMESTAMPTZ  DEFAULT NOW(),
    update_by         VARCHAR(64)  DEFAULT 'default',
    is_deleted        SMALLINT     DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_pc_medical_briefs_patient_id
    ON pc_medical_briefs (patient_id);
CREATE INDEX IF NOT EXISTS ix_pc_medical_briefs_status
    ON pc_medical_briefs (status);
CREATE INDEX IF NOT EXISTS ix_pc_medical_briefs_is_deleted
    ON pc_medical_briefs (is_deleted);

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
