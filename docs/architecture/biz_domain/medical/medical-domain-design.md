# 医疗域（subpackages.medical）详细设计文档

> 本文档是 domain-bundle-design.md 的域特化展开。
> 通用机制（执行模型、DomainBundle Protocol、故障隔离、样式规范等）参见总纲，本文档只描述医疗域的专属设计。

> 定位：面向**肿瘤与质子治疗**的临床智能化工作台
>
> 设计原则：只做 AI 擅长的事（信息提取、结构化整理、语义检索、推理判断），
> 不做物理/工程流程（治疗计划计算、剂量验证、设备QA）。
> 质子治疗相关内容作为智能病历的肿瘤专科子模块，聚焦适应症评估与信息管理。

---

## 一、功能设计

### 1.1 功能总览

医疗域分为 **3 个菜单入口（智能病历 / 患者管理 / 医学知识库）**，其中知识库内含文献/指南/药品三个 Tab；医学翻译仅作为 Agent 工具，不占菜单。

| 菜单名 | 页面路由 | 工具名 | REST 端点 | Agent 执行内容 |
|---|---|---|---|---|
| 智能病历 | `?domain=medical&page=records` | `smart_medical_record` | `POST /api/medical/records/upload` | 上传文件入队 → 后台解析 → 结构化字段 + AI 摘要；支持待归属队列与重新解析 |
| 患者管理 | `?domain=medical&page=patients` | `read_patient_profile` / `read_patient_reports` / `read_raw_report` | `GET /api/medical/patients` | 患者列表 / 统计 / 详情 / 报告；详情页内置 AI 咨询（三个只读工具供 Agent 取数） |
| 医学翻译 | 无独立入口 | `medical_translate` | —（仅 Agent 工具） | 中英互译 + 肿瘤学/放射医学术语对齐 |
| 医学知识库 · 搜指南 | `?domain=medical&page=knowledge&mw_kb=guidelines` | `search_guideline` | `GET /api/medical/kb/guidelines` | 检索真实导入的 NCCN / CSCO / 中华医学会指南，返回机构、学科、版本、摘要与核心推荐 |
| 医学知识库 · 搜药品 | `?domain=medical&page=knowledge&mw_kb=drugs` | `search_drug` | `GET /api/medical/kb/drugs` | 按药名/通用名检索说明书（适应症/禁忌/不良反应/相互作用） |
| 医学知识库 · 搜文献 | `?domain=medical&page=knowledge&mw_kb=literature` | `search_literature` | `GET /api/medical/kb/literature` | 检索知识库中的权威期刊文献（中英标题、研究类型、期刊、影响因子） |

> 三个检索工具均通过 `KbStore` 读 `pc_medical_kb_*` 真表，不持内置数据集；知识库为只读共享数据，三个 Tab 同属一个页面 `KnowledgePage`。

> **关于肿瘤专科能力**：本域不提供 TNM 分期自动提取、化疗方案模板、靶向药-突变匹配、质子适应症评估（无 `chemo_protocol` / `targeted_match` / `proton_eligibility` 工具）。模拟定位、治疗计划计算、剂量验证、设备 QA 属于物理/工程流程，明确不在 AI 能力范围内。

---

### 1.2 各功能详细设计

#### 1.2.1 智能病历

**REST API（异步解析流水线）**

```
POST /api/medical/records/upload     multipart/form-data  → 202
  files: UploadFile[]     上传文件（PDF / JPG / PNG / TXT / DOCX）
  patientId: string?      页内上传时带上，直接归属该患者

响应：
  { "reportIds": ["r_01H...", "r_01H..."] }

GET  /api/medical/records/?keyword=&reportType=&status=&page=1&pageSize=6
GET  /api/medical/records/queue                 解析队列（悬浮层轮询）
GET  /api/medical/records/inbox                 待归属报告
POST /api/medical/records/{id}/assign           { patientId }
POST /api/medical/records/{id}/reparse          → 202
GET  /api/medical/records/{id}/attachments/{i}  原件预览
```

单份报告视图模型（`report_to_vm`）主要字段：

| 字段 | 说明 |
|---|---|
| `id` / `patientId` / `fileName` / `fileType` / `mimeType` | 基础元信息 |
| `status` / `progress` / `retryCount` / `errorMessage` | 解析生命周期（排队中 / 解析中 / 已解析 / 失败） |
| `rawText` | OCR / 文本层抽取结果 |
| `visitDate` / `department` / `diagnosis` / `hospitalName` / `doctorName` | 从原文抽取的就诊信息 |
| `narrativeJson` / `labsJson` / `medicationsJson` / `tagsJson` | 结构化字段（JSONB） |
| `summary` | AI 摘要 |
| `attachmentsJson` | 多页/多图归组 |
| `patientNameRaw` / `matchReason` | 未归属时的指派线索（exact / ambiguous / no_match） |

**处理链路**

1. `upload` 落 `pc_medical_patient_reports`（`create_by` = 当前用户，`patient_id` 可空），立即返回 202
2. `BackgroundTasks` 执行 `_run_parse_then_maybe_analysis`：解析（文本层 / OCR / 视觉模型）→ 写结构化字段与摘要 → 状态置完成
3. 解析成功且已归属患者时，自动触发 `pc_medical_briefs` 分析流水线；分析失败不影响解析结果
4. 前端 `ParseQueueFloat` 轮询 `/queue` 展示进度；`/inbox` 提供人工指派兜底

**与 Agent 的关系**

`smart_medical_record` 工具仍保留：在对话里上传文件时由 Agent 调用完成解析与结构化，与页面上传共用同一 `MedicalStore`，写入同一张表。

#### 1.2.2 医学翻译

**无独立 REST 端点、无独立页面**，仅注册为 Agent 工具 `medical_translate`：

