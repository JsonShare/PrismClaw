"""医疗域 Table 定义 + 列集合 — SQLAlchemy Core (无 ORM)。

7 张表 + 1 个 MetaData：
- pc_medical_patients          患者档案 + 画像
- pc_medical_patient_reports   统一临床文档表
- pc_medical_consultations     咨询会话登记（域侧记录，核心不感知）
- pc_medical_kb_literatures    文献
- pc_medical_kb_guidelines     指南
- pc_medical_kb_drugs          药品
- pc_medical_briefs            研究简报（分析管线产出）

列集合常量供 Store 的 upsert 过滤使用。
"""
from __future__ import annotations

from sqlalchemy import (
    Column,
    MetaData,
    String,
    Text,
    Date,
    DateTime,
    Integer,
    SmallInteger,
    Boolean,
    Table,
    text,
    TIMESTAMP,
)
from sqlalchemy.dialects.postgresql import JSONB

# ── MetaData（全局唯一，DDL 反射用）──────────────────────────

metadata = MetaData()

# ── 患者档案 + 画像（合并原 patients + patients_extended）──────

patients = Table(
    "pc_medical_patients",
    metadata,
    Column("id", String(64), primary_key=True, comment="患者 ID"),
    Column("name", String(64), default="", comment="姓名"),
    Column("gender", String(8), default="", comment="性别"),
    Column("birth_date", String(16), default="", comment="出生日期（推算 age，不落库）"),
    Column("blood_type", String(8), default="", comment="血型"),
    Column("id_type", String(16), default="", comment="证件类型：身份证/护照/港澳通行证/台湾通行证/军官证/其他"),
    Column("id_number_masked", String(32), default="", comment="身份证号脱敏显示"),
    Column("phone_masked", String(32), default="", comment="手机号脱敏显示"),
    Column("id_number_hash", String(64), default="", comment="身份证号 HMAC 哈希（精确搜索）"),
    Column("phone_hash", String(64), default="", comment="手机号 HMAC 哈希（精确搜索）"),
    Column("department", String(64), default="", comment="科室"),
    Column("primary_diagnosis", String(256), default="", comment="主要诊断"),
    Column("tnm_stage", String(32), default="", comment="TNM 分期"),
    Column("clinical_stage", String(32), default="", comment="临床分期"),
    Column("ecog_score", Integer, default=0, comment="ECOG 评分"),
    Column("treatment_status", String(32), default="", comment="治疗状态"),
    Column("current_regimen", String(128), default="", comment="当前方案"),
    Column("current_cycle", String(32), default="", comment="当前周期"),
    Column("risk_level", String(16), default="", comment="风险等级"),
    Column("vitals_json", JSONB, default="{}", comment="生命体征"),
    Column("biomarkers_json", JSONB, default="{}", comment="生物标志物"),
    Column("allergies_json", JSONB, default="[]", comment="过敏史"),
    Column("history_json", JSONB, default="[]", comment="既往史"),
    Column("medications_json", JSONB, default="[]", comment="聚合态：当前用药列表"),
    Column("labs_json", JSONB, default="[]", comment="检验"),
    Column("attending_doctor", String(64), default="", comment="主治医师"),
    Column("tags_json", JSONB, default="[]", comment="标签"),
    Column("create_at", DateTime(timezone=True), comment="创建时间"),
    Column("create_by", String(64), default="default", comment="创建人"),
    Column("update_at", DateTime(timezone=True), comment="更新时间"),
    Column("update_by", String(64), default="default", comment="更新人"),
    Column("is_deleted", SmallInteger, default=0, comment="逻辑删除：0 正常 / 1 删除"),
)

# ── 统一临床文档表 ─────────────────────────────────────────────

