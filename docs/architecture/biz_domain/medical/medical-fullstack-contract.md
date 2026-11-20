# 医疗域前后端联调设计（数据库表 + 接口契约）

> 对接双方：`src/subpackages.medical`（FastAPI 域分包）↔ `frontend/subpackages/medical`（React 子包）
>
> 前置文档：[medical-domain-design.md](./medical-domain-design.md)（域能力设计）、
> [domain-bundle-design.md](../_shared/domain-bundle-design.md)（Bundle 机制）
>
> 本文只描述**联调契约**：建哪些表、开哪些接口、前后端类型形状。UI 还原问题不在本文范围。

---

## 0. 结论摘要

| 项 | 数量 | 说明 |
|---|---|---|
| 数据库表 | **7 张 + 1 视图** = `pc_medical_patients` / `pc_medical_patient_reports` / `pc_medical_consultations` / `pc_medical_kb_literatures` / `pc_medical_kb_guidelines` / `pc_medical_kb_drugs` / `pc_medical_briefs` + 派生视图 `v_patient_timeline` | 见 §2 |
| HTTP 接口 | **24 个**（患者 6 / 病历 7 / 知识库 3 / 咨询 3 / 摘要 5） | 见 §3 |
| 前端类型 | 实体 `id` 为 `string` 短码（5 个类型） | 见 §4 |

两个必须先说清的前提（否则整个设计不成立）：

1. **本产品无 HIS 数据源**。数据唯一入口是「上传病历」，经 AI 提取后落库。全系统的写操作只有一处 —— **上传病历**（收藏、导出一页纸均为纯客户端，见 §3.3）。患者/病历/指南/药品均无人工增删改入口。这是当前 UI 的客观事实（全库扫描确认：`PatientsPage` 只有筛选+分页，`PatientDetailPage` 只有一个「返回」按钮，`MedicalRecordsPage` 只有上传，`PatientDetailView` 有「复制摘要」与「导出一页纸」两个按钮）。
2. **前端 `api/client.ts` 无消费方**（6 个页面全部从 `data/mock-data.ts` 取数）。响应体形状因此不是兼容性约束：前后端直接按 camelCase 视图模型对接，不设 snake_case 契约层，也不留双轨。

### 0.1 真实报告验证

> 用一例真实肺癌患者报告集（孟先生质子治疗补充报告，36 页图片 → 去重后 **21 份独立文档**）对表设计做承载性验证。逐份核对后补齐缺口（D14/D15）；本节记录验证事实，供后续表结构演进时对照。

**21 份文档类型分布**：

| 类型 | 份数 | 现有 report_type | 验证结论 |
|---|---|---|---|
| 出院记录（扫描件 3 页，含 12 项出院诊断） | 1 | `discharge` | ✅ |
| 24 小时入出院记录（横排 3 页） | 1 | `admission`/`discharge` | ✅ |
| 疾病诊断证明书 | 2（重复拍摄 1） | `diagnosis_cert` | ✅ |
| 全胸 CT（2024-09→2025-04 共 5 个日期，1 份重复拍） | 6 | `imaging` | ✅；多日期靠 `visit_date` 区分 |
| EBUS-TBNA / 细胞蜡块 / 病理申请 / 免疫组化 | 4 | `surgery`/`pathology`/`pathology_supplement` | ✅ |
| **TROP2 分子检测报告（2 页 + 4 显微图）** | 1 | **❌ 无对应值** | → 新增 `gene_test` |
| 检验报告手机小程序截图 ×7（血常规约 25 项 / 甲功 5 / 凝血+D-dimer / 肌钙蛋白 / VEGF / 肿标 6 / 生化约 30 项） | 7 | `biochemistry` | ✅；labs_json 项 schema 见 §2.2.1 |
| **家属手绘病程表（纸质）+ Excel 截图** | 2 | **❌ 无对应值** | → 新增 `family_note` |

**数据形态结论**（决定列设计）：

1. **多页/多图归组是常态**：出院记录 3 页、24h 入出院 3 页、TROP2 2 页 → `attachments_json`（D15）。重复拍摄（CT ×2、诊断书 ×2）由 AI 解读阶段去重，表结构只需支持 1 文档 N 附件。
2. **检验值三形态**：数值 `8.98` / 上限型 `<10.00` / 分级 `+++`；参考值三形态：`0-3.3` / `<3.7` / `≤4.0`；异常标记 `↑↓`（另有 ◆ 重点标记）→ labs_json 项必须 `value_text` 恒存 + `value_num` 可选（§2.2.1），参考范围只存 `ref_text` 不拆 min/max。
3. **多医院转诊**：湖南省肿瘤医院 → 上海长海 → 人民医院，每份报告页眉自带医院名与医师签名 → `hospital_name` / `doctor_name` 提列（D15）。
4. **患者画像字段够用**：`tnm_stage`（T4N3Mx）、`clinical_stage`（IIIc期）、`blood_type`（A型 RH+）、`ecog_score`、`current_regimen`（白蛋白紫杉醇+卡铂+替雷利珠）在真实报告中全部有值，无需新增列。医院病案号（284879）多院各不相同，不提列、走 `history_json`。
5. **报告因果链（病理申请→蜡块→IHC→TROP2 同源标本）不加关联字段**：Phase 1 检索问答按语义路由即可，建 `related_report_id` 属 YAGNI。

**国标 WS/T 500-2016 验证（D16 依据）**：

> 用卫健委《电子病历共享文档规范》（57 种文档类型）和《电子病历基本数据集》（WS 445-2014）做外部基准，核对 13 值枚举对国标常见文档的覆盖度。不是自洽——这是独立的标准源。

| 国标文档类型 | WS/T 500 编号 | 13 值覆盖 | 16 值覆盖 |
|---|---|---|---|
| 门(急)诊病历 | 500.2 | `outpatient` ✅ | ✅ |
| 西药/中药处方 | 500.4/5 | ❌ 无对应 | `prescription` ✅ |
| 检查报告 | 500.6 | `exam` ⚠️ 语义模糊 | `exam`（标签修正为"功能检查报告"）✅ |
| 检验报告 | 500.7 | `biochemistry` ✅ | ✅ |
| 治疗记录 | 500.8 | ❌ 无对应 | `treatment_record` ✅ |
| 手术记录 | 500.9 | `surgery` ✅ | ✅ |
| 病程记录 | WS 445.13 | ❌ 无对应 | `progress_note` ✅ |
| 出院小结 | WS 445.15 | `discharge` ✅ | ✅ |
| 护理/麻醉/输血等 | 500.10-25 | P2（家庭照护者不上传/不常见） | 归入最近值可接受 |