| 参数 | 类型 | 说明 |
|---|---|---|
| `text` | string | 必填，待翻译文本 |
| `direction` | string | `zh2en` / `en2zh` |
| `specialty` | string | 可选，`oncology` / `radiation_oncology` / `general` |

术语对照（如 NSCLC / IHC / PD-L1 / RBE / SOBT / IMPT / OAR / CTV / PTV）由工具内术语表提供，Agent 基于术语对照完成翻译并输出中英对照表。


#### 1.2.3 搜指南（医学知识库 Tab）

**REST API**（只读列表，前端单页三 Tab 之一）

```
GET /api/medical/kb/guidelines
Authorization: Bearer <jwt>

查询参数：
  keyword    string  检索关键词，匹配 title / title_cn / authors（如「胃癌」）
  category   string  分类筛选（"全部" 或具体类别）
  sort       string  排序："year"（默认，按发布时间倒序）| "name"
  page       int     页码，默认 1
  pageSize   int     每页条数，默认 8（上限 100）

响应：
{
  "items": [
    {
      "id": "nccn-gastric-2026",
      "title": "NCCN Clinical Practice Guidelines in Oncology: Gastric Cancer",
      "titleCn": "胃癌",
      "docType": "指南",
      "authors": "NCCN Gastric Cancer Panel",
      "specialty": "消化肿瘤",
      "region": "NCCN",
      "org": "NCCN",
      "year": 2026,
      "ver": "Version 4.2026",
      "cat": "治疗",
      "abstract": "Overview 段落（PDF 解析所得）",
      "points": ["核心推荐 1", "核心推荐 2"],
      "publishedAt": "2026-01-15T00:00:00+00:00"
    }
  ],
  "total": 9,
  "page": 1,
  "pageSize": 8
}
```

数据来源唯一：`pc_medical_kb_guidelines`，由 `backend/tools/ingest_guidelines.py` 解析真实 PDF 写入（`source='import:pdf'`），不在种子模块中维护任何虚构指南。

**Agent 工具** `search_guideline`

| 参数 | 类型 | 说明 |
|---|---|---|
| `query` | string | 必填，疾病名 / 指南名 / 发布机构 |
| `year_from` / `year_to` | int | 可选，发表年份区间（在工具内二次过滤） |
| `limit` | int | 默认 10 |

工具经 `KbStore.list_guidelines()` 读真实表，返回 `title` / `title_cn` / `organization` / `doc_type` / `specialty` / `region` / `year` / `version` / `authors` / `summary` / `key_recommendations`。

**Agent 执行内容**

1. 调用 `search_guideline` 检索知识库中的真实指南
2. Agent 基于返回的摘要与核心推荐组织回答，并注明机构、版本与年份
3. 指南内容不足以回答时如实说明，不臆造推荐等级

---

#### 1.2.4 搜药品（医学知识库 Tab）

**REST API**（只读列表）

```
GET /api/medical/kb/drugs
Authorization: Bearer <jwt>

查询参数：
  keyword        string  药名 / 通用名（如「奥希替尼」或「Osimertinib」）
  category       string  分类筛选（"全部" 或具体类别）
  essentialOnly  bool    仅国家基本药物，默认 false
  page           int     默认 1
  pageSize       int     默认 8（目录场景上限 500，一次取全）

响应：
{
  "items": [
    {
      "id": "osimertinib",
      "name": "奥希替尼",
      "generic": "Osimertinib (Tagrisso)",
      "cat": "抗肿瘤药和免疫机能调节药",
      "kind": "西药",
      "atcL1": "抗肿瘤药和免疫机能调节药",
      "atcL2": "靶向药物",
      "atcGroup": "EGFR-TKI",
      "atcGroupDesc": "表皮生长因子受体酪氨酸激酶抑制剂",
      "dosage": "80mg po qd",
      "indications": ["EGFR突变NSCLC一线", "T790M耐药后", "术后辅助"],
      "mechanism": "第三代EGFR-TKI，不可逆抑制EGFR敏感突变和T790M耐药突变。",
      "sideEffects": ["皮疹", "腹泻", "甲沟炎"],
      "contraindications": ["QTc间期延长"],
      "interactions": [...],
      "pregnancy": "D",
      "essential": false
    }
  ],
  "total": 92,
  "page": 1,
  "pageSize": 8
}
```

前端 `DrugCatalog` 一次取全（`pageSize=500`）后在内存组树：西药/中成药 → 一级分类 → 二级分类 → 药物组；输入关键词时第三栏切换为扁平搜索结果。

**Agent 工具** `search_drug`

| 参数 | 类型 | 说明 |
|---|---|---|
| `query` | string | 必填，药名或通用名（商品名与通用名都可命中） |
| `action` | string | `info`（默认，用法用量/机制/妊娠分级）/ `indication`（适应症/禁忌/不良反应）/ `interaction`（相互作用） |
| `limit` | int | 默认 10 |

工具经 `KbStore.list_drugs()` 读 `pc_medical_kb_drugs`。

---

#### 1.2.5 搜文献（医学知识库 Tab）

**REST API**（只读列表）

