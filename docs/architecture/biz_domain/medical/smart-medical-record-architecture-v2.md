# 肿瘤患者智能病历工作台 — 功能架构设计 v3

> 以患者为中心，非以功能为中心。患者管理是入口，报告整理和病历生成是基于患者的功能。

---

## 一、核心设计理念

**一个患者 = 一个工作台**

医生的工作流不是"打开报告整理功能 → 上传文件"，而是：

```
找到患者 → 进入患者详情 → 看到时间线 → 上传新报告/生成新病历
```

所有功能围绕**患者**组织，不是围绕**操作类型**组织。

---

## 二、页面架构

```
智能病历（工作台菜单入口）
│
├── 患者列表（首页）
│   ├── 搜索/筛选（姓名/诊断/治疗阶段）
│   └── 患者卡片列表
│
└── 患者详情页（选中患者后进入）
    │
    ├── 患者概览（顶部固定）
    │   ├── 基本信息（姓名/性别/年龄/诊断/TNM分期）
    │   ├── 当前治疗方案（化疗方案+周期）
    │   └── 快捷操作（上传报告/生成病历/发送到对话）
    │
    ├── 时间线（主体区域）
    │   ├── 按时间倒序排列所有事件：
    │   │   ├── 📄 报告事件（病理/基因检测/影像/检验）
    │   │   ├── 🏥 就诊事件（入院/查房/出院）
    │   │   ├── 💊 治疗事件（化疗/放疗/靶向/手术）
    │   │   └── 📋 病历事件（入院记录/病程/出院小结）
    │   │
    │   ├── 时间线筛选（按事件类型过滤）
    │   └── 点击事件→展开详情
    │
    ├── 报告整理（从时间线或快捷入口进入）
    │   ├── 上传报告文件（PDF/图片/DOCX）
    │   ├── AI 自动提取关键指标
    │   ├── 确认/编辑提取结果
    │   └── 归入时间线（自动关联日期）
    │
    ├── 病历生成（从时间线或快捷入口进入）
    │   ├── 基于时间线上所有数据
    │   ├── 选择文书类型（入院记录/病程/出院小结）
    │   ├── AI 生成草稿
    │   ├── 医生审核修改
    │   └── 保存/归入时间线
    │
    └── AI 辅助诊疗（从快捷入口或时间线事件触发）
        ├── 患者全景摘要（自动汇总时间线关键数据）
        ├── 治疗方案推荐（基于诊断+分期+基因+指南）
        ├── 疗效评估（RECIST 标准自动比对病灶变化）
        ├── 不良反应预警（基于检验趋势+用药方案）
        └── 对话式追问（进入医疗助手对话，携带患者上下文）
```

---

## 三、数据模型

### 3.1 核心实体

```python
@dataclass
class Patient:
    """肿瘤患者"""
    id: str
    name: str
    gender: str
    birth_date: str           # 出生年月
    id_number: str            # 身份证号（脱敏显示）
    primary_diagnosis: str    # 主诊断
    tnm_stage: str            # TNM 分期
    clinical_stage: str       # 临床分期
    ecog_score: int           # ECOG 评分
    current_regimen: str      # 当前治疗方案
    current_cycle: int        # 当前周期数
    attending_doctor: str     # 主治医生
    tags: list[str]           # 标签（如：化疗中/待手术/随访中）


@dataclass
class TimelineEvent:
    """时间线事件 — 患者详情页的核心数据单元"""
    id: str
    patient_id: str
    event_type: str           # report | encounter | treatment | document
    event_date: str           # 事件日期
    title: str                # 事件标题
    summary: str              # 摘要（时间线上展示）
    detail: dict              # 结构化详情（点击展开）

    # 报告类事件
    report_type: str | None   # pathology | genetic | imaging | lab
    report_file_url: str | None

    # 治疗类事件
    treatment_type: str | None  # chemo | radiation | targeted | surgery | immunotherapy
    regimen: str | None
    cycle: int | None
    response: str | None       # CR/PR/SD/PD

    # 病历类事件
    document_type: str | None   # admission | course | discharge | summary
    document_status: str | None # draft | signed | archived


@dataclass
class ReportExtraction:
    """报告提取结果"""
    id: str
    event_id: str
    patient_id: str
    report_type: str

    # 病理报告
    pathology_diagnosis: str | None
    morphology: str | None
    ihc_markers: dict | None       # {"ER": "+", "PR": "-", "HER2": "2+", "Ki-67": "30%"}

    # 基因检测
    gene_variants: list[dict] | None  # [{"gene": "EGFR", "mutation": "L858R", "frequency": "35%"}]
    tmb: str | None
    msi_status: str | None

    # 影像报告
    imaging_findings: str | None
    lesion_measurements: list[dict] | None  # [{"location": "右肺上叶", "size": "3.2×2.8cm"}]
    recist_assessment: str | None  # CR/PR/SD/PD

    # 检验报告
    lab_items: list[dict] | None   # [{"name": "CEA", "value": "12.5", "unit": "ng/mL", "reference": "0-5"}]
    abnormal_flags: list[str] | None
```