结论：13 值 → 16 值后，**国标 P0 级文档全覆盖**（门急诊、处方、检验、检查、治疗、手术、病程、出院）；`exam` 中文标签从"检测报告"修正为"功能检查报告"，消除与 `biochemistry` 的语义重叠。

---

## 1. 设计决策与依据

| # | 决策 | 依据（已核实的事实） |
|---|---|---|
| D1 | 不提供无消费方端点 | 全库扫描 UI：患者/病历/指南/药品**均无增删改入口**，唯一数据入口是上传 |
| D2 | `pc_medical_patients` + `patients_extended` **合并为单表** | 两表主键同为 `VARCHAR(64) id`，是同一实体的两次建模；合并后 `pc_medical_patients` 承载全部档案+画像字段 |
| D3 | 证件号/手机号**入库即脱敏**，列名带 `_masked` 后缀 | 让"这是脱敏值"在 schema 层无法被忽略；不设 `name_hash` 裸列（零读取方） |
| D4 | 额外存 `id_number_hash` / `phone_hash`（HMAC） | `PatientFilterBar` 的 placeholder 明写"搜索姓名 / 手机号 / 证件号"。只存掩码则搜完整号码必然搜不到——脱敏与搜索的固有冲突，必须补哈希索引解决 |
| D5 | 知识库三库建表，数据落持久化表而非工具内字典 | 分页与关键词检索都需要持久化；指南来自真实 PDF 解析导入（`tools/ingest_guidelines.py`），文献/药品由 `persistence/seeds/kb.py` 灌入，Agent 工具统一读 `KbStore` |
| D6 | 后端**直出 camelCase 视图模型** | 前端不需要 `mapPatient`/`mapTimelineEvent` 这类纯搬运映射层 |
| D7 | 实体 ID 用 **VARCHAR(64) 短码而非 UUID**（患者 `P0001`、药品 `pembrolizumab`） | 与项目既有全部医疗表 `VARCHAR(64)` 惯例一致；短码保证 URL 深链 `?mw_patient=P0001` 可读；短码同时让种子数据可幂等重跑 |
| D8 | AI 对话**下沉到患者详情页**，端点改为 `/api/medical/consultations/*` 登记会话；不设全局面板 `/agent/chat` | 对话上下文本质是患者级（报告清单 + 画像），页面级助手无数据支撑 |
| D9 | 肿瘤信息**不单独建表**：不设 `oncology.py` 的 8 个 HTTP 端点、3 张肿瘤表（`chemo_protocols`/`targeted_matches`/`proton_assessments`）与 3 个肿瘤 Agent 工具 | 用户确认：肿瘤信息落在**患者画像 + 上传报告**中（方案/匹配/评估都来自报告抽取与画像字段），无需单独建表 |
| D10 | **时间线改为派生视图，不存表** | 时间线只是 `patient_reports` 按患者聚合的投影；无独立写入方（不设手动写端点），100% 可派生 |
| D11 | `pc_medical_patient_reports` **合并** `pc_medical_processing_tasks` | 每次上传 = 1 报告 = 1 任务（1:1），拆分会造成前端双写双查；单表 `status` 承载完整流水线生命周期 |
| D12 | **不做收藏功能**，不设 `pc_medical_kb_bookmarks` | 用户确认不需要收藏；收藏是行为数据，无消费方 |
| D13 | 病历**合并入** `pc_medical_patient_reports` | 一份上传文档 = 一次 AI 抽取 = 一条结构化病历（1:1 同日生同患者同删除），拆两张表是同一事物的两次建模；统一为「临床文档表」，用 `report_type` 判别文档类型 |
| D14 | `report_type` 枚举 **+2**：`gene_test`（基因/分子检测）、`family_note`（家属自录） | **真实报告验证**（孟先生质子报告 36 页 → 21 份独立文档，见 §0.1）：TROP2 伴随诊断报告既非病理也非生化，11 值装不下；家属手绘病程表 + Excel 疗程记录是老年照护产品最高频输入形态之一，必须有类型兜底，否则枚举落不进去 |
| D15 | reports 表 **+3 列**：`hospital_name` / `doctor_name` / `attachments_json` | **真实报告验证**：患者三家医院转诊（湖南省肿瘤医院→长海→人民医院），`department` 仅是科室，定位不了报告来源；出院记录 3 页 / TROP2 2 页均为一份逻辑文档多个物理图片，`file_path` 单值只能挂首图 |
| D16 | `report_type` 枚举 **+3**：`progress_note`（病程记录）、`prescription`（处方/医嘱）、`treatment_record`（治疗记录） | **国标 WS/T 500-2016 验证**：病程记录（WS 445.13）是住院期间数量最多的文档类型，当前无值可放；处方/医嘱（WS/T 500.4-5）是老年照护最高频上传类型之一（出院带药单、门诊处方）；治疗记录（WS/T 500.8）含化疗执行单/放疗计划单/免疫记录，既非手术也非检查 |
| D17 | `narrative_json` 补 **sections 骨架契约**（§2.2.2） | 不同 `report_type` 叙事结构完全不同（出院记录=主诉+现病史+诊疗经过+出院医嘱；病理=标本+镜下+诊断+注释；影像=检查方法+所见+印象），无契约则 AI 抽取格式随意→前端只能当纯文本→等于 `raw_text` 冗余副本。定义最小公共骨架 `sections[{heading, body}]`，前端一个通用 `SectionList` 组件覆盖全部类型 |

---

## 2. 数据库设计

### 2.1 命名规范（先立规矩，再定名字）

> 表名/字段名**先符合下列规则**，再逐表确定。规则之外的命名一律视为不合格。

**基础字段规范（所有表统一，优先级最高）**
所有表在业务字段之外，统一携带以下基础字段（顺序约定：主键 `id` → 业务字段 → 基础字段）：

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `id` | 见字段规则 #3 | — | 主键 |
| `create_at` | TIMESTAMPTZ | NOW() | 创建时间，插入时写入 |
| `create_by` | VARCHAR(64) | `'default'` | 创建人，引用 `pc_users.id`（账户域系统用户表）；未接入鉴权时回落 `'default'` |
| `update_at` | TIMESTAMPTZ | NOW() | 更新时间，每次写操作刷新 |
| `update_by` | VARCHAR(64) | `'default'` | 更新人，引用 `pc_users.id`；未接入鉴权时回落 `'default'` |
| `is_deleted` | SMALLINT | 0 | 逻辑删除：0 正常 / 1 删除 |