```
GET /api/medical/kb/literature
Authorization: Bearer <jwt>

查询参数：
  keyword   string  检索关键词，匹配 title / title_en / title_cn
  category  string  分类筛选（"全部" 或具体类别）
  year      int     年份筛选（可选）
  page      int     默认 1
  pageSize  int     默认 8（上限 100）

响应：
{
  "items": [
    {
      "id": "101056nejmoa2500123",
      "title": "Melanoma Prevention Among Children of Melanoma Survivors",
      "titleEn": "Melanoma Prevention Among Children of Melanoma Survivors",
      "titleCn": "黑色素瘤幸存者子女的黑色素瘤预防",
      "studyType": "RCT",
      "authors": "Landi MT, et al.",
      "journal": "N Engl J Med",
      "journalTags": ["独家原文", "TOP期刊", "JCR Q1", "中科院1区"],
      "impactFactor": 96.2,
      "publishedAt": "2026-11-19T00:00:00+00:00",
      "year": 2025,
      "category": "黑色素瘤",
      "abstract": "...",
      "keywords": ["黑色素瘤", "预防", "筛查"],
      "citations": 18,
      "doi": "10.1056/NEJMoa2500123"
    }
  ],
  "total": 12,
  "page": 1,
  "pageSize": 8
}
```

数据来源：`persistence/seeds/kb.py` 的 `_LITERATURE_SEEDS`（12 条真实期刊文献），由 `backend/tools/seed_kb.py` 幂等灌入。

**Agent 工具** `search_literature`

| 参数 | 类型 | 说明 |
|---|---|---|
| `query` | string | 必填，疾病名 / 药物名 / 研究主题 |
| `year_from` / `year_to` | int | 可选，发表年份区间 |
| `limit` | int | 默认 10 |

工具经 `KbStore.list_literatures()` 读 `pc_medical_kb_literatures`，返回 `title` / `title_cn` / `authors` / `journal` / `journal_tags` / `impact_factor` / `study_type` / `year` / `published_at` / `doi` / `abstract`。

**Agent 执行内容**

1. 调用 `search_literature` 工具检索文献库
2. Agent 对返回文献生成中文摘要，提炼研究目的、方法、结论
3. 标注研究类型、期刊与影响因子；命中年会摘要时标注会议来源
4. 库内无匹配文献时如实反馈"知识库暂无相关文献"

---

### 1.3 域 Agent 会话机制

执行模型参见 domain-bundle-design.md §执行模型。患者详情页的 AI 咨询通过 `POST /api/medical/consultations/` 创建会话（登记到 `pc_medical_consultations`），`SESSION_START` 钩子 `ConsultationContextHook` 注入该患者报告清单摘要；Agent 只能经 `read_patient_profile` / `read_patient_reports` / `read_raw_report` 三个只读工具取数，不直接拼 SQL。

---


## 二、UI 设计

> 共享样式规范（glass-card 设计系统、容器/按钮/输入框/加载态等）参见 domain-bundle-design.md §共享样式规范。

### 2.1 侧边栏与工作台入口

启用医疗域后，侧边栏出现**一级入口**，其下平铺二级菜单（由 `scripts/compile/domains.yaml` 的 `group` 驱动，核心侧栏渲染）：

```
┌──────────┐
│ 新任务    │  ← 固定
│ 能力扩展  │  ← 固定
│ 自动化    │  ← 固定
│ 产出物    │  ← 固定
│──────────│
│ 🏥 医疗工作台
│    临床业务
│      智能病历
│      患者管理
│      医学知识库
└──────────┘
```

### 2.2 医疗工作台页面（MedicalWorkbenchApp）

医疗域只渲染**内容区**单列，域内不设自己的侧栏/抽屉：

```
┌──────────────────────────────────────────────────────────────┐
│  MedicalWorkbenchApp                                         │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  main（mw-cq 容器查询上下文，内部滚动）                  │  │
│  │                                                        │  │
│  │  当前页面：智能病历 / 患者管理 / 医学知识库 / 患者详情    │  │
│  │                                                        │  │
│  └────────────────────────────────────────────────────────┘  │
│  ParseQueueFloat（右下角解析队列浮层）                        │
└──────────────────────────────────────────────────────────────┘
```

**布局规范**

| 区域 | 样式 |
|---|---|
| 外层容器 | `relative h-full w-full flex flex-col p-3 sm:p-4 lg:p-5 min-h-0 overflow-hidden` |
| `<main>` | `mw-cq flex-1 min-w-0 min-h-0 overflow-y-auto mw-scroll` |
| 解析队列浮层 | `<ParseQueueFloat />`，`createPortal` 到 body，固定右下角 |

**交互流程**

1. 侧栏点击子项 → 宿主经 `page` / `pageNonce` 下发跳转意图 → 域内切页
2. 刷新 / 前后翻页 → 域内 URL（`?domain=medical&page=&mw_patient=&mw_kb=`）自行解析
3. 域切页后调 `reportDomainPage` 回报，侧栏据此高亮

**路由设计**

| 路由 | 页面组件 | 说明 |
|---|---|---|
| `?domain=medical&page=records` | `MedicalRecordsPage` | 默认页；三栏：患者选择 / 报告列表 / 报告详情 |
| `?domain=medical&page=patients` | `PatientsPage` | 患者管理列表 |
| `?domain=medical&page=knowledge&mw_kb=literature` | `KnowledgePage` | 医学知识库 · 搜文献（缺省 Tab） |
| `?domain=medical&page=knowledge&mw_kb=guidelines` | `KnowledgePage` | 医学知识库 · 搜指南 |
| `?domain=medical&page=knowledge&mw_kb=drugs` | `KnowledgePage` | 医学知识库 · 搜药品 |
| `?domain=medical&page=records&mw_patient=P0001` | `PatientDetailPage` | 患者详情（两栏：档案 / AI 咨询），不在侧栏导航中 |

> 医学翻译没有独立页面：`medical_translate` 仅作为 Agent 工具注册，供对话内调用。

### 2.3 智能病历页面（MedicalRecordsPage）

三栏 + 右下角全局解析队列浮层：