patient_reports = Table(
    "pc_medical_patient_reports",
    metadata,
    Column("id", String(64), primary_key=True, comment="文档 ID"),
    Column("patient_id", String(64), nullable=True, index=True, comment="患者 ID（允许 NULL）"),
    Column("report_type", String(32), default="outpatient", comment="文档类型"),
    Column("file_name", String(256), nullable=False, comment="文件名"),
    Column("file_type", String(16), default="text", comment="展示图标：text/image/audio"),
    Column("file_path", Text, default="", comment="存储 URL"),
    Column("mime_type", String(128), default="", comment="MIME 类型"),
    Column("attachments_json", JSONB, default="[]", comment="多页/多图归组"),
    Column("hospital_name", String(128), default="", comment="报告来源医院"),
    Column("doctor_name", String(64), default="", comment="报告署名医师"),
    Column("patient_name_raw", String(64), default="", comment="AI 识别的患者姓名（未归档时的指派线索）"),
    Column("match_reason", String(32), default="", comment="归属判定原因 exact/ambiguous/no_match/..."),
    Column("status", String(16), default="排队中", comment="生命周期"),
    Column("progress", SmallInteger, default=0, comment="处理进度 0-100"),
    Column("retry_count", SmallInteger, default=0, comment="重试次数"),
    Column("error_message", Text, default="", comment="失败原因"),
    Column("raw_text", Text, default="", comment="OCR/原始抽取文本"),
    Column("visit_date", Date, comment="文档所述就诊日期"),
    Column("department", String(64), default="", comment="科室"),
    Column("diagnosis", String(256), default="", comment="AI 抽取诊断"),
    Column("narrative_json", JSONB, default="[]", comment="结构化叙事"),
    Column("labs_json", JSONB, default="[]", comment="检验项"),
    Column("medications_json", JSONB, default="[]", comment="快照态：该份报告提到的药物"),
    Column("tags_json", JSONB, default="[]", comment="标签"),
    Column("summary", Text, default="", comment="AI 摘要"),
    Column("started_at", DateTime(timezone=True), comment="处理开始时刻"),
    Column("finished_at", DateTime(timezone=True), comment="处理完成时刻"),
    Column("create_at", DateTime(timezone=True), comment="创建时间"),
    Column("create_by", String(64), default="default", comment="创建人"),
    Column("update_at", DateTime(timezone=True), comment="更新时间"),
    Column("update_by", String(64), default="default", comment="更新人"),
    Column("is_deleted", SmallInteger, default=0, comment="逻辑删除：0 正常 / 1 删除"),
)

# ── 咨询会话登记（域侧记录，核心不感知。登记行 = 工具的授权凭证）──────────

consultations = Table(
    "pc_medical_consultations",
    metadata,
    Column("id", String(64), primary_key=True, comment="登记行 ID"),
    Column("session_id", String(128), nullable=False, unique=True, comment="核心返回的会话 id"),
    Column("patient_id", String(64), nullable=False, comment="患者 ID"),
    Column("doctor_id", String(64), nullable=False, comment="医生 ID"),
    Column("title", String(256), default="", comment="咨询标题"),
    Column("created_at", DateTime(timezone=True), comment="创建时间"),
)

# ── 知识库：文献 ──────────────────────────────────────────────

kb_literatures = Table(
    "pc_medical_kb_literatures",
    metadata,
    Column("id", String(64), primary_key=True, comment="文献 ID"),
    Column("title", String(512), nullable=False, comment="标题"),
    Column("authors", String(512), default="", comment="作者"),
    Column("journal", String(128), default="", comment="期刊"),
    Column("year", Integer, comment="发表年份"),
    Column("category", String(32), default="", comment="分类"),
    Column("abstract", Text, default="", comment="摘要"),
    Column("keywords_json", JSONB, default="[]", comment="关键词"),
    Column("citations", Integer, default=0, comment="引用数"),
    Column("doi", String(128), default="", comment="DOI"),
    Column("source", String(32), default="seed", comment="来源"),
    Column("create_at", TIMESTAMP(timezone=True), server_default=text("NOW()"), comment="创建时间"),
    Column("create_by", String(64), default="default", comment="创建人"),
    Column("update_at", TIMESTAMP(timezone=True), server_default=text("NOW()"), comment="更新时间"),
    Column("update_by", String(64), default="default", comment="更新人"),
    Column("is_deleted", Integer, default=0, comment="逻辑删除"),
)

# ── 知识库：指南 ──────────────────────────────────────────────

kb_guidelines = Table(
    "pc_medical_kb_guidelines",
    metadata,
    Column("id", String(64), primary_key=True, comment="指南 ID"),
    Column("title", String(512), nullable=False, comment="标题"),
    Column("org", String(32), default="", comment="发布机构"),
    Column("category", String(32), default="", comment="分类"),
    Column("year", Integer, comment="发布年份"),
    Column("version", String(32), default="", comment="版本"),
    Column("abstract", Text, default="", comment="摘要"),
    Column("points_json", JSONB, default="[]", comment="核心推荐条目"),
    Column("source", String(32), default="seed", comment="来源"),
    Column("create_at", TIMESTAMP(timezone=True), server_default=text("NOW()"), comment="创建时间"),
    Column("create_by", String(64), default="default", comment="创建人"),
    Column("update_at", TIMESTAMP(timezone=True), server_default=text("NOW()"), comment="更新时间"),
    Column("update_by", String(64), default="default", comment="更新人"),
    Column("is_deleted", Integer, default=0, comment="逻辑删除"),
)