### 3.2 时间线事件类型

| event_type | 图标 | 典型事件 |
|-----------|------|---------|
| `report` | 📄 | 病理报告、基因检测、CT 报告、血液检验 |
| `encounter` | 🏥 | 入院、查房、出院 |
| `treatment` | 💊 | 化疗第3周期、放疗15次、靶向用药 |
| `document` | 📋 | 入院记录、病程记录、出院小结 |

---

## 四、功能详设

### 4.1 患者列表

**布局**：

```
┌───────────────────────────────────────────────────┐
│  智能病历                          [+ 新增患者]    │
├───────────────────────────────────────────────────┤
│  🔍 搜索姓名/诊断          筛选: [化疗中▼] [科室▼] │
├───────────────────────────────────────────────────┤
│                                                     │
│  ┌─────────────────────────────────────────────┐  │
│  │ 张明华  男 62岁   右肺上叶肺癌 cT2aN1M0 IIB  │  │
│  │ 培美曲塞+卡铂 第3周期   主治：李主任          │  │
│  │ [化疗中] [待评估]         最近：8.28 CT报告   │  │
│  └─────────────────────────────────────────────┘  │
│                                                     │
│  ┌─────────────────────────────────────────────┐  │
│  │ 王秀英  女 55岁   左乳浸润性导管癌 T2N0M0 IIA│  │
│  │ AC→T 方案 第4周期   主治：陈主任              │  │
│  │ [化疗中]             最近：8.30 血常规        │  │
│  └─────────────────────────────────────────────┘  │
│                                                     │
│  ┌─────────────────────────────────────────────┐  │
│  │ 刘建国  男 70岁   食管鳞癌 cT3N1M0 III       │  │
│  │ 放疗 30/30次   已完成   主治：赵主任          │  │
│  │ [随访中] [待复查]       最近：8.25 出院小结   │  │
│  └─────────────────────────────────────────────┘  │
│                                                     │
└───────────────────────────────────────────────────┘
```

### 4.2 患者详情 — 时间线

**布局**：

```
┌───────────────────────────────────────────────────┐
│ ← 返回   张明华  男 62岁   右肺上叶肺癌 IIB期     │
│ 培美曲塞+卡铂 第3周期   ECOG 1分                    │
│                          [上传报告] [生成病历]       │
├───────────────────────────────────────────────────┤
│                                                     │
│  筛选: [全部] [📄报告] [🏥就诊] [💊治疗] [📋病历]  │
│                                                     │
│  ── 2026年8月 ──────────────────────────────────   │
│                                                     │
│       📄 8.28  CT复查报告                           │
│       │     右肺上叶病灶 3.2→2.4cm (PR)            │
│       │     纵隔淋巴结较前缩小                      │
│       │                               [查看详情]    │
│       │                                              │
│       💊 8.22  培美曲塞+卡铂 第3周期                │
│       │     耐受良好，II度骨髓抑制                   │
│       │                               [查看详情]    │
│       │                                              │
│       📄 8.20  血常规+肝肾功能                       │
│       │     WBC 3.2↓  Hb 105↓  CEA 8.5↓           │
│       │                               [查看详情]    │
│       │                                              │
│  ── 2026年7月 ──────────────────────────────────   │
│                                                     │
│       💊 7.18  培美曲塞+卡铂 第2周期                │
│       │     III度骨髓抑制，G-CSF支持                │
│       │                               [查看详情]    │
│       │                                              │
│       📋 7.15  入院记录                             │
│       │     第2周期化疗入院                          │
│       │                               [查看详情]    │
│       │                                              │
│       📄 7.05  基因检测报告                          │
│             EGFR L858R(-) ALK(-) PD-L1 60%         │
│                               [查看详情]            │
│                                                     │
│  [加载更多...]                                      │
└───────────────────────────────────────────────────┘
```

### 4.3 报告整理

**入口**：患者详情页 → [上传报告] 按钮 → 弹出上传面板

**流程**：

```
选择/拖拽文件
    ↓
AI 识别报告类型（病理/基因/影像/检验）
    ↓
提取关键指标（结构化）
    ↓
展示提取结果 → 医生确认/修正
    ↓
归入时间线（自动关联日期）
```

**提取内容按报告类型**：