```
┌──────────────────────────────────────────────────────────────────────┐
│  患者选择（可按姓名/病历号检索）      │  报告列表（筛选 + 分页）        │
│  ┌──────────────────────┐  ┌──────────────────────────────────────┐ │
│  │ ● 张**  P0001        │  │ [全部][门诊][住院][影像][病理]        │ │
│  │ ● 李**  P0002        │  │ ──────────────────────────────────  │ │
│  │ ○ 未归属（inbox 3）   │  │ 2026-11-18 胸部CT  已解析  摘要…     │ │
│  └──────────────────────┘  │ 2026-11-17 病理报告 解析中 62%       │ │
│                            └──────────────────────────────────────┘ │
│                              │ 报告详情（结构化字段 / 附件 / 摘要）  │ │
└──────────────────────────────────────────────────────────────────────┘
                                                        ┌───────────┐
                                                        │ 解析队列 2 │
                                                        └───────────┘
```

**交互流程**

1. `UploadModal` 上传 → `POST /records/upload` 返回 `reportIds`，条目以「排队中」进入列表与队列浮层
2. `ParseQueueFloat` 轮询 `GET /records/queue`，展示进度环与失败原因，支持重试
3. AI 无法判定归属的报告进入 `InboxSlideOut`（`GET /records/inbox`），由医生 `POST /{id}/assign` 指派患者
4. 点击报告行 → `ReportDetailModal`：结构化叙事 / 检验 / 用药、AI 摘要、附件预览（`GET /{id}/attachments/{index}`）、重新解析
5. 报告解析完成且已归属患者 → 自动触发摘要分析，可在 `features/briefs` 中查看与重新分析


### 2.4 医学知识库页面（KnowledgePage）

单页三 Tab（文献 / 指南 / 药品），Tab 状态同步到 `?mw_kb=`，刷新或分享可回到同一 Tab。

**页面结构**

```
┌──────────────────────────────────────────────────────┐
│  ┌────────────────────────────────────────┐ [→]     │
│  │  海量医学文献任意搜…                     │         │
│  └────────────────────────────────────────┘         │
│                                                      │
│   📄 搜文献      📋 搜指南      💊 搜药品             │
│   ───────                                            │
│                                                      │
│  最新精选        内科学 肿瘤学 心血管 …      [⇅]      │
│                                                      │
│  [N Engl J Med] [JAMA] [RCT] [Meta分析]  ← 筛选芯片   │
│  ──────────────────────────────────────────────────  │
│  RCT   Melanoma Prevention Among Children of …       │
│        黑色素瘤幸存者子女的黑色素瘤预防                 │
│        Ⓝ N Engl J Med · 独家原文 · IF 96.2   1天前   │
│  ──────────────────────────────────────────────────  │
│  RCT   Tislelizumab plus chemotherapy as first-line  │
│        …                                             │
└──────────────────────────────────────────────────────┘
```

**三个 Tab 的实现**

| Tab | `?mw_kb=` | 组件 | 布局 |
|---|---|---|---|
| 搜文献 | `literature`（缺省） | `features/kb/LiteratureFeed` | 「最新精选」行流：研究类型 + 英文标题 / 中文标题 / 期刊徽标与标识标签 / IF + 相对时间；筛选芯片由数据生成 |
| 搜指南 | `guidelines` | `features/kb/GuidelineFeed` | 「最新精选」+ 学科 Tab（学科取自数据）+ 类型/地区筛选芯片 + 排序切换 |
| 搜药品 | `drugs` | `features/kb/DrugCatalog` | 西药/中成药 → 一级分类 → 二级分类 → 药物组 三栏目录；检索时第三栏切换为扁平结果 |

**交互流程**

1. 顶部统一检索框输入关键词 → 300ms 防抖后按当前 Tab 调对应 `/api/medical/kb/*` 端点
2. 点击 Tab → 清空关键词、写入 `?mw_kb=`、切换到对应内容区
3. 点击指南/文献行 → 行内展开摘要与核心推荐；点击药名 → 行内展开说明书要点
4. 筛选芯片与学科 Tab 均为客户端过滤当前已取数据，不追加请求

> 知识库为 `KnowledgePage` 单页三 Tab（文献 / 指南 / 药品），facet 筛选与阅读器在同页内展开，不设独立搜索页。医学翻译不设独立页面，`medical_translate` 仅作为 Agent 工具。

---

## 三、表设计

### 3.1 表清单（7 表 + 1 视图）

DDL 真相源：`deploy/sql/init_medical.sql`；SQLAlchemy 表定义：`models/tables.py`。全部 `CREATE TABLE IF NOT EXISTS`；按未上线标准不保留补列迁移。

| 表名 | 用途 | 关键列 |
|---|---|---|
| `pc_medical_patients` | 患者档案 + 画像 | `id`(varchar PK) / `name` / `birth_date` / `id_number_masked` / `id_number_hash` / `phone_hash` / `primary_diagnosis` / `tnm_stage` / `ecog_score` / `vitals_json` / `biomarkers_json` / `medications_json` / `labs_json` / `tags_json` |
| `pc_medical_patient_reports` | 统一临床文档表（含解析队列） | `id` / `patient_id`(可空) / `report_type` / `file_path` / `attachments_json` / `patient_name_raw` / `match_reason` / `status` / `progress` / `raw_text` / `narrative_json` / `labs_json` / `medications_json` / `summary` / `started_at` / `finished_at` |
| `pc_medical_consultations` | 患者 AI 咨询会话登记 | `session_id`(unique) / `patient_id` / `doctor_id` / `title` / `created_at` |
| `pc_medical_kb_literatures` | 知识库 · 文献 | `title` / `title_en` / `title_cn` / `study_type` / `journal` / `journal_tags_json` / `impact_factor` / `published_at` / `doi` / `source` |
| `pc_medical_kb_guidelines` | 知识库 · 指南 | `title` / `title_cn` / `doc_type` / `authors` / `specialty` / `region` / `org` / `version` / `year` / `abstract` / `points_json` / `source` |
| `pc_medical_kb_drugs` | 知识库 · 药品 | `name` / `generic_name` / `drug_kind` / `atc_l1` / `atc_l2` / `atc_group` / `dosage` / `indications_json` / `mechanism` / `side_effects_json` / `contraindications_json` / `interactions_json` / `pregnancy_grade` / `is_essential` |
| `pc_medical_briefs` | 病历摘要（分析管线产出） | `patient_id` / `report_ids_json` / `status` / `version` / `sections_json` / `citations_json` / `audit_result_json` |
| `v_patient_timeline`（视图） | 患者时间线 | `patient_id` / `event_id` / `event_type` / `event_date` / `title` / `detail` |