规则：
- `create_at`/`update_at` 由应用层维护；`update_at` 每次 UPDATE 刷新。
- `create_by`/`update_by` 引用 **`pc_users`**（账户域系统用户表；其命名同样遵循 `pc_` 前缀约定）。**不建硬外键**（跨域耦合 + 本项目既有风格为逻辑外键），仅按 `{用户实体}_id` 命名并建索引，取值合法性由应用层保证。
- `is_deleted` 为逻辑删除标志。**所有列表/详情查询必须带 `WHERE is_deleted = 0`**，否则会读出已删除行；物理删除禁用（除 DBA 运维）。

**表名规则**
- 格式：`pc_{domain}_{[submodule_]entity}`，全小写 `snake_case`。
  - `pc` = 产品命名空间（PrismClaw）；`{domain}` ∈ {`medical`, `legal`}。
  - `{entity}` **一律复数**（表是集合语义）。
- 知识库子模块统一 `kb_` 前缀：`kb_literatures` / `kb_guidelines` / `kb_drugs`。
- **禁用实现/组件名**：表名描述"它是什么"，不描述"谁拥有它"。
- **禁冗余**：去掉与域前缀重复的单词（`medical_records` → `records`，避免 `pc_medical_medical_records`）。
- 域词义锁定（消除歧义）：`patient_reports`=患者全部临床文档（上传报告 + AI 抽取结果统一于此）；`records` 语义归入 `patient_reports`，不单独建表。

**字段名规则**
1. `snake_case`、小写、ASCII。
2. **缩写白名单**（其余一律写全）：`id`、`kb`(knowledge_base)、`tnm`(Tumor-Node-Metastasis)、`ecog`、`doi`、`url`、`json`(JSONB 列后缀)。
3. 主键 `id`：URL 可达实体用 `VARCHAR(64)` 短码；内部/事务实体用 UUID。
4. 外键 `{被引用实体单数}_id`（`patient_id`）。**子表必须显式持有父表外键**。
5. 生命周期/状态列统一叫 **`status`**（枚举值集中文档化）；禁止 `stage`/`state`/`parse_state` 混用。临床分期等"属性"列不受限（`tnm_stage`/`clinical_stage`/`treatment_status` 是临床概念，保留描述性命名）。
6. 类型判别列统一叫 **`type`**（不用 `kind`）；文档类型判别列叫 `report_type`。
7. 时间戳：基础字段 `create_at`/`update_at` 见上方；其余**语义日期**用 `{事件}_date`（`birth_date`/`visit_date`），**事件时刻**用 `{事件}_at`（`started_at`/`finished_at`）。
8. 布尔列加 **`is_`** 前缀（`is_essential`）。
9. 结构化/数组列统一 **`JSONB`** + **`_json`** 后缀（禁止 `TEXT` 存 JSON 串）。

**PHI 列规约**（沿用既有三道防线）：脱敏展示值后缀 `_masked`，可检索哈希值后缀 `_hash`（HMAC + pepper）。

### 2.1.1 表全景

| 表名 | 状态 | 承载的视图模型 | 消费方 |
|---|---|---|---|
| `pc_medical_patients` | **合并单表**（`patients` + `patients_extended`） | `PatientProfile` | 患者列表 / 患者详情 |
| `pc_medical_patient_reports` | **统一表**（`patient_reports` + `processing_tasks` + `records`） | `PatientReport` / `MedicalRecord`（同一行的两种视图） | 病历页列表 / 队列 / 患者详情「报告解析」卡 |
| `pc_medical_consultations` | 业务表 | `Consultation` | 患者详情 AI 咨询 |
| `pc_medical_kb_literatures` | 业务表 | `LiteratureEntry` | 文献 Tab |
| `pc_medical_kb_guidelines` | 业务表 | `GuidelineEntry` | 指南 Tab |
| `pc_medical_kb_drugs` | 业务表 | `DrugEntry` | 药品 Tab |
| `pc_medical_briefs` | 业务表 | `MedicalBrief` | 病历摘要（详情页侧板） |
| `v_patient_timeline`（视图，非表） | **派生** | `PatientProfile.timeline` | 患者详情时间线 |

**`report_type` 枚举（临床文档类型，16 值；按 §0.1 真实报告验证 + D16 国标验证收敛）**：

| 值 | 含义 | 来源 |
|---|---|---|
| `outpatient` | 门诊记录 | 初始 |
| `admission` | 入院记录（含 24 小时入出院记录） | 初始 |
| `exam` | 功能检查报告（心电/脑电/肺功能/内镜等；不含影像和检验） | 初始（D17 标签修正） |
| `imaging` | 影像报告 | 初始 |
| `biochemistry` | 生化/检验报告 | 初始 |
| `pathology` | 病理报告 | 初始 |
| `pathology_supplement` | 病理补充报告 | 初始 |
| `pathology_consult` | 病理会诊意见书 | 初始 |
| `gene_test` | 基因/分子检测（如 TROP2、HER2、NGS） | D14（真实验证） |
| `surgery` | 手术/操作记录 | 初始 |
| `diagnosis_cert` | 疾病诊断证明书 | 初始 |
| `discharge` | 出院记录 | 初始 |
| `family_note` | 家属自录（手绘病程表、Excel 疗程记录等非官方材料） | D14（真实验证） |
| `progress_note` | 病程记录（住院病程/首次病程/日常病程） | D16（国标 WS 445.13） |
| `prescription` | 处方/医嘱（出院带药单、门诊处方、住院医嘱） | D16（国标 WS/T 500.4-5） |
| `treatment_record` | 治疗记录（化疗执行单、放疗计划、免疫治疗记录等） | D16（国标 WS/T 500.8） |

### 2.2 DDL

所有表名统一带 `pc_medical_` 前缀。DDL 位于 `deploy/sql/init_medical.sql`，域激活时由 `ensure_medical_schema()` 调用 `apply_domain_schema(engine, "medical")` 执行；语句幂等，advisory lock 由通用执行器按文件持有。

**项目未上线，直接按最终结构建表（无 ALTER 迁移）**。

时间列统一 `TIMESTAMPTZ`（带时区），与 `deploy/sql/init_medical.sql` 一致。

#### `pc_medical_patients`（合并档案 + 画像）