| 报告类型 | 提取字段 |
|---------|---------|
| 病理报告 | 诊断、组织学分级、免疫组化（ER/PR/HER2/Ki-67等）、切缘状态 |
| 基因检测 | 突变基因、变异类型、丰度、TMB、MSI、推荐靶向药 |
| CT/MRI/PET | 病灶位置、大小、RECIST 评估、新发病灶、转移情况 |
| 血液检验 | 异常指标、肿瘤标志物趋势、关键值高亮 |

**关键**：提取结果不是终点，归入时间线才是。时间线上所有数据才能驱动病历生成。

### 4.4 病历生成

**入口**：患者详情页 → [生成病历] 按钮 → 选择文书类型

**核心**：基于时间线上**该患者的所有数据**生成，不是从零开始。

**生成流程**：

```
时间线数据（报告+治疗+历史病历）
    ↓
选择文书类型 + 补充口述内容
    ↓
LLM 生成草稿
    ↓
医生审核修改 → 保存
    ↓
归入时间线
```

**各文书的数据来源**：

| 文书类型 | 时间线数据来源 | 补充内容 |
|---------|--------------|---------|
| 入院记录 | 既往诊断、上次出院小结、最近检验报告 | 本次主诉/现病史 |
| 病程记录 | 本次入院记录、今日检验/治疗 | 查房口述/病情变化 |
| 出院小结 | 入院记录+全部病程+治疗记录+最近检验 | 出院医嘱/随访计划 |
| 治疗记录 | 当前方案、周期数、既往不良反应 | 本次耐受情况 |

### 4.5 AI 辅助诊疗

**入口**：患者详情页 → [AI 辅助诊疗] 按钮，或从时间线事件右键菜单触发

**核心**：患者时间线上的全部数据就是 AI 的输入上下文，不是医生手动描述病情。

**五大能力**：

#### 4.5.1 患者全景摘要

**场景**：主任查房、MDT 会诊前，快速了解患者全貌。

**生成逻辑**：
```
时间线全部数据
    ↓ 按类别聚合
诊断+分期 + 基因特征 + 治疗经过 + 疗效变化 + 近期检验
    ↓ LLM 生成
患者全景摘要（1 屏可读完）
```

**输出格式**：
```
【患者全景】张明华 男 62岁
诊断：右肺上叶腺癌 cT2aN1M0 IIB期
基因：EGFR(-) ALK(-) ROS1(-) PD-L1 60%
治疗：培美曲塞+卡铂 ×3 周期
疗效：PR（病灶 3.2→2.4cm）
不良反应：III度骨髓抑制（第2周期），II度（第3周期）
近期：CEA 8.5↓ WBC 3.2↓
关键决策点：PD-L1 高表达，进展后可考虑免疫治疗
```

#### 4.5.2 治疗方案推荐

**场景**：初诊制定方案、疗效不佳换方案、基因检测结果指导靶向选择。

**输入**：诊断 + 分期 + 基因检测结果 + 既往治疗 + ECOG 评分
**输出**：NCCN/CSCO 指南推荐方案 + 循证依据 + 适用理由

**示例**：
```
基于 CSCO 2026 指南，推荐方案：
1. 培美曲塞+铂类+帕博利珠单抗（I级推荐，KEYNOTE-189）
   - 适用：非鳞 NSCLC，PD-L1≥1%，ECOG 0-1
   - 本患者 PD-L1 60%，ECOG 1，符合条件
2. 帕博利珠单抗单药（II级推荐，KEYNOTE-024）
   - 适用：PD-L1≥50%
   - 本患者 PD-L1 60% 可考虑，但联合化疗缓解率更高
```

#### 4.5.3 疗效评估

**场景**：每次 CT 复查后，自动比对基线和最新病灶数据。

**逻辑**：
```
时间线上所有影像报告
    ↓ 提取可测量病灶尺寸
    ↓ 与基线对比
RECIST 1.1 标准自动评估（CR/PR/SD/PD）
    ↓
生成疗效趋势图
```

**输出**：
```
RECIST 1.1 评估：PR（部分缓解）
基线病灶总和：5.8cm（8.05 CT）
最新病灶总和：4.2cm（8.28 CT）
缩小率：27.6%（≥30% 为 PR 临界，接近 PR）

靶病灶变化：
- 右肺上叶：3.2→2.4cm (-25%)
- 纵隔淋巴结：2.6→1.8cm (-31%)

非靶病灶：纵隔其余淋巴结较前缩小
无新发病灶
```

#### 4.5.4 不良反应预警

**场景**：检验报告上传后，自动分析趋势和用药关联。

**逻辑**：
```
最新检验数据 + 用药方案
    ↓ 规则引擎 + LLM
异常指标趋势 + CTCAE 分级 + 用药关联
    ↓
预警 + 处理建议
```