统一审计列（除 `consultations` 与视图）：`create_at` / `create_by` / `update_at` / `update_by` / `is_deleted`。

```sql
-- 示例：知识库 · 指南（deploy/sql/init_medical.sql）
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
    source      VARCHAR(32)  DEFAULT 'seed',   -- 指南实际写入值为 'import:pdf'
    create_at   TIMESTAMPTZ  DEFAULT NOW(),
    create_by   VARCHAR(64)  DEFAULT 'default',
    update_at   TIMESTAMPTZ  DEFAULT NOW(),
    update_by   VARCHAR(64)  DEFAULT 'default',
    is_deleted  SMALLINT     DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_pc_medical_kb_guidelines_category   ON pc_medical_kb_guidelines (category);
CREATE INDEX IF NOT EXISTS ix_pc_medical_kb_guidelines_year       ON pc_medical_kb_guidelines (year);
CREATE INDEX IF NOT EXISTS ix_pc_medical_kb_guidelines_is_deleted ON pc_medical_kb_guidelines (is_deleted);
```

### 3.2 表关系说明

```
pc_medical_patients     1──N  pc_medical_patient_reports   （patient_id 可空：先入库后归属）
pc_medical_patients     1──N  pc_medical_consultations
pc_medical_patients     1──N  pc_medical_briefs
pc_medical_patient_reports 1──N pc_medical_briefs          （经 report_ids_json 弱关联）
pc_medical_kb_literatures / kb_guidelines / kb_drugs       （独立只读共享数据，无外键）
v_patient_timeline                                         （由 patient_reports 派生）
```

### 3.3 表结构位置

DDL 统一放在 `deploy/sql/init_medical.sql`；域激活时由 `ensure_medical_schema()` 调用通用执行器创建，执行器按文件持有 advisory lock，语句保持 `IF NOT EXISTS` 幂等。

### 3.4 初始化入口

```python
# persistence/schema.py
async def ensure_medical_schema(engine: AsyncEngine) -> None:
    await apply_domain_schema(engine, "medical")
```

在 `on_build_infrastructure` 中调用一次；失败仅记录日志，不阻断域加载。

---


## 四、MedicalBundle 实现设计

### 4.1 包结构

```
subpackages/medical/
  pyproject.toml
  _manifest_generated.py                 # 侧栏菜单 / 页面入口声明
  _protocols.py                          # ToolContext + text_result / error_result
  api/                                   # REST 路由（on_build_lifecycle 注册）
    records.py                           # 上传 / 队列 / 结构化病历
    patients.py                          # 患者档案与列表
    kb.py                                # 知识库三列表：literature / guidelines / drugs
    consultations.py                     # 患者 AI 咨询会话
    briefs.py                            # 病历摘要
  tools/                                 # Agent 工具（on_build_runtime 注册，共 8 个）
    smart_medical_record.py              # 病历文件解析 + 结构化
    medical_translate.py                 # 医学术语翻译
    search_guideline.py                  # 指南检索 → KbStore
    search_drug.py                       # 药品检索 → KbStore
    search_literature.py                 # 文献检索 → KbStore
    read_patient_profile.py              # 只读：患者画像
    read_patient_reports.py              # 只读：报告清单
    read_raw_report.py                   # 只读：报告原文
  persistence/
    medical_store.py                     # 患者 / 报告 / 队列 / 咨询
    kb_store.py                          # 知识库三表（KbStore）
    brief_store.py                       # 摘要
    schema.py                            # ensure_medical_schema（委托通用 SQL 执行器）
    storage.py                           # 上传文件落盘
    seeds/kb.py                          # 文献 12 条 + 药品 92 条种子
    seeds/patients.py                    # 患者演示数据
  services/                              # 解析 / 分析 / 患者业务编排
  domain/                                # hooks（PHI 脱敏、咨询上下文）、governance（HIPAA）、parsing、analysis
  integration/bundle.py                  # MedicalBundle（唯一集成点）

backend/tools/                           # 运维脚本（非运行时）
  seed_kb.py                             # 幂等回填文献 / 药品
  ingest_guidelines.py                   # 解析真实 PDF 并写入指南表（source='import:pdf'）

frontend/subpackages/medical/src/
  app/MedicalWorkbenchApp.tsx            # 域内容区外壳（导航已在系统侧栏）
  pages/
    MedicalRecordsPage.tsx               # 智能病历（三栏）
    PatientsPage.tsx                     # 患者管理
    PatientDetailPage.tsx                # 患者详情（档案 + AI 咨询两栏）
    KnowledgePage.tsx                    # 医学知识库（单页三 Tab）
  features/kb/                           # LiteratureFeed / GuidelineFeed / DrugCatalog / ChipsRow
  features/records/                      # 上传、报告列表、详情、解析队列浮层
  api/client.ts                          # 薄封装，直接返回 camelCase 视图模型
  types/view.ts                          # 前端视图类型
```