```sql
CREATE TABLE IF NOT EXISTS pc_medical_patients (
    id                VARCHAR(64)  PRIMARY KEY,
    name              VARCHAR(64)  DEFAULT '',
    gender            VARCHAR(8)   DEFAULT '',
    birth_date        VARCHAR(16)  DEFAULT '',     -- 推算 age，不落库
    blood_type        VARCHAR(8)   DEFAULT '',
    id_type           VARCHAR(16)  DEFAULT '',     -- 证件类型：身份证/护照/…
    id_number_masked  VARCHAR(32)  DEFAULT '',
    phone_masked      VARCHAR(32)  DEFAULT '',
    id_number_hash    VARCHAR(64)  DEFAULT '',     -- HMAC，精确搜索用
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
    medications_json  JSONB        DEFAULT '[]',       -- 聚合态：当前用药列表（由多条报告合并而来）
    labs_json         JSONB        DEFAULT '[]',
    attending_doctor  VARCHAR(64)  DEFAULT '',
    tags_json         JSONB        DEFAULT '[]',
    create_at         TIMESTAMPTZ  DEFAULT NOW(),
    create_by         VARCHAR(64)  DEFAULT 'default',  -- 引用 pc_users.id
    update_at         TIMESTAMPTZ  DEFAULT NOW(),
    update_by         VARCHAR(64)  DEFAULT 'default',  -- 引用 pc_users.id
    is_deleted        SMALLINT     DEFAULT 0            -- 逻辑删除：0 正常 / 1 删除
);
CREATE INDEX IF NOT EXISTS ix_pc_medical_patients_department      ON pc_medical_patients (department);
CREATE INDEX IF NOT EXISTS ix_pc_medical_patients_risk_level      ON pc_medical_patients (risk_level);
CREATE INDEX IF NOT EXISTS ix_pc_medical_patients_id_number_hash  ON pc_medical_patients (id_number_hash);
CREATE INDEX IF NOT EXISTS ix_pc_medical_patients_phone_hash      ON pc_medical_patients (phone_hash);
CREATE INDEX IF NOT EXISTS ix_pc_medical_patients_is_deleted     ON pc_medical_patients (is_deleted);
```

字段 → 视图映射：

| 视图字段 | 来源 | 备注 |
|---|---|---|
| `age` | `birth_date` 推算 | **不落库**，派生字段 |
| `idNumber` | `id_number_masked` | 形如 `110105********2214` |
| `phone` | `phone_masked` | 形如 `138****2361` |
| `diagnosis` | `primary_diagnosis` | |
| `stage` | `tnm_stage` | 空则回落 `clinical_stage` |
| `status` | `treatment_status` | |
| `timeline` | 派生视图 `v_patient_timeline` | 见 §2.1.1 |

#### `pc_medical_patient_reports`（统一临床文档表）

```sql
CREATE TABLE IF NOT EXISTS pc_medical_patient_reports (
    id               VARCHAR(64)  PRIMARY KEY,
    patient_id       VARCHAR(64),                     -- 允许 NULL：AI 未识别患者时不绑定
    report_type      VARCHAR(32)  DEFAULT 'outpatient', -- 判别列：见 §2.1.1 枚举（16 值）
    file_name        VARCHAR(256) NOT NULL,
    file_type        VARCHAR(16)  DEFAULT 'text',     -- 展示图标：text/image/audio
    file_path        TEXT         DEFAULT '',         -- 存储 URL（首图/主文件；多页见 attachments_json）
    mime_type        VARCHAR(128) DEFAULT '',
    attachments_json JSONB        DEFAULT '[]',       -- 多页/多图归组：[{path, mime, page_no}]
    hospital_name    VARCHAR(128) DEFAULT '',         -- 报告来源医院（如 湖南省肿瘤医院）
    doctor_name      VARCHAR(64)  DEFAULT '',         -- 报告署名医师/开方医生
    patient_name_raw VARCHAR(64)  DEFAULT '',         -- OCR 原文患者姓名（匹配前）
    match_reason     VARCHAR(32)  DEFAULT '',         -- 患者匹配依据（强/弱/未匹配）
    status           VARCHAR(16)  DEFAULT '排队中',    -- 生命周期列统一 status（枚举见 §2.3）
    progress         SMALLINT     DEFAULT 0,
    retry_count      SMALLINT     DEFAULT 0,
    error_message    TEXT         DEFAULT '',
    raw_text         TEXT         DEFAULT '',         -- OCR/原始抽取文本
    visit_date       DATE,                             -- 文档所述就诊日期（语义日期）
    department       VARCHAR(64)  DEFAULT '',
    diagnosis        VARCHAR(256) DEFAULT '',         -- AI 抽取诊断
    narrative_json   JSONB        DEFAULT '[]',       -- 结构化叙事，sections 骨架见 §2.2.2
    labs_json        JSONB        DEFAULT '[]',       -- 检验项，item schema 见 §2.2.1
    medications_json JSONB        DEFAULT '[]',       -- 快照态：该份报告提到的药物
    tags_json        JSONB        DEFAULT '[]',
    summary          TEXT         DEFAULT '',         -- AI 摘要
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ,
    create_at        TIMESTAMPTZ  DEFAULT NOW(),
    create_by        VARCHAR(64)  DEFAULT 'default',  -- 引用 pc_users.id
    update_at        TIMESTAMPTZ  DEFAULT NOW(),
    update_by        VARCHAR(64)  DEFAULT 'default',  -- 引用 pc_users.id
    is_deleted       SMALLINT     DEFAULT 0            -- 逻辑删除：0 正常 / 1 删除
);
CREATE INDEX IF NOT EXISTS ix_pc_medical_patient_reports_patient_id  ON pc_medical_patient_reports (patient_id);
CREATE INDEX IF NOT EXISTS ix_pc_medical_patient_reports_status     ON pc_medical_patient_reports (status);
CREATE INDEX IF NOT EXISTS ix_pc_medical_patient_reports_report_type ON pc_medical_patient_reports (report_type);
CREATE INDEX IF NOT EXISTS ix_pc_medical_patient_reports_is_deleted ON pc_medical_patient_reports (is_deleted);
```

#### 2.2.1 `labs_json` 检验项 schema（AI 抽取契约，形态依据 §0.1 真实数据）

每个元素固定六键，**缺测键为 `null`，不得发明新键**：