# ── 知识库：药品 ──────────────────────────────────────────────

kb_drugs = Table(
    "pc_medical_kb_drugs",
    metadata,
    Column("id", String(64), primary_key=True, comment="药品 ID"),
    Column("name", String(128), nullable=False, comment="药品名"),
    Column("generic_name", String(256), default="", comment="通用名"),
    Column("category", String(32), default="", comment="分类"),
    Column("dosage", String(128), default="", comment="用法用量"),
    Column("indications_json", JSONB, default="[]", comment="适应症"),
    Column("mechanism", Text, default="", comment="作用机制"),
    Column("side_effects_json", JSONB, default="[]", comment="不良反应"),
    Column("contraindications_json", JSONB, default="[]", comment="禁忌"),
    Column("interactions_json", JSONB, default="[]", comment="相互作用"),
    Column("pregnancy_grade", String(8), default="", comment="妊娠分级"),
    Column("is_essential", Boolean, default=False, comment="国家基药"),
    Column("source", String(32), default="seed", comment="来源"),
    Column("create_at", TIMESTAMP(timezone=True), server_default=text("NOW()"), comment="创建时间"),
    Column("create_by", String(64), default="default", comment="创建人"),
    Column("update_at", TIMESTAMP(timezone=True), server_default=text("NOW()"), comment="更新时间"),
    Column("update_by", String(64), default="default", comment="更新人"),
    Column("is_deleted", Integer, default=0, comment="逻辑删除"),
)

# ── 研究简报（分析管线产出）────────────────────────────────────

briefs = Table(
    "pc_medical_briefs",
    metadata,
    Column("id", String(64), primary_key=True, comment="简报 ID"),
    Column("patient_id", String(64), nullable=False, index=True, comment="患者 ID"),
    Column("report_ids_json", JSONB, default="[]", comment="消费的报告 ID 列表"),
    Column("status", String(16), default="分析中", comment="分析中/已通过/待修正/已废弃"),
    Column("version", SmallInteger, default=1, comment="重新分析递增"),
    Column("director_plan_json", JSONB, default="{}", comment="主任规划输出"),
    Column("sections_json", JSONB, default="[]", comment="简报正文（含引用）"),
    Column("citations_json", JSONB, default="[]", comment="统一引用表"),
    Column("audit_result_json", JSONB, default="{}", comment="审计结果"),
    Column("error_message", Text, default="", comment="失败/待修正原因"),
    Column("started_at", DateTime(timezone=True), comment="分析开始时刻"),
    Column("finished_at", DateTime(timezone=True), comment="分析完成时刻"),
    Column("create_at", DateTime(timezone=True), comment="创建时间"),
    Column("create_by", String(64), default="default", comment="创建人"),
    Column("update_at", DateTime(timezone=True), comment="更新时间"),
    Column("update_by", String(64), default="default", comment="更新人"),
    Column("is_deleted", SmallInteger, default=0, comment="逻辑删除：0 正常 / 1 删除"),
)

# ── 列集合常量（upsert 过滤用）───────────────────────────────

PATIENT_COLUMNS: frozenset[str] = frozenset({
    "name", "gender", "birth_date", "blood_type", "id_type", "id_number_masked", "phone_masked",
    "id_number_hash", "phone_hash", "department", "primary_diagnosis", "tnm_stage",
    "clinical_stage", "ecog_score", "treatment_status", "current_regimen", "current_cycle",
    "risk_level", "vitals_json", "biomarkers_json", "allergies_json", "history_json",
    "medications_json", "labs_json", "attending_doctor", "tags_json",
})

REPORT_COLUMNS: frozenset[str] = frozenset({
    "patient_id", "report_type", "file_name", "file_type", "file_path", "mime_type",
    "attachments_json", "hospital_name", "doctor_name",
    "patient_name_raw", "match_reason",
    "status", "progress", "retry_count", "error_message", "raw_text", "visit_date",
    "department", "diagnosis", "narrative_json", "labs_json", "medications_json",
    "tags_json", "summary", "started_at", "finished_at",
})

CONSULTATION_COLUMNS: frozenset[str] = frozenset({
    "session_id", "patient_id", "doctor_id", "title",
})

BRIEF_COLUMNS: frozenset[str] = frozenset({
    "patient_id", "report_ids_json", "status", "version",
    "director_plan_json", "sections_json", "citations_json",
    "audit_result_json", "error_message", "started_at", "finished_at",
})