> 目录结构：钩子与治理在 `domain/hooks`、`domain/governance`，路由在 `api/`，无独立 `skills/` 目录（域技能通过 `configs/medical.json` 与运行时注册表达）。

### 4.2 Bundle 实现

```python
# integration/bundle.py（节选，仅展示三阶段注册骨架）
class MedicalBundle:
    name = "medical"
    required_core_version = ">=0.1.0,<2.0.0"
    depends_on: list[str] = []
    task_store: Any | None = None

    async def on_build_infrastructure(self, ctx: DomainInfrastructureContext) -> None:
        from ..persistence.medical_store import MedicalStore
        from ..persistence.kb_store import KbStore
        from ..persistence.brief_store import BriefStore
        from ..persistence.schema import ensure_medical_schema

        ctx.register_store("medical", MedicalStore(ctx.db_engine))
        ctx.register_store("medical_kb", KbStore(ctx.db_engine))
        ctx.register_store("medical_briefs", BriefStore(ctx.db_engine))
        await ensure_medical_schema(ctx.db_engine)

    def on_build_runtime(self, ctx: DomainRuntimeContext) -> None:
        self._register_tools(ctx)
        self._register_hooks(ctx)
        self._register_governance(ctx)
        self._register_domain_extensions(ctx)
        ctx.commit()

    def _register_tools(self, ctx: DomainRuntimeContext) -> None:
        """注册 8 个工具；知识库三工具注入 KbStore，病历与只读工具注入 MedicalStore。"""
        medical_store = ctx.domain_stores.get("medical")
        kb_store = ctx.domain_stores.get("medical_kb")

        record_tool = SmartMedicalRecordTool(); record_tool.store = medical_store
        guideline_tool = SearchGuidelineTool();  guideline_tool.kb_store = kb_store
        drug_tool = SearchDrugTool();            drug_tool.kb_store = kb_store
        literature_tool = SearchLiteratureTool(); literature_tool.kb_store = kb_store
        # reports / raw_report / profile 三个只读工具同样注入 medical_store
        ...

    def on_build_lifecycle(self, ctx: DomainLifecycleContext) -> None:
        from ..api.records import router as records_router
        from ..api.patients import router as patients_router
        from ..api.kb import router as kb_router
        from ..api.consultations import router as consultations_router
        from ..api.briefs import router as briefs_router

        ctx.register_route(records_router, "/api/medical/records")
        ctx.register_route(patients_router, "/api/medical/patients")
        ctx.register_route(kb_router, "/api/medical/kb")
        ctx.register_route(consultations_router, "/api/medical/consultations")
        ctx.register_route(briefs_router, "/api/medical/briefs")

    def get_manifest(self) -> DomainManifest:
        from .._manifest_generated import MANIFEST   # 由 domains.yaml 生成
        return MANIFEST
```

### 4.3 入口点注册

```toml
# pyproject.toml
[project]
name = "prismclaw-medical"
version = "0.1.0"
dependencies = ["prismclaw>=0.1.0,<2.0.0"]

[project.entry-points."prismclaw.domains"]
medical = "subpackages.medical.integration.bundle:MedicalBundle"
```

`_manifest_generated.py` 由 `scripts/compile/domains-registry.mjs` 从 `domains.yaml` 生成，声明 3 个二级菜单（智能病历 / 患者管理 / 医学知识库，同属 `临床业务` 分组）与 1 个页面入口 `/medical`。**该文件请勿手改。**

### 4.4 REST 路由实现

路由分 5 组，全部在 `api/` 下，prefix 由 Bundle 的 `register_route` 指定：

| 文件 | 前缀 | 端点 |
|---|---|---|
| `api/records.py` | `/api/medical/records` | `POST /upload`(202) · `GET /` · `GET /inbox` · `GET /queue` · `POST /{id}/reparse` · `POST /{id}/assign` · `GET /{id}/attachments/{index}` |
| `api/patients.py` | `/api/medical/patients` | `POST /` · `GET /` · `GET /stats` · `GET /{id}` · `GET /{id}/reports` · `PATCH /{id}/reports/{report_id}` |
| `api/kb.py` | `/api/medical/kb` | `GET /literature` · `GET /guidelines` · `GET /drugs`（均 `keyword` + `page` + `pageSize`，直出 camelCase VM） |
| `api/consultations.py` | `/api/medical/consultations` | `POST /` · `GET /` · `GET /{session_id}` |
| `api/briefs.py` | `/api/medical/briefs` | `GET /` · `GET /{id}` · `POST /` · `POST /{id}/reanalyze` · `DELETE /{id}` |

```python
# api/kb.py
router = APIRouter(tags=["medical-kb"])

@router.get("/guidelines")
async def list_guidelines(
    keyword: str = "",
    page: int = 1,
    page_size: int = Query(8, alias="pageSize"),
    store: KbStore = Depends(get_kb_store),
):
    """指南列表 — 直出 camelCase 视图模型。"""
    result = await store.list_guidelines(keyword=keyword, page=page, page_size=page_size)
    return {
        "items": [guideline_to_vm(g) for g in result["items"]],
        "total": result["total"],
        "page": page,
        "pageSize": page_size,
    }
```

> 知识库无写接口：数据由 `tools/seed_kb.py`（文献/药品）与 `tools/ingest_guidelines.py`（指南 PDF）离线写入，前端只读。

### 4.5 工具实现示例