```json
[
  {
    "item":      "白细胞",
    "value_text": "8.98",
    "value_num":  8.98,
    "unit":      "*10~9/L",
    "ref_text":  "4.00-10.00",
    "flag":      ""
  },
  {
    "item":      "超敏肌钙蛋白T",
    "value_text": "<10.00",
    "value_num":  null,
    "unit":      "pg/mL",
    "ref_text":  "0-14",
    "flag":      ""
  }
]
```

| 键 | 类型 | 规则 |
|---|---|---|
| `item` | string | 检验项目名，恒有 |
| `value_text` | string | 结果**原文恒存**——承载 `<10.00`（上限型）、`+++`（分级）、`40%`（比例）等非纯数值形态 |
| `value_num` | number \| null | `value_text` 可无损解析为数值时填，否则 `null`（**趋势图只消费此键**） |
| `unit` | string | 单位原样（`*10~9/L` / `NG/ML` 大小写照抄原报告） |
| `ref_text` | string | 参考范围**只存原文**（`0-3.3` / `<3.7` / `≤4.0` / `120-160`），不拆 min/max——真实参考值形态无法穷举，拆解交给检索期按需解析 |
| `flag` | string | 异常标记原样：`↑` / `↓`；正常留空串。报告中另有 ◆ 重点标记，属于版式符号不落 `flag` |

> 反面约束：禁止把 `flag` 归一化为 `high`/`low`（丢失原文语义）、禁止为参考范围建 `ref_min`/`ref_max` 列（`≤`/`<`/开区间无法无损还原）。

#### 2.2.2 `narrative_json` 叙事骨架契约（AI 抽取契约，D17）

不同 `report_type` 的叙事结构完全不同（出院记录=主诉+现病史+诊疗经过+出院医嘱；病理=标本+镜下+诊断+注释；影像=检查方法+所见+印象），但前端只需一个通用 `SectionList` 组件——因此定义**最小公共骨架**，不做 report_type 特化。

每个元素固定两键，**缺测键为 `null`，不得发明新键**：

```json
{
  "sections": [
    { "heading": "主诉", "body": "咳嗽伴痰中带血2月" },
    { "heading": "现病史", "body": "患者2月前无明显诱因出现咳嗽…" },
    { "heading": "诊疗经过", "body": "入院后予白蛋白紫杉醇+卡铂+替雷利珠方案化疗…" },
    { "heading": "出院医嘱", "body": "1. 继续口服吉非替尼 250mg qd…" }
  ]
}
```

| 键 | 类型 | 规则 |
|---|---|---|
| `sections` | array | 顶层键，恒有（空文档为 `[]`） |
| `sections[].heading` | string | 段落标题（如"主诉"/"镜下描述"/"检查所见"），恒有 |
| `sections[].body` | string | 该段落的纯文本内容，恒有（空段为 `""`） |

> 反面约束：禁止在 section 层定义 report_type 特化字段（如 `specimen`/`impression`）——那等于在 JSONB 里重建 16 种子表，违背"通用渲染"初衷。特化信息留在 `labs_json`/`medications_json` 等专项列。

> `narrative_json` 旧值 `DEFAULT '[]'` 在此契约下有两种合法形态：空文档 `[]`（无 sections）或含 sections 的对象 `{sections: [...]}`。AI 抽取统一输出对象形态；空值兜底保持 `[]`（前端判断 `Array.isArray → 无叙事` / `obj.sections → 渲染 SectionList`）。

> 此表同时承载「上传报告」（元数据 + `status` 流水线）与「病历」（AI 抽取结果），1:1 同生。列表/详情/队列/时间线均从该表的不同投影读出。

#### 知识库三表

```sql
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
    create_by     VARCHAR(64)  DEFAULT 'default',  -- 引用 pc_users.id
    update_at     TIMESTAMPTZ  DEFAULT NOW(),
    update_by     VARCHAR(64)  DEFAULT 'default',  -- 引用 pc_users.id
    is_deleted    SMALLINT     DEFAULT 0            -- 逻辑删除：0 正常 / 1 删除
);
CREATE INDEX IF NOT EXISTS ix_pc_medical_kb_literatures_category   ON pc_medical_kb_literatures (category);
CREATE INDEX IF NOT EXISTS ix_pc_medical_kb_literatures_year       ON pc_medical_kb_literatures (year);
CREATE INDEX IF NOT EXISTS ix_pc_medical_kb_literatures_is_deleted ON pc_medical_kb_literatures (is_deleted);

CREATE TABLE IF NOT EXISTS pc_medical_kb_guidelines (
    id           VARCHAR(64) PRIMARY KEY,
    title        VARCHAR(512) NOT NULL,
    title_cn     VARCHAR(512) DEFAULT '',
    doc_type     VARCHAR(16)  DEFAULT '',
    authors      VARCHAR(512) DEFAULT '',
    specialty    VARCHAR(64)  DEFAULT '',
    region       VARCHAR(16)  DEFAULT '',
    published_at TIMESTAMPTZ,
    org          VARCHAR(32)  DEFAULT '',
    category     VARCHAR(32)  DEFAULT '',
    year         INTEGER,
    version      VARCHAR(32)  DEFAULT '',
    abstract     TEXT         DEFAULT '',
    points_json  JSONB        DEFAULT '[]',
    source       VARCHAR(32)  DEFAULT 'seed',
    create_at    TIMESTAMPTZ  DEFAULT NOW(),
    create_by    VARCHAR(64)  DEFAULT 'default',  -- 引用 pc_users.id
    update_at    TIMESTAMPTZ  DEFAULT NOW(),
    update_by    VARCHAR(64)  DEFAULT 'default',  -- 引用 pc_users.id
    is_deleted   SMALLINT     DEFAULT 0            -- 逻辑删除：0 正常 / 1 删除
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
    create_by            VARCHAR(64)  DEFAULT 'default',  -- 引用 pc_users.id
    update_at            TIMESTAMPTZ  DEFAULT NOW(),
    update_by            VARCHAR(64)  DEFAULT 'default',  -- 引用 pc_users.id
    is_deleted           SMALLINT     DEFAULT 0            -- 逻辑删除：0 正常 / 1 删除
);
CREATE INDEX IF NOT EXISTS ix_pc_medical_kb_drugs_category   ON pc_medical_kb_drugs (category);
CREATE INDEX IF NOT EXISTS ix_pc_medical_kb_drugs_is_deleted ON pc_medical_kb_drugs (is_deleted);

CREATE TABLE IF NOT EXISTS pc_medical_consultations (
    id          VARCHAR(64)   PRIMARY KEY,
    session_id  VARCHAR(128)  NOT NULL UNIQUE,      -- 会话域 session_key
    patient_id  VARCHAR(64)   NOT NULL,
    doctor_id   VARCHAR(64)   NOT NULL,
    title       VARCHAR(256)  DEFAULT '',
    created_at  TIMESTAMPTZ   DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_med_cons_patient
    ON pc_medical_consultations (patient_id, created_at DESC);

CREATE TABLE IF NOT EXISTS pc_medical_briefs (
    id                 VARCHAR(64)  PRIMARY KEY,
    patient_id         VARCHAR(64)  NOT NULL,
    report_ids_json    JSONB        DEFAULT '[]',   -- 消费的报告 ID 列表
    status             VARCHAR(16)  DEFAULT '分析中', -- 分析中/已通过/待修正/已废弃
    version            SMALLINT     DEFAULT 1,       -- 重新分析递增
    director_plan_json JSONB        DEFAULT '{}',   -- 主任规划输出
    sections_json      JSONB        DEFAULT '[]',   -- 简报正文（含引用）
    citations_json     JSONB        DEFAULT '[]',   -- 统一引用表
    audit_result_json  JSONB        DEFAULT '{}',   -- 审计结果
    error_message      TEXT         DEFAULT '',
    started_at         TIMESTAMPTZ,
    finished_at        TIMESTAMPTZ,
    create_at          TIMESTAMPTZ  DEFAULT NOW(),
    create_by          VARCHAR(64)  DEFAULT 'default',
    update_at          TIMESTAMPTZ  DEFAULT NOW(),
    update_by          VARCHAR(64)  DEFAULT 'default',
    is_deleted         SMALLINT     DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_pc_medical_briefs_patient_id
    ON pc_medical_briefs (patient_id);
CREATE INDEX IF NOT EXISTS ix_pc_medical_briefs_status
    ON pc_medical_briefs (status);
CREATE INDEX IF NOT EXISTS ix_pc_medical_briefs_is_deleted
    ON pc_medical_briefs (is_deleted);
```