**输出**：
```
⚠️ 不良反应预警
1. 骨髓抑制趋势：WBC 4.1→3.2→2.8（连续3次下降）
   - CTCAE 分级：II度
   - 关联用药：培美曲塞+卡铂（已知骨髓抑制风险）
   - 建议：下周期考虑减量或 G-CSF 预防性使用

2. 肾功能关注：Cr 88→95→102（上升趋势）
   - 尚在正常范围，但趋势需关注
   - 卡铂肾毒性相关
```

#### 4.5.5 对话式追问

**场景**：结构化推荐不够，医生想进一步追问细节。

**关键设计**：点击"进入对话"后，自动携带患者上下文进入医疗助手对话。

```
时间线数据 → 构建患者摘要 prompt
    ↓
extra_system 注入到 ChatService
    ↓
医疗助手对话（医生可自由追问）
```

**与医疗助手的关系**：AI 辅助诊疗的对话式追问就是医疗助手（Agent Chat），区别在于自动携带了该患者的完整上下文。不是新建对话入口，是已有 `ChatService.start_conversation(RunRequest)` 的一个预填充调用。

---

## 五、后端 API 设计

### 5.1 患者管理

```
GET    /api/medical/patients              # 患者列表（搜索/筛选）
POST   /api/medical/patients              # 新增患者
GET    /api/medical/patients/{id}         # 患者详情
PUT    /api/medical/patients/{id}         # 更新患者信息
```

### 5.2 时间线

```
GET    /api/medical/patients/{id}/timeline        # 时间线事件列表
POST   /api/medical/patients/{id}/timeline        # 新增时间线事件
GET    /api/medical/patients/{id}/timeline/{eid}   # 事件详情
PUT    /api/medical/patients/{id}/timeline/{eid}   # 更新事件
```

### 5.3 报告整理

```
POST   /api/medical/patients/{id}/reports/upload   # 上传报告 → 识别 → 提取
GET    /api/medical/patients/{id}/reports/{rid}     # 查看提取结果
PUT    /api/medical/patients/{id}/reports/{rid}     # 确认/修正提取结果
```

### 5.4 病历生成

```
POST   /api/medical/patients/{id}/documents/generate   # 生成病历草稿
PUT    /api/medical/patients/{id}/documents/{did}      # 更新/确认病历
```

### 5.5 AI 辅助诊疗

```
POST   /api/medical/patients/{id}/ai/summary           # 患者全景摘要
POST   /api/medical/patients/{id}/ai/treatment-plan    # 治疗方案推荐
POST   /api/medical/patients/{id}/ai/recist            # RECIST 疗效评估
POST   /api/medical/patients/{id}/ai/adverse-alert     # 不良反应预警
POST   /api/medical/patients/{id}/ai/chat              # 进入对话（携带患者上下文）
```

---

## 六、与现有代码的映射

| 新模块 | 现有文件 | 改造 |
|--------|---------|------|
| 患者列表 | 新建 PatientListPage.tsx | 新增 |
| 患者详情+时间线 | 新建 PatientDetailPage.tsx | 新增，替代 SmartRecordPage |
| 报告整理 | SmartRecordPage FileTab 部分 | 改造为患者下的上传面板 |
| 病历生成 | SmartRecordPage DictateTab 部分 | 改造为基于时间线数据生成 |
| AI 辅助诊疗 | 新建 AIAssistPanel 组件 | 新增，5 个子功能 |
| 后端患者路由 | 新建 patients.py | 新增 |
| 后端报告路由 | record.py /upload | 改造为 /patients/{id}/reports/upload |
| 后端病历路由 | record.py /dictate | 改造为 /patients/{id}/documents/generate |
| 后端 AI 辅助路由 | 新建 ai_assist.py | 新增：5 个端点 |
| 类型定义 | types.ts | 重写：Patient、TimelineEvent、ReportExtraction |
| MedicalStore | persistence/store.py | 扩展：患者 CRUD、时间线查询 |
| 医疗助手对话 | agent.py ChatService | 复用：extra_system 注入患者上下文 |

---

## 七、实施步骤

1. **types.ts** — 新增 Patient、TimelineEvent、ReportExtraction 等类型
2. **后端 patients.py** — 患者管理 + 时间线 API
3. **后端 ai_assist.py** — AI 辅助诊疗 5 个端点
4. **前端 PatientListPage** — 患者列表页
5. **前端 PatientDetailPage** — 患者详情 + 时间线
6. **报告整理** — 上传面板 + AI 提取（接入现有 upload 路由）
7. **病历生成** — 基于时间线数据的文书生成
8. **AI 辅助诊疗面板** — 全景摘要/方案推荐/疗效评估/不良反应预警/对话入口
9. **MedicalWorkbenchPage** — 菜单入口调整