```python
# tools/search_guideline.py
@dataclass
class SearchGuidelineTool:
    name: str = "search_guideline"
    description: str = "临床指南检索工具 — 检索知识库中的 NCCN / CSCO / 中华医学会等临床实践指南"
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "疾病名 / 指南名 / 发布机构"},
            "year_from": {"type": "integer", "description": "起始年份（含）"},
            "year_to": {"type": "integer", "description": "结束年份（含）"},
            "limit": {"type": "integer", "description": "返回结果数量上限", "default": 10},
        },
        "required": ["query"],
    })
    tool_class: str = "read"
    side_effect: bool = False
    effect_class: str = "search"

    kb_store: Any = None   # 由 bundle 注入

    async def execute(self, args: dict[str, Any], context: ToolContext) -> Any:
        call_id = args.get("_call_id", "")
        if self.kb_store is None:
            return error_result(call_id, "KbStore 未注入，无法检索指南")
        page = await self.kb_store.list_guidelines(
            keyword=args.get("query", ""), page=1, page_size=max(limit * 5, 50)
        )
        # 按 year_from / year_to 二次过滤后取前 limit 条
        ...
        return text_result(call_id, json.dumps(payload, ensure_ascii=False))
```

`search_drug` / `search_literature` 结构相同，分别调 `KbStore.list_drugs()` / `list_literatures()`，不持任何内置数据集。

### 4.6 PHI 脱敏钩子

```python
# domain/hooks/phi_redact.py
class PHIRedactHook(HookHandler):
    @property
    def name(self) -> str: return "phi_redact"

    @property
    def event(self) -> str: return "pre_tool"

    async def execute(self, context: HookContext) -> HookResult:
        tool_name = context.tool_name or ""
        if not any(tool_name.startswith(p) for p in MEDICAL_TOOL_PREFIXES):
            return HookResult.success()
        redacted = self._redact_phi(context.tool_input)
        if redacted is None:
            return HookResult.success()
        return HookResult(outcome=HookOutcome.SUCCESS, updated_input=redacted,
                          additional_context="[PHI脱敏] 已对工具参数中的受保护健康信息进行脱敏处理")
```

生效前缀取 `config/settings.py` 的 `MEDICAL_TOOL_PREFIXES`（`smart_medical_record` / `medical_translate` / `search_guideline` / `search_drug` / `search_literature`），脱敏模式见 `domain/governance/phi_patterns.py`（身份证号、手机号、姓名、病历号）。


## 五、前端组件设计

### 5.1 路由与导航

域内无路由库，导航状态由 `navigation/useWorkbenchNav` 统一管理，来源有三：

1. 系统侧栏点击子项 → 宿主经 `page` / `pageNonce` 下发
2. 刷新 / 浏览器前进后退 → 解析 URL `?domain=medical&page=&mw_patient=&mw_kb=`
3. 域内交互（如点击患者行）→ `openPatient(patientId)`

```ts
// navigation/nav-config.ts（页面键与默认值）
export const PAGES = ['records', 'patients', 'knowledge'] as const
export const KB_TABS = ['literature', 'guidelines', 'drugs'] as const
export const DEFAULT_PAGE = 'records'
export const DEFAULT_KB_TAB = 'literature'
```

`app/MedicalWorkbenchApp.tsx` 按 `route.page` 分发：

| 状态 | 渲染 |
|---|---|
| `isPatientDetail` | `<PatientDetailPage patientId onBack />` |
| `records` | `<MedicalRecordsPage onOpenPatient />` |
| `patients` | `<PatientsPage onOpenPatient />` |
| `knowledge` | `<KnowledgePage />` |

切页后调 `reportDomainPage('medical', route.page)` 回报系统侧栏做高亮。

### 5.2 智能病历（MedicalRecordsPage）

三栏：患者选择 / 报告列表 / 报告详情，配套全局解析队列浮层。

| 组件 | 职责 |
|---|---|
| `features/records/UploadModal` | 上传文件 → `POST /api/medical/records/upload`（202 入队） |
| `features/records/InboxSlideOut` | 待归属报告抽屉（`GET /inbox`），支持指派给患者 |
| `features/records/AssignDrawer` | 指派抽屉：选定患者后 `POST /{id}/assign` |
| `features/records/ReportArchive` | 已归档报告区（按就诊时间分组） |
| `features/records/ReportBrowser` | 报告列表 + 筛选 + 分页（`GET /`，`PagePager`） |
| `features/records/ReportDetailModal` | 报告详情：结构化字段、附件预览、重新解析 |
| `features/records/AttachmentViewer` | 原件预览（`GET /{id}/attachments/{index}`） |
| `features/records/StructuredData` | 解析后的结构化字段展示 |
| `features/records/ParseQueueFloat` | 右下角悬浮队列（`GET /queue`，轮询） |
| `features/briefs/*` | 病历摘要生成、引用弹窗、列表侧板 |

### 5.3 患者管理（PatientsPage / PatientDetailPage）

| 组件 | 职责 |
|---|---|
| `features/patients/PatientStatsRow` | 顶部统计条（`GET /patients/stats`） |
| `features/patients/PatientFilterBar` | 关键词 / 病种 / 状态筛选 |
| `features/patients/PatientTable` | 患者表格，行点击进入详情 |
| `features/patients/PatientDetailView` | 详情页骨架（档案 + 咨询两栏） |
| `features/patients/PatientIdentityBanner` | 详情页身份横幅 |
| `features/patients/MedicalSummary` | 病史概要 |
| `features/patients/ProgressRing` | 治疗进度环 |
| `features/consultation/MedicalChatArea` | 详情页 AI 咨询区（`/api/medical/consultations`） |
| `features/consultation/useConsultationChat` | 会话状态与流式消息 |

> 本域不设 `SmartRecordPage` / `OncologyDetailCard` / `MedicalWorkbenchPage`：工作台由上述真实页面组件组合而成。

### 5.4 医学知识库（KnowledgePage）

单页三 Tab，`features/kb/` 下三个内容组件 + 一个通用芯片组件：