#### `v_patient_timeline`（派生视图，非表）

```sql
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
```

> 时间线不存表：它是 `patient_reports` 按 `patient_id` 聚合的投影。`GET /patients/{id}` 内嵌的时间线即对该视图 `WHERE patient_id = :id` 的查询；时间线无独立写入方（D10）。

**所有查询须带 `WHERE is_deleted = 0`**：列表/详情/统计接口默认过滤已删除行（物理删除禁用，仅 DBA 运维）。

**为什么 `pc_users` 不建硬外键**：跨域外键会把医疗域与账户域在 DDL 层耦合，且违反既有"逻辑外键 + 应用层保证"风格；`create_by`/`update_by` 仅按 `{用户实体}_id` 命名并建索引，取值合法性由应用层校验。

**为什么新建表的 `is_deleted` 默认 0 而非 NULL**：NULL 会让 `WHERE is_deleted = 0` 漏行（NULL ≠ 0），统一默认 0 保证过滤安全。

### 2.3 `pc_medical_patient_reports` 状态机（含上传任务流水线）

```
                  ┌──────────── 已解析 (progress=100, finished_at)
                  │
排队中 ──► OCR识别 ──► 结构化提取 ──┤
  │                                  │
  │        ┌──── 语音转写 ───────────┘
  │        │
  └────────┴──► 失败 (error_message, retry_count+1)
```

- `status` 枚举值：`排队中` / `OCR识别` / `结构化提取` / `语音转写` / `已解析` / `失败`（生命周期列统一 `status`）
- 分支依据 `file_type`：`语音` → `语音转写`；其余 → `OCR识别` → `结构化提取`
- 终态：`已解析` / `失败`
- `elapsed` 不落库，由 `started_at`/`finished_at` 现算：`未开始 → '--:--'`、`已解析 → finished_at - started_at`、`进行中 → now - started_at`

**Phase 1 执行方式**：`POST /records/upload` 用 FastAPI `BackgroundTasks` 同步推进（各阶段推进即 UPDATE `status`/`progress`）。进程重启时，启动时把残留的 `排队中/进行中` 统一置为 `失败（进程重启，需重试）`。表结构已预留 `retry_count`/`started_at`/`finished_at`，Phase 2 换独立 worker 不动 schema。

### 2.4 脱敏策略

```python
def mask_id_number(raw: str) -> str:
    if len(raw) < 10:
        return "*" * len(raw)
    return f"{raw[:6]}{'*' * 8}{raw[-4:]}"      # 110105********2214

def mask_phone(raw: str) -> str:
    if len(raw) < 7:
        return "*" * len(raw)
    return f"{raw[:3]}{'*' * 4}{raw[-4:]}"      # 138****2361

def hmac_index(raw: str) -> str:                 # 精确搜索用
    return hmac.new(settings.phi_pepper, raw.encode(), hashlib.sha256).hexdigest()
```

三道防线，互不替代：

1. **入库即脱敏**（`_masked` 列）—— 数据库被拖库也不泄漏完整 PHI
2. **HMAC 哈希列** —— 让"按证件号/手机号精确搜索"成立，pepper 与库分离则哈希列不可反推
3. **`PHIRedactHook`**（既有的 `PRE_TOOL` 钩子）—— 管的是 Agent 工具**出站**参数，与本节的**入库**脱敏是不同环节

关键词搜索的执行顺序：先用 `LIKE` 匹配 `name` / `id_number_masked` / `phone_masked` 前缀；输入长度 ≥ 15（证件号）或 = 11（手机号）时，额外用哈希列做等值匹配。

### 2.5 知识库数据来源

知识库三个 Tab 均有**唯一明确来源**，不设工具内字典兜底：

| Tab | 目标表 | 数据来源 | 条数 |
|---|---|---|---|
| 搜文献 | `pc_medical_kb_literatures` | `persistence/seeds/kb.py` 的 `_LITERATURE_SEEDS`（真实期刊文献） | 12 |
| 搜指南 | `pc_medical_kb_guidelines` | `tools/ingest_guidelines.py` 解析真实 PDF 写入，`source='import:pdf'` | 22 |
| 搜药品 | `pc_medical_kb_drugs` | `persistence/seeds/kb.py` 的 `_DRUG_SEEDS`（西药/中成药三级分类目录） | 92 |

- 文献与药品由 `backend/tools/seed_kb.py` 手工回填（`seed_kb_data()` 无启动期自动调用点）。
- 指南**只**来自 PDF 导入；种子模块不含虚构指南，避免与真实指南混排。
- Agent 侧 `search_guideline` / `search_literature` / `search_drug` 三个工具统一经 `KbStore` 读上述表，不持任何内置数据集。

ID 生成规则（保证可幂等重跑）：

| 表 | ID 规则 | 示例 |
|---|---|---|
| `pc_medical_patients` | `P{seq:04d}` | `P0001` |
| `pc_medical_kb_literatures` | DOI 小写、去 `/` 与 `.` | `101056nejmoa2500123` |
| `pc_medical_kb_guidelines` | `{org}-{slug}-{year}`（导入时由解析器生成） | `nccn-gastric-2026` |
| `pc_medical_kb_drugs` | 有详情的沿用原 id，其余 name+路径哈希 | `pembrolizumab` |
| `pc_medical_patient_reports` | `uuid4()` | 内部实体，不入 URL |

---

## 3. 接口设计

### 3.1 通用约定

**Base path**：`/api/medical`（沿用现有挂载点，不引入 `/v1`——旧响应体形状零消费方，改形状无破坏性）。

**命名**：请求/响应一律 **camelCase**。实现方式（Pydantic v2）：

```python
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

class ViewModel(BaseModel):
    """所有医疗域响应模型的基类：内部 snake_case，序列化输出 camelCase。"""
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        protected_namespaces=(),
    )
```

FastAPI 默认 `response_model_by_alias=True`，响应自动为 camelCase；`populate_by_name=True` 让请求体同时接受 camelCase 与 Python 字段名。

**分页**：列表端点统一返回

```ts
interface Paged<T> {
  items: T[]
  total: number
  page: number
  pageSize: number
}
```

**错误**：沿用项目既有约定（非 2xx 时 `{ detail: string }`），不新增自定义错误信封。

**用户身份**：`GET`/`PUT` 从请求上下文取 `user_id`；未接入鉴权时回落 `'default'`。

### 3.2 端点清单

#### A. 患者总览页（`patients`）

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/medical/patients` | 分页列表，返回 `PatientProfile[]` |
| `GET` | `/api/medical/patients/stats` | 统计条四项指标 |
| `GET` | `/api/medical/patients/{patientId}` | 详情（含 `timeline`，来自 `v_patient_timeline`） |
| `GET` | `/api/medical/patients/{patientId}/reports` | 患者「报告解析」卡列表 |

`GET /api/medical/patients`

```
Query: keyword?  gender?  risk?  page=1  pageSize=8
Response 200:
{
  "items": [
    {
      "id": "P0001", "name": "王建国", "age": 58, "gender": "男",
      "idNumber": "110105********2214", "phone": "138****2361",
      "department": "胸部肿瘤内科", "diagnosis": "肺腺癌 IIIB期", "stage": "T3N2M0",
      "status": "化疗中", "cycle": "第2周期", "risk": "高危",
      "vitals": { "hr": 82, "bp": "135/85", "temp": 36.5, "spo2": 96 },
      "biomarkers": { "CEA": "45.2 ng/mL", "PD-L1": "TPS 80%", "EGFR": "Exon19del" },
      "allergies": ["青霉素"], "history": ["高血压5年", "吸烟史30年"],
      "medications": ["培美曲塞 500mg/m²"],
      "labs": [{ "item": "白细胞", "value": "3.2×10⁹/L", "abnormal": true }],
      "timeline": [{ "date": "01-15", "event": "入院，制定化疗+免疫方案" }]
    }
  ],
  "total": 10, "page": 1, "pageSize": 8
}
```

- `keyword` 命中：`name` / `phone_masked` / `id_number_masked` / `primary_diagnosis` / `tnm_stage`（与前端现有过滤逻辑逐字对齐）
- `gender` / `risk`：`'全部'` 不过滤
- **不提供 `status` 过滤**：本产品无 HIS 数据源，治疗状态来自人工录入，不作为筛选维度
- 列表直接返回完整 `PatientProfile`。量级约 1KB/条 × 8 条 = 8KB，可接受。**超过 200 名建档患者时需拆出 `PatientListItem`**（表格只用到 10 个字段，详情页才需要全部）

`GET /api/medical/patients/stats`

```
Response 200:
{
  "archivedPatients": 24,
  "archivedMonthlyDelta": 3,
  "highRisk": 3,
  "reportsToday": 46,
  "reportsParsed": 44,
  "reportsParsing": 2
}
```

只返回数字，`sub` 文案（"来院咨询 · 待初筛"、"需优先关注"）留在前端——那是文案不是数据。

#### B. 智能病历页（`records`）

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/medical/records` | 分页列表，返回 `PatientReport[]` |
| `GET` | `/api/medical/records/queue` | 整理队列 + 页脚汇总（读 `patient_reports.status`） |
| `POST` | `/api/medical/records/upload` | multipart 上传，替换前端 `setTimeout(2500)` |

`GET /api/medical/records?keyword=&page=1&pageSize=6`
`keyword` 命中 `pc_medical_patients.name` + `pc_medical_patient_reports.diagnosis`（与前端现有逻辑一致）。**不提供 `status` 过滤**——当前 UI 没有该筛选器。

`GET /api/medical/records/queue`

```
Response 200:
{
  "tasks": [
    { "id": "...", "file": "赵德明-胸部CT报告.pdf", "type": "PDF",
      "stage": "OCR识别", "progress": 68, "elapsed": "00:04:21" }
  ],
  "summary": { "doneToday": 26, "failed": 0, "retried": 0 }
}
```

前端每 **3s 轮询**（队列规模在 10 量级，不值得为此开 WebSocket 通道）。`running` 计数前端自己从 `tasks` 算。

`POST /api/medical/records/upload`

```
Request:  multipart/form-data，字段名 files，可多文件
Response 202: { "reportIds": ["<uuid>", "..."] }
```

服务端行为：每个文件 → 写一条 `pc_medical_patient_reports`（`status='排队中'`，`file_type` 按扩展名判定）→ `BackgroundTasks` 推进流水线 → 完成后回写 `status='已解析'` + AI 抽取字段。无独立 `processing_tasks` 表（D11）。

> 不创建 `GET /api/medical/records/{id}`：列表已返回完整 `PatientReport`，无独立详情消费方。将来列表改为返回摘要时再加。