| 组件 | 数据源 | 布局 |
|---|---|---|
| `LiteratureFeed` | `GET /kb/literature` | 搜索框 + 最新精选 + 分类芯片 + 行流（研究类型 / 中英标题 / 期刊徽标 / IF / 相对时间） |
| `GuidelineFeed` | `GET /kb/guidelines` | 最新精选 + 学科 Tab + 类型/地区芯片 + 排序（年份 / 名称） |
| `DrugCatalog` | `GET /kb/drugs?pageSize=500` | 西药/中成药 → 一级分类 → 二级分类 → 药物组三栏；检索时第三栏转扁平结果 |
| `ChipsRow` | — | 通用筛选芯片行（单选/多选、可清除）；`kb-utils.ts` 提供去重与相对时间格式化 |

`api/client.ts` 只做薄封装，直接返回 `types/view.ts` 中的 camelCase 视图模型（`LiteratureEntry` / `GuidelineEntry` / `DrugEntry`），不做字段重映射。


## 六、安全设计

### 6.1 PHI 脱敏策略

| 数据字段 | 存储策略 | 展示策略 |
|---|---|---|
| 患者姓名 | 仅存 `name_hash`（SHA-256 + salt） | 展示"患者***" |
| 出生日期 | 仅存 `birth_year` | 展示"XX岁" |
| 身份证号 | 不存储 | 不展示 |
| 电话 | 不存储 | 不展示 |
| 地址 | 不存储 | 不展示 |
| 住院号 | 加密存储 | 需授权展示 |
| 基因检测结果 | 加密存储 | 正常展示（非PHI） |
| TNM分期 | 正常存储 | 正常展示（非PHI） |

### 6.2 医疗内容安全

| 验证项 | 触发时机 | 验证内容 |
|---|---|---|
| 强制免责声明 | 每次涉及用药/剂量/禁忌的回答 | 由 `_disclaimer_registry` 注册的 `medical` 规则注入，任何置信度都附加 |
| 证据推断 | Agent 输出证据结论前 | `domain/analysis/evidence.py` 的 `medical_evidence_inferencer` 标注证据强度 |
| 敏感扩展拦截 | 文件路由阶段 | `.dcm` / `.dicom` / `.hl7` / `.nii` / `.phi` / `.emr` / `.ehr` 走敏感通道 |
| 咨询上下文隔离 | 会话启动 | `ConsultationContextHook`（SESSION_START）只注入该患者报告清单摘要 |

### 6.3 HIPAA 治理 Reducer

```python
# domain/governance/hipaa.py
class HIPAAReducer:
    """HIPAA 合规治理 — 限制医疗域工具的使用范围。"""

    # reduce() 约束：
    # 1. 禁止含 PHI 的工具结果写入非医疗域文件
    # 2. 医疗数据不得发送到未授权外部端点
    # 3. 所有 PHI 访问记入审计日志
```

治理规则、证据推断器、敏感扩展与 PHI 模式均在 `on_build_runtime` 中注册，并通过 `ctx.defer_registry_cleanup` 在域卸载时反注册。

---


## 七、与系统现有能力的映射

### 7.1 工具能力映射

| 医疗域工具（8 个） | 系统现有能力 | 数据来源 |
|---|---|---|
| `smart_medical_record` | `RunRequest.files` + OCR + 结构化抽取 | `MedicalStore`（写入报告） |
| `medical_translate` | 通用翻译能力 | 无（模型能力） |
| `search_guideline` | `WebSearch` / `WebFetch`（可选外部补充） | `KbStore.list_guidelines()` |
| `search_drug` | 同上 | `KbStore.list_drugs()` |
| `search_literature` | 同上 | `KbStore.list_literatures()` |
| `read_patient_profile` | 无（新建） | `MedicalStore` |
| `read_patient_reports` | 无（新建） | `MedicalStore` |
| `read_raw_report` | 无（新建） | `MedicalStore` |

后三个为患者 AI 咨询的只读工具，避免 Agent 直接拼 SQL。不设 `chemo_protocol` / `targeted_match` / `proton_eligibility` 三个肿瘤专科工具。

### 7.2 数据表映射

| 表名 | 说明 |
|---|---|
| `pc_medical_patients` | 患者档案，PHI 脱敏（`*_masked` + `*_hash`） |
| `pc_medical_patient_reports` | 报告 / 解析队列 / 结构化结果（`patient_id` 可空，支持先上传后归属） |
| `pc_medical_consultations` | 患者 AI 咨询会话登记 |
| `pc_medical_briefs` | 病历摘要 |
| `pc_medical_kb_literatures` | 文献 12 条（种子） |
| `pc_medical_kb_guidelines` | 指南 22 条（PDF 导入） |
| `pc_medical_kb_drugs` | 药品 92 条（种子） |

> `pc_medical_records` / `pc_oncology_staging` / `pc_oncology_treatments` / `pc_medical_searches` / `pc_medical_translations` / `pc_medical_record_files` 不单独建表：对应信息由 `pc_medical_patient_reports` 的 JSONB 字段（`narrative_json` / `labs_json` / `medications_json`）承载。

---

## 八、实施进度

| 阶段 | 功能 | 状态 |
|---|---|---|
| P0 | 智能病历（上传 / 解析队列 / 结构化 / 摘要） | 已实现 |
| P0 | 患者管理（列表 / 统计 / 详情 / AI 咨询） | 已实现 |
| P0 | 医学知识库 · 搜药品（92 条真实说明书） | 已实现 |
| P1 | 医学知识库 · 搜指南（22 条真实 PDF 导入） | 已实现 |
| P1 | 医学知识库 · 搜文献（12 条真实期刊文献） | 已实现 |
| P1 | 医学翻译（仅 Agent 工具，无独立页面） | 已实现 |