#### C. 知识库（单页三 Tab：`literature` / `guidelines` / `drugs`）

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/medical/kb/literature` | 文献列表（按发布时间倒序） |
| `GET` | `/api/medical/kb/guidelines` | 指南列表（按发布时间倒序） |
| `GET` | `/api/medical/kb/drugs` | 药品列表（前端按分类树组目录，一次取全） |

```
GET /api/medical/kb/literature?keyword=&page=1&pageSize=20

{
  "items": [ { "id": "...",
               "titleEn": "...", "titleCn": "...", "studyType": "RCT",
               "journal": "N Engl J Med",
               "journalTags": ["独家原文", "TOP期刊", "JCR Q1", "中科院1区"],
               "impactFactor": 96.2, "publishedAt": "2025-11-18T08:00:00Z",
               "authors": "...", "abstract": "...", "doi": "..." } ],
  "total": 12, "page": 1, "pageSize": 20
}
```

- `guidelines` 额外接 `sort=year|name`；`drugs` 额外接 `essentialOnly=bool`
- 列表不返回 facet 计数：筛选芯片由前端按当前数据派生（期刊 / 研究类型 / 类型 / 地区）
- 药品目录字段：`kind`（西药/中成药）、`atcL1`、`atcL2`、`atcGroup`、`atcGroupDesc`

#### D. 患者 AI 咨询（`consultations`）

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/medical/consultations/` | 登记会话（前端先调核心建会话拿 `session_id`，再登记） |
| `GET` | `/api/medical/consultations/` | 当前医生咨询列表，可选 `patient_id` 过滤 |
| `GET` | `/api/medical/consultations/{session_id}` | 单条咨询（工具入口校验用） |

```
POST /api/medical/consultations/
Request:  { "session_id": "...", "patient_id": "P0001", "title": "..." }
Response: { "id": "...", "sessionId": "...", "patientId": "P0001",
            "doctorId": "...", "title": "...", "createdAt": "..." }
```

- 归属校验：患者档案 `create_by` 必须是当前医生，否则 403
- 幂等：`session_id` 已登记则直接返回既有行
- 会话上下文由 `SESSION_START` 钩子 `ConsultationContextHook` 注入，Agent 经三个只读工具取患者数据
- 无 `POST /api/medical/agent/chat` 端点（AI 咨询走 `/consultations/*`，见 D8）

#### E. 病历摘要（`briefs`）

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/medical/briefs/` | 摘要列表（当前医生，可按 `patient_id` 过滤） |
| `GET` | `/api/medical/briefs/{brief_id}` | 摘要详情（含 sections / citations） |
| `POST` | `/api/medical/briefs/` | 创建摘要分析任务 |
| `POST` | `/api/medical/briefs/{brief_id}/reanalyze` | 重新分析 |
| `DELETE` | `/api/medical/briefs/{brief_id}` | 删除摘要 |

### 3.3 不新增端点的两处交互

| 交互 | 现状 | 处置 |
|---|---|---|
| 复制摘要 | 纯客户端 Clipboard API | 不动 |
| 导出一页纸 | 只弹 toast「已加入导出队列」，无后端调用 | **Phase 1 改为前端 Blob 下载**（`.md`），toast 文案同步改为「一页纸总结已导出」 |

「导出一页纸」不新增端点的理由：内容由 `lib/format.ts` 的 `buildPatientSummary(p)` 生成，输入数据已在客户端内存里，为了下载一段文本往返服务端没有收益。

若后续需要带医院抬头/样式的 PDF 或 Word，再引入 `GET /api/medical/patients/{patientId}/one-pager` —— 复用 `pc_medical_patient_reports` 的异步生成模式，届时表结构不变。

### 3.4 接口总览（24 个）

| # | 方法 | 路径 |
|---|---|---|
| 1 | GET | `/api/medical/patients` |
| 2 | POST | `/api/medical/patients` |
| 3 | GET | `/api/medical/patients/stats` |
| 4 | GET | `/api/medical/patients/{patientId}` |
| 5 | GET | `/api/medical/patients/{patientId}/reports` |
| 6 | PATCH | `/api/medical/patients/{patientId}/reports/{reportId}` |
| 7 | GET | `/api/medical/records` |
| 8 | GET | `/api/medical/records/inbox` |
| 9 | GET | `/api/medical/records/queue` |
| 10 | POST | `/api/medical/records/upload` |
| 11 | POST | `/api/medical/records/{reportId}/reparse` |
| 12 | POST | `/api/medical/records/{reportId}/assign` |
| 13 | GET | `/api/medical/records/{reportId}/attachments/{index}` |
| 14 | GET | `/api/medical/kb/literature` |
| 15 | GET | `/api/medical/kb/guidelines` |
| 16 | GET | `/api/medical/kb/drugs` |
| 17 | POST | `/api/medical/consultations/` |
| 18 | GET | `/api/medical/consultations/` |
| 19 | GET | `/api/medical/consultations/{session_id}` |
| 20 | GET | `/api/medical/briefs/` |
| 21 | GET | `/api/medical/briefs/{brief_id}` |
| 22 | POST | `/api/medical/briefs/` |
| 23 | POST | `/api/medical/briefs/{brief_id}/reanalyze` |
| 24 | DELETE | `/api/medical/briefs/{brief_id}` |

---

## 4. 前端契约要点

- **`types/view.ts` 是唯一类型源**：后端直出 camelCase 视图模型，前端不设 snake_case 契约层；`api/client.ts` 是薄封装，返回值直接是 `view.ts` 类型，保留既有超时 / AbortController / 请求去重机制。
- **实体 `id` 为 `string` 短码**：`PatientProfile` / `PatientReport` / `LiteratureEntry` / `GuidelineEntry` / `DrugEntry` 五个类型（`P0001` / `pembrolizumab`），路由 `patientId` 同为 `string`。
- **列表统一 `Paged<T>`**；队列与统计条用 `RecordsQueueResponse` / `PatientStats`。
- **`PatientReport` 同时承载报告与病历**：同一行的两种视图，含 `reportType`（D13）。
- **`LabItem` 六键对齐后端**：`{item, valueText, valueNum, unit, refText, flag}`，`abnormal` 由 `flag !== ''` 派生。
- **`medications` 分两级**：患者档案为 `string[]` 聚合摘要，报告级为结构化 `MedicationItem[]`。
- **`narrative_json` 由 `SectionList` 渲染**：消费统一骨架 `sections[{heading, body}]`，一个组件覆盖全部 `report_type`。
- **数据源替换不改渲染层**：页面 JSX 与 `features/*` 展示组件保持不变，只在取数处替换——「后端直出视图模型」的收益。

