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

医疗域分为 **5 个核心功能**，质子治疗相关内容融入智能病历作为肿瘤专科子模块：

| 菜单名 | 页面路由 | 工具名 | REST 端点 | Agent 执行内容 |
|---|---|---|---|---|
| 智能病历 | `/medical/smart-record` | `smart_medical_record` | `POST /api/medical/records/organize` | 解析文件 → 结构化肿瘤病历（TNM分期/IHC/基因检测/化疗方案/放疗记录/irAE/靶向匹配/**质子适应症评估**) |
| 医学翻译 | `/medical/translate` | `medical_translate` | `POST /api/medical/translate` | 中英互译 + 肿瘤学/放射医学术语对齐 |
| 搜指南 | `/medical/guideline` | `search_guideline` | `POST /api/medical/guideline/search` | 语义检索 NCCN/CSCO/质子适应症指南 + 摘要生成 |
| 搜药品 | `/medical/drug` | `search_drug` | `POST /api/medical/drug/search` | 肿瘤药品信息 + 化疗方案模板 + CYP3A4相互作用 + 靶向药-突变匹配 |
| 搜文献 | `/medical/literature` | `search_literature` | `POST /api/medical/literature/search` | 语义检索文献 + 年会追踪 + 临床试验 + GRADE分级 |

> **关于质子治疗**：模拟定位、治疗计划计算、剂量验证、设备QA是物理/工程流程，依赖专业TPS软件和实际设备，不属于AI智能助手的能力范围。AI只做**适应症评估**（检索指南+推理判断）和**信息管理**（治疗记录整理、随访模板生成），这些融入智能病历的肿瘤专科子模块。

---

### 1.2 各功能详细设计

#### 1.2.1 智能病历（肿瘤专科扩展）

**REST API**

```
POST /api/medical/records/organize
Content-Type: multipart/form-data

请求：
  files: UploadFile[]    — 上传的医学文件（JPG/PNG/PDF/TXT/DOCX）
  specialty: str = "oncology"  — 可选：专科类型，默认肿瘤

响应：
{
  "session_id": "med_rec_abc123",
  "record": {
    "chief_complaint": "右肺上叶占位伴咳嗽2月余",
    "present_illness": "患者2月前体检发现右肺上叶占位...",
    "past_history": "高血压病史5年，规律服药...",
    "physical_examination": "PS评分 1分，KPS 90分...",
    "auxiliary_examination": "胸部CT示右肺上叶3.2cm占位...",

    "── 肿瘤专科字段 ──": "",
    "tnm_staging": {
      "t": "T2a", "n": "N0", "m": "M0",
      "stage": "IB", "edition": "AJCC 8th",
      "basis": "胸部CT+PET-CT+头颅MRI"
    },
    "pathology": {
      "type": "非小细胞肺癌",
      "subtype": "腺癌",
      "grade": "中分化",
      "ihc": { "TTF-1": "+", "Napsin-A": "+", "P40": "-", "Ki-67": "25%" },
      "molecular": {
        "EGFR": "19del",
        "ALK": "阴性",
        "ROS1": "阴性",
        "PD-L1 TPS": "60%"
      }
    },
    "treatment_plan": {
      "intention": "根治性",
      "modalities": ["手术", "辅助化疗", "免疫治疗"],
      "chemo_protocol": {
        "name": "培美曲塞+顺铂",
        "cycles_planned": 4,
        "dose": { "pemetrexed": "500mg/m² d1", "cisplatin": "75mg/m² d1" },
        "cycle_length": "21天"
      },
      "targeted_therapy": {
        "drug": "奥希替尼",
        "indication": "EGFR 19del阳性",
        "dose": "80mg qd"
      }
    },
    "performance_status": { "ecog": 1, "kps": 90 },
    "comorbidity_scores": { "charlson": 2 },

    "── 既往治疗记录 ──": "",
    "prior_treatments": [
      {
        "type": "化疗",
        "regimen": "培美曲塞+顺铂",
        "start_date": "2025-03-15",
        "end_date": "2025-06-20",
        "cycles_completed": 4,
        "best_response": "PR",
        "toxicity": { "grade3": ["中性粒细胞减少"], "grade2": ["恶心"] },
        "dose_modifications": "第3周期顺铂减量25%"
      }
    ],
    "irae_records": [],
    "radiation_records": [],

    "── 质子治疗信息 ──": "",
    "proton_eligibility": {
      "indication_level": "conditional",    // "strong" | "conditional" | "investigational" | "not_indicated"
      "indication_basis": [
        "局部晚期NSCLC，质子治疗可降低正常肺组织剂量",
        "NCCN NSCLC指南：质子治疗为Conditional适应症"
      ],
      "guideline_refs": [
        { "source": "NCCN NSCLC V5.2025", "recommendation": "特定情况下可考虑质子治疗" },
        { "source": "ASTRO Model Policy", "recommendation": "Conditional适应症" }
      ],
      "special_considerations": ["需进一步影像评估确定OAR距离"]
    }
  },
  "files_processed": 3,
  "warnings": ["免疫组化部分标记物结果待补充", "基因检测仅覆盖8基因面板"]
}
```

**Agent 执行内容**

1. 调用 `smart_medical_record` 工具提取文件内容（OCR / 文本提取）
2. 按肿瘤专科病历结构整理：主诉 → 现病史 → 既往史 → 体格检查(含PS评分) → 辅助检查 → TNM分期 → 病理(含IHC/分子检测) → 治疗方案 → 既往治疗记录
3. 自动提取并填充 TNM 分期（依据 AJCC 8版标准）
4. 自动匹配化疗方案模板（基于病理类型+分期+PS评分）
5. 自动匹配靶向药-基因突变对（基于分子检测结果）
6. **质子治疗适应症评估**（基于诊断+部位+年龄，检索指南判定适应症等级，附指南依据）
7. 诊断需标注置信度；信息不完整段标注"信息不足"
8. PHI 信息由 `PhiRedactHook` 钩子自动脱敏

**肿瘤病历结构 vs 通用病历结构对比**

| 字段 | 通用病历 | 肿瘤专科病历（新增/扩展） |
|---|---|---|
| 主诉/现病史/既往史 | ✓ | ✓ |
| 体格检查 | ✓ | ✓（+ ECOG/KPS 评分） |
| 辅助检查 | ✓ | ✓（+ 影像分期依据） |
| TNM 分期 | ✗ | ✓（T/N/M/Stage/Edition/Basis） |
| 病理 | ✗ | ✓（类型/亚型/分级/IHC/分子检测） |
| 治疗方案 | 简要文字 | ✓（意图/模式/化疗方案模板/靶向药/免疫） |
| 既往治疗记录 | ✗ | ✓（方案/周期/疗效/毒性/减量） |
| irAE 记录 | ✗ | ✓（CTCAE分级/处理/转归） |
| 放疗记录 | ✗ | ✓（靶区/剂量/技术） |
| 合并症评分 | ✗ | ✓（Charlson指数） |
| 质子适应症评估 | ✗ | ✓（适应症等级/指南依据/特殊注意事项） |

**现有插件能力映射**

| 能力 | 来源 | 说明 |
|---|---|---|
| 文件上传 + OCR | 系统 `RunRequest.files` + `smart_medical_record` 工具 | 支持 JPG/PNG/PDF/TXT/DOCX |
| 结构化整理 | Agent + 智能病历 SKILL.md | 肿瘤专科 SKILL.md 定义行为规范 |
| TNM 分期提取 | `smart_medical_record` 工具扩展 | 内置 AJCC 8版分期规则 |
| 化疗方案匹配 | `chemo_protocol` 工具 | 基于病理+分期+PS查询方案模板库 |
| 靶向药-突变匹配 | `targeted_match` 工具 | 基于分子检测结果匹配NCCN推荐 |
| 质子适应症评估 | `proton_eligibility` 工具 | 基于诊断+部位+年龄检索指南判定适应症等级 |
| PHI 脱敏 | `PhiRedactHook` 钩子 | PRE_TOOL 钩子，仅对医疗域工具生效 |
| 结果持久化 | `MedicalStore` | 存入 `pc_medical_records` + `pc_oncology_staging` + `pc_oncology_treatments` |

---

#### 1.2.2 医学翻译（肿瘤学/放射医学扩展）

**REST API**

```
POST /api/medical/translate
Content-Type: application/json

请求：
{
  "text": "非小细胞肺癌EGFR突变阳性患者一线推荐奥希替尼",
  "direction": "zh2en",           // "zh2en" | "en2zh"
  "specialty": "oncology"         // 可选："oncology" | "radiation_oncology" | "general"
}

响应：
{
  "session_id": "med_trn_def456",
  "translated_text": "First-line osimertinib is recommended for EGFR mutation-positive non-small cell lung cancer patients",
  "terms": [
    { "source": "非小细胞肺癌", "target": "non-small cell lung cancer (NSCLC)", "domain": "oncology" },
    { "source": "EGFR突变阳性", "target": "EGFR mutation-positive", "domain": "molecular_oncology" },
    { "source": "奥希替尼", "target": "osimertinib", "domain": "pharmacology" },
    { "source": "一线推荐", "target": "first-line recommendation", "domain": "guideline" }
  ]
}
```

**肿瘤学/放射医学核心术语库**

| 中文 | 英文 | 领域 |
|---|---|---|
| 非小细胞肺癌 | NSCLC (non-small cell lung cancer) | 肿瘤学 |
| 小细胞肺癌 | SCLC (small cell lung cancer) | 肿瘤学 |
| 免疫组化 | IHC (immunohistochemistry) | 病理学 |
| 程序性死亡受体 | PD-1 (programmed death-1) | 免疫肿瘤学 |
| 程序性死亡配体 | PD-L1 (programmed death-ligand 1) | 免疫肿瘤学 |
| 相对生物学效应 | RBE (relative biological effectiveness) | 放射医学 |
| 扩展布拉格峰 | SOBP (spread-out Bragg peak) | 质子治疗 |
| 笔形束扫描 | PBS (pencil beam scanning) | 质子治疗 |
| 调强质子治疗 | IMPT (intensity-modulated proton therapy) | 质子治疗 |
| 危及器官 | OAR (organ at risk) | 放射医学 |
| 临床靶区 | CTV (clinical target volume) | 放射医学 |
| 计划靶区 | PTV (planning target volume) | 放射医学 |

**Agent 执行内容**

1. 调用 `medical_translate` 工具查询术语库，获取肿瘤学/放射医学术语对照
2. Agent 基于术语对照执行翻译，确保专业术语精确对齐
3. 输出翻译文本 + 术语对照表

---

#### 1.2.3 搜指南（NCCN/CSCO/质子适应症扩展）

**REST API**

```
POST /api/medical/guideline/search
Content-Type: application/json

请求：
{
  "query": "EGFR突变阳性NSCLC一线治疗",
  "specialty": "oncology",         // 可选
  "guideline_source": "nccn",      // 可选："nccn" | "csco" | "esmo" | "asco" | "proton_indication" | "all"
  "category": "treatment",         // 可选："treatment" | "staging" | "follow_up" | "supportive_care"
  "limit": 5
}

响应：
{
  "session_id": "med_gln_789",
  "results": [
    {
      "title": "NCCN Non-Small Cell Lung Cancer (Version 5.2025)",
      "summary": "EGFR exon 19 del/L858R阳性：一线推荐奥希替尼(Category 1)...",
      "source": "NCCN",
      "version": "5.2025",
      "category_evidence": "Category 1",
      "year": 2025,
      "confidence": 0.95,
      "url": "https://..."
    },
    {
      "title": "CSCO 非小细胞肺癌诊疗指南（2025版）",
      "summary": "EGFR敏感突变：I级推荐奥希替尼...",
      "source": "CSCO",
      "version": "2025",
      "category_evidence": "I级推荐",
      "year": 2025,
      "confidence": 0.92,
      "url": "https://..."
    }
  ],
  "total": 3
}
```

**指南检索特有工作流**

| 场景 | 检索策略 | 示例 |
|---|---|---|
| NCCN 指南 | 版本号 + Category分级 + 证据类型 | "NSCLC V5.2025, Category 1" |
| CSCO 指南 | 推荐分级（I/II/III级）+ 证据级别 | "I级推荐，1A类证据" |
| 质子治疗适应症 | 指南来源 + 适应症等级 | "NCCN推荐儿童实体瘤质子治疗" |
| 随访指南 | 术后/放化疗后随访时间点 | "NSCLC术后随访：前2年每6月CT" |
| 支持治疗 | 骨改良药物/G-CSF/止吐 | "高致吐方案止吐推荐" |

**Agent 执行内容**

1. 调用 `search_guideline` 工具检索指南数据库
2. Agent 对返回结果生成摘要，提炼与查询相关的关键推荐
3. NCCN 指南标注 Category 分级（1/2A/2B/3），CSCO 指南标注推荐分级
4. 质子适应症指南标注适应症等级
5. 按置信度和证据级别排序输出

---

#### 1.2.4 搜药品（肿瘤药品扩展）

**REST API**

```
POST /api/medical/drug/search
Content-Type: application/json

请求：
{
  "drug_name": "奥希替尼",
  "query_type": "interaction"      // "info" | "interaction" | "dosage" | "protocol" | "target_match"
}

响应（query_type = "info"）：
{
  "session_id": "med_drg_012",
  "drug_info": {
    "generic_name": "奥希替尼",
    "brand_names": ["泰瑞沙", "Tagrisso"],
    "category": "第三代表皮生长因子受体酪氨酸激酶抑制剂(EGFR-TKI)",
    "indication": "EGFR T790M突变阳性NSCLC/EGFR敏感突变一线",
    "mechanism": "不可逆结合EGFR T790M及敏感突变...",
    "molecular_target": "EGFR",
    "required_mutation": ["EGFR 19del", "EGFR L858R", "EGFR T790M"],
    "contraindications": "...",
    "adverse_reactions": "...",
    "dosage": "80mg qd, 口服",
    "interactions": [
      { "drug": "CYP3A4强诱导剂(如利福平)", "severity": "high", "description": "降低奥希替尼血药浓度", "mechanism": "CYP3A4代谢" },
      { "drug": "CYP3A4强抑制剂(如伊曲康唑)", "severity": "medium", "description": "升高奥希替尼血药浓度", "mechanism": "CYP3A4代谢" }
    ]
  }
}

响应（query_type = "protocol"）：
{
  "session_id": "med_drg_013",
  "protocols": [
    {
      "name": "培美曲塞+顺铂+帕博利珠单抗",
      "indication": "非鳞NSCLC一线（PD-L1 TPS<50%或阴性）",
      "source": "NCCN NSCLC V5.2025",
      "category": "Category 1",
      "drugs": [
        { "name": "培美曲塞", "dose": "500mg/m² d1", "route": "IV" },
        { "name": "顺铂", "dose": "75mg/m² d1", "route": "IV" },
        { "name": "帕博利珠单抗", "dose": "200mg d1", "route": "IV" }
      ],
      "cycle_length": "21天",
      "cycles": 4,
      "premedication": ["地塞米松4mg bid ×3天(培美曲塞前1天起)", "维生素B12 1000μg IM q9w", "叶酸400μg qd"],
      "dose_modifications": [...],
      "monitoring": ["血常规q周期", "肝肾功能q周期", "甲状腺功能q3周期"]
    }
  ]
}

响应（query_type = "target_match"）：
{
  "session_id": "med_drg_014",
  "matches": [
    {
      "mutation": "EGFR 19del",
      "drug": "奥希替尼",
      "line": "一线",
      "evidence_level": "Category 1",
      "guideline": "NCCN NSCLC V5.2025",
      "notes": "优于一代TKI(FLAURA研究)"
    },
    {
      "mutation": "EGFR 19del",
      "drug": "埃克替尼/吉非替尼/厄洛替尼",
      "line": "一线(替代)",
      "evidence_level": "Category 1",
      "guideline": "NCCN NSCLC V5.2025",
      "notes": "奥希替尼为优选"
    }
  ]
}
```

**肿瘤药品特有维度**

| 维度 | 说明 | 示例 |
|---|---|---|
| 药品分类 | 化疗/靶向/免疫/支持治疗 | 培美曲塞(化疗)、奥希替尼(靶向)、帕博利珠单抗(免疫) |
| 分子靶点 | 靶向药需标注作用靶点 | EGFR、ALK、ROS1、BRAF、HER2、KRAS G12C |
| 必需基因检测 | 靶向药使用前必需的检测 | 奥希替尼→EGFR突变; 克唑替尼→ALK融合 |
| 化疗方案模板 | 多药联合方案+剂量+周期+预处理 | 培美曲塞+顺铂方案含预处理要求 |
| CYP3A4 相互作用 | 肿瘤药代谢途径关键交互 | 奥希替尼经CYP3A4代谢，避免强诱导剂 |
| 剂量调整规则 | 基于毒性的减量方案 | 3级中性粒细胞减少→下一周期减量25% |

**Agent 执行内容**

1. 调用 `search_drug` 工具查询药品数据库
2. 根据 `query_type` 聚焦返回：
   - `info`：基本信息 + 分子靶点 + 必需检测 + CYP3A4相互作用
   - `interaction`：药物相互作用（聚焦CYP酶系）
   - `dosage`：用法用量 + 特殊人群调整
   - `protocol`：化疗方案模板（多药联合+剂量+周期+预处理+监测）
   - `target_match`：靶向药-基因突变匹配（基于NCCN推荐）
3. 相互作用分析标注 CYP3A4 代谢途径和严重程度

---

#### 1.2.5 搜文献（肿瘤文献扩展）

**REST API**

```
POST /api/medical/literature/search
Content-Type: application/json

请求：
{
  "query": "osimertinib first-line NSCLC EGFR",
  "year_from": 2020,
  "year_to": 2025,
  "study_type": "rct",            // 可选："rct" | "meta" | "cohort" | "case_series" | "all"
  "conference": "asco_2025",      // 可选："asco_2025" | "esmo_2025" | "wclc_2025" | "all"
  "limit": 10
}

响应：
{
  "session_id": "med_lit_345",
  "results": [
    {
      "title": "FLAURA: Osimertinib vs Comparator EGFR-TKI as First-Line Treatment...",
      "authors": "Soria JC, et al.",
      "journal": "New England Journal of Medicine",
      "year": 2018,
      "doi": "10.1056/NEJMoa1713137",
      "abstract": "Objective: To compare osimertinib with gefitinib or erlotinib...",
      "study_type": "rct",
      "phase": "III",
      "grade": "A",
      "confidence": 0.95,
      "is_conference_abstract": false
    },
    {
      "title": "LAURA: Osimertinib after chemoradiotherapy in EGFR-mutated NSCLC (ASCO 2025)",
      "authors": "Lu S, et al.",
      "conference": "ASCO 2025",
      "year": 2025,
      "abstract": "Background: LAURA evaluated osimertinib as consolidation...",
      "study_type": "rct",
      "phase": "III",
      "grade": "B",
      "confidence": 0.85,
      "is_conference_abstract": true
    }
  ],
  "total": 7
}
```

**肿瘤文献检索特有维度**

| 维度 | 说明 |
|---|---|
| 研究类型 | RCT / Meta分析 / 队列研究 / 病例系列 |
| 临床试验阶段 | I/II/III期 |
| GRADE 证据分级 | A(高质量)/B(中等)/C(低)/D(极低) |
| 年会追踪 | ASCO/ESMO/WCLC/AACR 最新摘要 |
| 预后评分 | KPS/ECOG/Charlson 相关研究 |

**Agent 执行内容**

1. 调用 `search_literature` 工具检索文献数据库
2. Agent 对返回文献生成中文摘要，提炼研究目的、方法、结论
3. 自动标注研究类型、临床阶段、GRADE 分级
4. 年会摘要标注会议来源
5. 按相关度、证据等级、时效性排序

---

### 1.3 质子适应症评估（智能病历子模块）

质子治疗相关内容不作为独立功能，而是融入智能病历的肿瘤专科子模块。AI 只做适应症评估和信息管理，不做物理/工程流程。

**AI 能做 vs 不能做**

| AI 能做 | AI 不能做（需专业设备/软件） |
|---|---|
| 适应症等级判定（检索NCCN/ASTRO指南+推理） | 模拟定位（CT模拟/MRI融合/4D-CT） |
| 指南依据匹配与引用 | 治疗计划计算（IMPT/PBS参数优化/SOBP设计） |
| 禁忌症筛查 | 剂量验证（RBE校正/输出因子测量） |
| 治疗记录整理与结构化存储 | 计划审核（DVH评估/鲁棒性分析） |
| 随访模板生成 | 治疗实施（IGRT/体位验证） |
| RBE/SOBP/PBS/IMPT 概念解释 | 设备QA（日常/周度/月度/年度） |

**质子治疗适应症分级**

| 等级 | 定义 | 典型适应症 |
|---|---|---|
| Strong | 指南明确推荐，医保覆盖 | 儿童实体瘤、眼部黑色素瘤、颅底脊索瘤/软骨肉瘤 |
| Conditional | 有证据支持，需个体化评估 | 局部晚期前列腺癌、头颈部癌(降低毒性)、再程放疗 |
| Investigational | 临床研究阶段 | 乳腺癌(降低心脏毒性)、肝癌、胰腺癌 |
| Not Indicated | 不推荐或禁忌 | 常规可光子治疗且无特别优势的部位 |

**触发时机**：智能病历整理时，若 `specialty="oncology"` 且病历中涉及放疗相关内容，自动执行质子适应症评估，将评估结果写入 `proton_eligibility` 字段。

### 1.4 域 Agent 会话机制

执行模型参见 domain-bundle-design.md §执行模型。医疗域通过 `DomainLifecycleContext.chat_service` 创建带域技能的 Agent 会话：

```python
chat_service = ctx.chat_service
result = await chat_service.create_session(
    domain="medical",
    message="请整理这些文件为结构化肿瘤病历",
    files=files,
    skill_hint="智能病历",
)
```

---

## 二、UI 设计

> 共享样式规范（glass-card 设计系统、容器/按钮/输入框/加载态等）参见 domain-bundle-design.md §共享样式规范。

### 2.1 侧边栏与工作台入口

启用医疗域后，侧边栏新增**一级入口**，点击进入医疗工作台（内嵌二级菜单 + 功能页面）：

```
┌──────────┐
│ 新任务    │  ← 固定
│ 能力扩展  │  ← 固定
│ 自动化    │  ← 固定
│ 产出物    │  ← 固定
│──────────│
│ 🏥 医疗   │  ← 点击进入医疗工作台（非展开子菜单）
└──────────┘
```

### 2.2 医疗工作台页面（MedicalWorkbenchPage）

工作台是一个完整的工作空间，左侧内嵌二级菜单，右侧展示对应功能页面：

```
┌──────────────────────────────────────────────────────────────────┐
│  🏥 医疗工作台                                                    │
│  ─────────────────────────────────────────────────────────────── │
│                                                                  │
│  ┌──────────┐  ┌──────────────────────────────────────────────┐  │
│  │          │  │                                              │  │
│  │ 智能病历  │  │  ← 当前选中的功能页面内容                      │  │
│  │ 医学翻译  │  │     默认展示"智能病历"                         │  │
│  │ 搜指南    │  │                                              │  │
│  │ 搜药品    │  │                                              │  │
│  │ 搜文献    │  │                                              │  │
│  │          │  │                                              │  │
│  └──────────┘  └──────────────────────────────────────────────┘  │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

**布局规范**

| 区域 | 样式 |
|---|---|
| 工作台容器 | `flex-1 overflow-hidden flex min-w-[950px]` |
| 左侧菜单栏 | `w-[200px] shrink-0 border-r border-[var(--c-border)] px-3 py-4 flex flex-col gap-1` |
| 菜单项 | `px-3 py-2.5 rounded-lg text-[var(--fs-body)] cursor-pointer`，选中 `bg-[var(--c-surface-2)] text-[var(--c-accent)]`，hover `bg-[var(--c-surface-2)]/50` |
| 右侧内容区 | `flex-1 overflow-y-auto`，承载各功能页面 |
| 菜单来源 | `DomainManifest.menus` → 动态渲染，不硬编码 |

**交互流程**

1. 用户点击侧边栏"医疗" → 路由到 `/medical` → 渲染 `MedicalWorkbenchPage`
2. 工作台默认选中第一个菜单项（智能病历），右侧展示对应功能页面
3. 点击左侧菜单项 → 右侧切换功能页面，不跳路由（单页 Tab 模式）
4. 每个功能页面的内部交互逻辑不变

**路由设计**

| 路由 | 页面 | 说明 |
|---|---|---|
| `/medical` | `MedicalWorkbenchPage` | 工作台入口，默认显示智能病历 |
| `/medical?tab=translate` | `MedicalWorkbenchPage` | 工作台，显示医学翻译 |
| `/medical?tab=guideline` | `MedicalWorkbenchPage` | 工作台，显示搜指南 |
| `/medical?tab=drug` | `MedicalWorkbenchPage` | 工作台，显示搜药品 |
| `/medical?tab=literature` | `MedicalWorkbenchPage` | 工作台，显示搜文献 |

### 2.3 智能病历页面（SmartRecordPage）

**页面结构**

```
┌──────────────────────────────────────────────────────────────┐
│  智能病历 · 肿瘤专科                                           │
│  上传文件，AI 自动整理成结构化肿瘤病历                            │
│  ─────────────────────────────────────────────────────────── │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │                                                        │  │
│  │    📎 拖拽上传或点击选择文件                             │  │
│  │       支持 JPG、PNG、PDF、TXT、DOCX                     │  │
│  │                                                        │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  已上传：                                                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │📄 门诊记录│ │🖼️ CT扫描 │ │🧬 基因检测│ │📋 病理报告│       │
│  │  PDF 2.1MB│ │ DICOM 12MB│ │  PDF 0.5MB│ │  PDF 1.2MB│       │
│  │       ✕  │ │       ✕  │ │       ✕  │ │       ✕  │       │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘       │
│                                                              │
│  [整理病历]                                                   │
│                                                              │
│  ── 结构化肿瘤病历 ──────────────────────────────────────────  │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  主诉：右肺上叶占位伴咳嗽2月余                            │  │
│  │  现病史：患者2月前体检发现右肺上叶占位...                  │  │
│  │  既往史：高血压病史5年，规律服药...                        │  │
│  │  体格检查：PS评分 ECOG 1分，KPS 90分                     │  │
│  │  辅助检查：胸部CT示右肺上叶3.2cm占位...                   │  │
│  │                                                          │  │
│  │  ── TNM 分期 ──────────────────────────────             │  │
│  │  T2a N0 M0 → Stage IB（AJCC 8th）                       │  │
│  │  分期依据：胸部CT + PET-CT + 头颅MRI                     │  │
│  │                                                          │  │
│  │  ── 病理 ──────────────────────────────                 │  │
│  │  类型：非小细胞肺癌-腺癌（中分化）                         │  │
│  │  免疫组化：TTF-1(+) Napsin-A(+) P40(-) Ki-67(25%)       │  │
│  │  分子检测：EGFR 19del(+) ALK(-) PD-L1 TPS 60%            │  │
│  │                                                          │  │
│  │  ── 治疗方案 ──────────────────────────────             │  │
│  │  意图：根治性                                             │  │
│  │  模式：手术 + 辅助化疗 + 靶向治疗                          │  │
│  │  靶向药：奥希替尼 80mg qd（EGFR 19del阳性）              │  │
│  │  化疗方案：培美曲塞500mg/m² + 顺铂75mg/m² d1 q21d ×4    │  │
│  │                                                          │  │
│  │  ⚠️ 警告：免疫组化部分标记待补充；基因检测仅8基因面板     │  │
│  │                                                          │  │
│  │  ── 质子治疗适应症评估 ──────────────────────────        │  │
│  │  🟡 Conditional（有条件适应症）                          │  │
│  │  依据：局部晚期NSCLC，质子可降低正常肺组织剂量            │  │
│  │  指南：NCCN NSCLC V5.2025 / ASTRO Model Policy          │  │
│  │  注意：需进一步影像评估确定OAR距离                        │  │
│  └────────────────────────────────────────────────────────┘  │
│  [复制] [导出PDF] [发送到对话]                                 │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**交互流程**

1. 用户拖拽或点击上传医学文件 → 文件列表实时显示（图标按类型区分：门诊记录📄/影像🖼️/基因检测🧬/病理报告📋）
2. 点击"整理病历" → `POST /api/medical/records/organize` → 显示加载态
3. 返回结构化肿瘤病历 → 展示结果卡片（含 TNM 分期、病理、治疗方案等专有区块）
4. 底部操作："复制"剪贴板复制、"导出PDF"调用浏览器打印、"发送到对话"调用 `setActiveSection('chat')` + `setPendingComposerPrefill()`

### 2.4 医学翻译页面（MedicalTranslatePage）

**页面结构**

```
┌──────────────────────────────────────────────────────┐
│  医学翻译                                             │
│  中英肿瘤学/放射医学术语专业翻译                        │
│  ─────────────────────────────────────────────────── │
│                                                      │
│  [中 → 英]  [英 → 中]                                 │
│                                                      │
│  专科领域：[肿瘤学 ▾]  ← 可选：肿瘤学/放射医学/通用      │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │  输入原文...                                     │  │
│  │  支持 Drag & Drop 文本文件                       │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  [翻译]                                               │
│                                                      │
│  ── 翻译结果 ──────────────────────────────────────  │
│  ┌────────────────────────────────────────────────┐  │
│  │  First-line osimertinib is recommended for      │  │
│  │  EGFR mutation-positive NSCLC patients          │  │
│  │                                                │  │
│  │  ── 术语对照 ──────────────────────────────    │  │
│  │  非小细胞肺癌     →  NSCLC (non-small cell...)  │  │
│  │  EGFR突变阳性    →  EGFR mutation-positive     │  │
│  │  奥希替尼        →  osimertinib                 │  │
│  │  一线推荐        →  first-line recommendation   │  │
│  └────────────────────────────────────────────────┘  │
│  [复制] [发送到对话]                                    │
│                                                      │
└──────────────────────────────────────────────────────┘
```

**交互流程**

1. 选择翻译方向（中→英 / 英→中）+ 选择专科领域
2. 输入或拖拽文本 → 点击"翻译" → `POST /api/medical/translate`
3. 展示翻译文本 + 术语对照表（肿瘤学/放射医学术语高亮标注）
4. "复制"剪贴板、"发送到对话"跳转聊天预填

### 2.5 搜指南页面（SearchGuidelinePage）

**页面结构**

```
┌──────────────────────────────────────────────────────┐
│  搜指南                                               │
│  语义检索 NCCN/CSCO/质子适应症指南                      │
│  ─────────────────────────────────────────────────── │
│                                                      │
│  ┌──────────────────────────────────┐  [搜索]        │
│  │  输入关键词或描述...               │               │
│  └──────────────────────────────────┘               │
│                                                      │
│  指南来源：[全部 ▾]  分类：[全部 ▾]                      │
│  来源选项：NCCN / CSCO / ESMO / ASCO / 质子适应症      │
│  分类选项：治疗 / 分期 / 随访 / 支持治疗                  │
│                                                      │
│  搜索结果（3条）                                       │
│  ┌────────────────────────────────────────────────┐  │
│  │  📋 NCCN NSCLC (Version 5.2025)                 │  │
│  │  EGFR exon 19 del/L858R阳性：一线推荐奥希替尼     │  │
│  │  Category 1 · 2025 · 置信度 95%                  │  │
│  │  [查看详情] [发送到对话]                          │  │
│  └────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────┐  │
│  │  📋 CSCO 非小细胞肺癌诊疗指南（2025版）            │  │
│  │  EGFR敏感突变：I级推荐奥希替尼...                 │  │
│  │  I级推荐 · 2025 · 置信度 92%                     │  │
│  │  [查看详情] [发送到对话]                          │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
└──────────────────────────────────────────────────────┘
```

**交互流程**

1. 输入关键词 → 可选筛选指南来源和分类 → 搜索
2. 展示搜索结果卡片（标注指南来源版本、Category/推荐分级、证据级别）
3. "查看详情"展开完整摘要 / "发送到对话"跳转聊天预填

### 2.6 搜药品页面（SearchDrugPage）

**页面结构**

```
┌──────────────────────────────────────────────────────┐
│  搜药品                                               │
│  查询肿瘤药品、化疗方案、靶向药-突变匹配                 │
│  ─────────────────────────────────────────────────── │
│                                                      │
│  ┌──────────────────────────────────┐               │
│  │  输入药品名称或基因突变...         │               │
│  └──────────────────────────────────┘               │
│                                                      │
│  查询类型：[基本信息] [用法用量] [相互作用] [化疗方案] [靶向匹配] │
│                                                      │
│  [搜索]                                               │
│                                                      │
│  ── 药品信息 ──────────────────────────────────────  │
│  ┌────────────────────────────────────────────────┐  │
│  │  奥希替尼 (Tagrisso/泰瑞沙)                     │  │
│  │  第三代 EGFR-TKI                                │  │
│  │  分子靶点：EGFR                                 │  │
│  │  必需检测：EGFR 19del / L858R / T790M           │  │
│  │                                                  │  │
│  │  ── CYP3A4 相互作用 ──────────────────────      │  │
│  │  🔴 利福平(CYP3A4强诱导剂) — 降低血药浓度(高风险)│  │
│  │  🟡 伊曲康唑(CYP3A4强抑制剂) — 升高血药浓度(中) │  │
│  └────────────────────────────────────────────────┘  │
│  [复制] [发送到对话]                                    │
│                                                      │
└──────────────────────────────────────────────────────┘
```

**交互流程**

1. 输入药品名或基因突变 → 选择查询类型（新增"化疗方案"和"靶向匹配"）
2. 展示药品信息卡片
3. 化疗方案模式下展示多药联合方案详情（剂量/周期/预处理/监测）
4. 靶向匹配模式下展示突变-药物对照表（基于NCCN推荐）
5. 相互作用分析以 CYP3A4 代谢途径彩色标注

### 2.7 搜文献页面（SearchLiteraturePage）

**页面结构**

```
┌──────────────────────────────────────────────────────┐
│  搜文献                                               │
│  语义检索肿瘤学文献，AI 生成摘要 + GRADE 分级           │
│  ─────────────────────────────────────────────────── │
│                                                      │
│  ┌──────────────────────────────────┐  [搜索]        │
│  │  输入关键词或描述...               │               │
│  └──────────────────────────────────┘               │
│                                                      │
│  筛选：年份 [2020] — [2025]                            │
│  研究类型：[全部 ▾]  年会：[全部 ▾]                      │
│  类型选项：RCT / Meta分析 / 队列 / 病例系列             │
│  年会选项：ASCO 2025 / ESMO 2025 / WCLC 2025          │
│                                                      │
│  搜索结果（7条）                                       │
│  ┌────────────────────────────────────────────────┐  │
│  │  📄 FLAURA: Osimertinib vs Comparator...        │  │
│  │  Soria JC, et al. · NEJM · 2018 · Phase III    │  │
│  │  Objective: To compare osimertinib with...      │  │
│  │  GRADE A · RCT · 置信度 95%                     │  │
│  │  [查看详情] [发送到对话]                          │  │
│  └────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────┐  │
│  │  🎤 LAURA: Osimertinib after CRT... (ASCO 2025)│  │
│  │  Lu S, et al. · ASCO 2025 · Phase III          │  │
│  │  GRADE B · RCT · 置信度 85%                     │  │
│  │  [查看详情] [发送到对话]                          │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
└──────────────────────────────────────────────────────┘
```

**交互流程**

1. 输入关键词 → 可选设置年份范围/研究类型/年会筛选 → 搜索
2. 展示文献结果卡片（标注研究类型、临床阶段、GRADE 分级）
3. 年会摘要标注会议来源和年份
4. "查看详情"展开完整摘要 / DOI 链接外链 / "发送到对话"

---

## 三、表设计

### 3.1 完整 DDL

```sql
-- ============================================================
-- 医疗域数据表（subpackages.medical）
-- 命名规范：pc_medical_* / pc_oncology_* / pc_proton_* 前缀
-- Advisory Lock Key 区间：742200-742299
-- ============================================================

-- 患者信息表
CREATE TABLE IF NOT EXISTS pc_medical_patients (
    id              SERIAL PRIMARY KEY,
    patient_code    VARCHAR(64)  NOT NULL UNIQUE,   -- 患者编码（系统生成）
    name_hash       VARCHAR(128),                    -- 姓名哈希（PHI 脱敏）
    gender          VARCHAR(8),                      -- 性别
    birth_year      INT,                             -- 出生年份（仅存年份，不存完整日期）
    workspace_id    VARCHAR(64)  NOT NULL DEFAULT 'default',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_medical_patients_workspace ON pc_medical_patients(workspace_id);
CREATE INDEX IF NOT EXISTS idx_medical_patients_code      ON pc_medical_patients(patient_code);

-- 病历记录表
CREATE TABLE IF NOT EXISTS pc_medical_records (
    id              SERIAL PRIMARY KEY,
    session_id      VARCHAR(64)  NOT NULL,           -- Agent 会话 ID
    patient_id      INT          REFERENCES pc_medical_patients(id),
    workspace_id    VARCHAR(64)  NOT NULL DEFAULT 'default',
    specialty       VARCHAR(32)  NOT NULL DEFAULT 'oncology',  -- "oncology" | "general"
    files           JSONB        NOT NULL DEFAULT '[]',  -- 上传文件元数据列表
    record          JSONB,                              -- 结构化病历（通用字段）
    oncology_detail JSONB,                              -- 肿瘤专科详情（TNM/病理/治疗方案/irAE等）
    status          VARCHAR(16)  NOT NULL DEFAULT 'pending',  -- pending / completed / failed
    error_message   TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_medical_records_session    ON pc_medical_records(session_id);
CREATE INDEX IF NOT EXISTS idx_medical_records_patient    ON pc_medical_records(patient_id);
CREATE INDEX IF NOT EXISTS idx_medical_records_workspace  ON pc_medical_records(workspace_id);
CREATE INDEX IF NOT EXISTS idx_medical_records_specialty  ON pc_medical_records(specialty);
CREATE INDEX IF NOT EXISTS idx_medical_records_status     ON pc_medical_records(status);

-- 肿瘤分期表（含质子适应症评估）
CREATE TABLE IF NOT EXISTS pc_oncology_staging (
    id              SERIAL PRIMARY KEY,
    record_id       INT          NOT NULL REFERENCES pc_medical_records(id) ON DELETE CASCADE,
    patient_id      INT          NOT NULL REFERENCES pc_medical_patients(id),
    workspace_id    VARCHAR(64)  NOT NULL DEFAULT 'default',
    cancer_type     VARCHAR(128) NOT NULL,            -- "非小细胞肺癌"
    tnm_t           VARCHAR(16),                      -- "T2a"
    tnm_n           VARCHAR(16),                      -- "N0"
    tnm_m           VARCHAR(16),                      -- "M0"
    stage           VARCHAR(16),                      -- "IB"
    staging_system  VARCHAR(32)  DEFAULT 'AJCC_8th',  -- 分期系统版本
    staging_basis   TEXT,                              -- 分期依据
    pathology_type  VARCHAR(128),                      -- 病理类型
    pathology_sub   VARCHAR(128),                      -- 病理亚型
    ihc_markers     JSONB        DEFAULT '{}',         -- 免疫组化标记
    molecular_test  JSONB        DEFAULT '{}',         -- 分子检测结果
    performance_status JSONB     DEFAULT '{}',         -- ECOG/KPS

    -- 质子适应症评估（智能病历子模块，仅AI可执行的判断）
    proton_indication_level  VARCHAR(32),              -- "strong" | "conditional" | "investigational" | "not_indicated" | NULL(未评估)
    proton_indication_basis  JSONB        DEFAULT '[]', -- 适应症依据列表
    proton_guideline_refs    JSONB        DEFAULT '[]', -- 指南参考
    proton_special_considerations JSONB  DEFAULT '[]', -- 特殊注意事项

    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_oncology_staging_record   ON pc_oncology_staging(record_id);
CREATE INDEX IF NOT EXISTS idx_oncology_staging_patient  ON pc_oncology_staging(patient_id);
CREATE INDEX IF NOT EXISTS idx_oncology_staging_cancer   ON pc_oncology_staging(cancer_type);
CREATE INDEX IF NOT EXISTS idx_oncology_staging_workspace ON pc_oncology_staging(workspace_id);

-- 肿瘤治疗记录表
CREATE TABLE IF NOT EXISTS pc_oncology_treatments (
    id              SERIAL PRIMARY KEY,
    patient_id      INT          NOT NULL REFERENCES pc_medical_patients(id),
    workspace_id    VARCHAR(64)  NOT NULL DEFAULT 'default',
    treatment_type  VARCHAR(32)  NOT NULL,            -- "chemo" | "targeted" | "immunotherapy" | "radiation" | "surgery" | "proton"
    regimen_name    VARCHAR(256),                     -- 方案名称 "培美曲塞+顺铂"
    intention       VARCHAR(32),                      -- "curative" | "palliative" | "adjuvant" | "neoadjuvant"
    start_date      DATE,
    end_date        DATE,
    cycles_planned  INT,                              -- 计划周期数
    cycles_completed INT,                             -- 完成周期数
    drugs           JSONB        DEFAULT '[]',         -- 药物列表[{name, dose, route}]
    best_response   VARCHAR(16),                      -- "CR" | "PR" | "SD" | "PD"
    toxicity        JSONB        DEFAULT '[]',         -- 毒性记录[{grade, description}]
    dose_modifications TEXT,                           -- 减量/延迟说明
    molecular_target VARCHAR(64),                     -- 靶点（靶向药）
    required_mutation VARCHAR(128),                   -- 必需突变（靶向药）
    status          VARCHAR(16)  NOT NULL DEFAULT 'planned',  -- planned / ongoing / completed / discontinued
    notes           TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_oncology_treatments_patient    ON pc_oncology_treatments(patient_id);
CREATE INDEX IF NOT EXISTS idx_oncology_treatments_type       ON pc_oncology_treatments(treatment_type);
CREATE INDEX IF NOT EXISTS idx_oncology_treatments_workspace  ON pc_oncology_treatments(workspace_id);
CREATE INDEX IF NOT EXISTS idx_oncology_treatments_status     ON pc_oncology_treatments(status);

-- 搜索记录表（搜指南 / 搜药品 / 搜文献 通用）
CREATE TABLE IF NOT EXISTS pc_medical_searches (
    id              SERIAL PRIMARY KEY,
    session_id      VARCHAR(64)  NOT NULL,
    workspace_id    VARCHAR(64)  NOT NULL DEFAULT 'default',
    query           TEXT         NOT NULL,
    category        VARCHAR(32)  NOT NULL,           -- "guideline" / "drug" / "literature"
    filters         JSONB        NOT NULL DEFAULT '{}',
    result          JSONB,
    result_count    INT          DEFAULT 0,
    status          VARCHAR(16)  NOT NULL DEFAULT 'pending',
    error_message   TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_medical_searches_session   ON pc_medical_searches(session_id);
CREATE INDEX IF NOT EXISTS idx_medical_searches_workspace ON pc_medical_searches(workspace_id);
CREATE INDEX IF NOT EXISTS idx_medical_searches_category  ON pc_medical_searches(category);
CREATE INDEX IF NOT EXISTS idx_medical_searches_status    ON pc_medical_searches(status);

-- 翻译记录表
CREATE TABLE IF NOT EXISTS pc_medical_translations (
    id              SERIAL PRIMARY KEY,
    session_id      VARCHAR(64)  NOT NULL,
    workspace_id    VARCHAR(64)  NOT NULL DEFAULT 'default',
    source_text     TEXT         NOT NULL,
    direction       VARCHAR(8)   NOT NULL,           -- "zh2en" / "en2zh"
    specialty       VARCHAR(64),                     -- "oncology" / "radiation_oncology" / "general"
    translated_text TEXT,
    terms           JSONB        NOT NULL DEFAULT '[]',
    status          VARCHAR(16)  NOT NULL DEFAULT 'pending',
    error_message   TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_medical_translations_session   ON pc_medical_translations(session_id);
CREATE INDEX IF NOT EXISTS idx_medical_translations_workspace ON pc_medical_translations(workspace_id);
CREATE INDEX IF NOT EXISTS idx_medical_translations_status    ON pc_medical_translations(status);

-- 文件关联表（病历与上传文件的多对多关系）
CREATE TABLE IF NOT EXISTS pc_medical_record_files (
    id              SERIAL PRIMARY KEY,
    record_id       INT          NOT NULL REFERENCES pc_medical_records(id) ON DELETE CASCADE,
    file_name       VARCHAR(256) NOT NULL,
    file_type       VARCHAR(16),                      -- "pdf" / "jpg" / "png" / "txt" / "docx" / "dicom"
    file_size       BIGINT,
    storage_path    TEXT,
    ocr_text        TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_medical_record_files_record ON pc_medical_record_files(record_id);
```

### 3.2 表关系说明

```
pc_medical_patients  1──N  pc_medical_records
pc_medical_patients  1──N  pc_oncology_staging
pc_medical_patients  1──N  pc_oncology_treatments
pc_medical_records   1──1  pc_oncology_staging  （含质子适应症评估）
pc_medical_records   1──N  pc_medical_record_files
pc_medical_searches       （独立，按 category 区分指南/药品/文献）
pc_medical_translations   （独立）
```

### 3.3 Advisory Lock Key 分配

| 子系统 | Lock Key | 区间 |
|--------|----------|------|
| 医疗基础表(patients/records/files) | 742200 | 742200 |
| 肿瘤专科表(staging含质子适应症/treatments) | 742201 | 742201 |
| 搜索/翻译表 | 742202 | 742202 |

### 3.4 幂等迁移

```python
# persistence/ensure_schema.py
from sqlalchemy.ext.asyncio import AsyncEngine

_ONCOLOGY_LOCK_KEY = 742201

async def ensure_medical_schema(engine: AsyncEngine) -> None:
    """幂等建表 — IF NOT EXISTS 保证可重复执行"""
    async with engine.begin() as conn:
        # 基础表（lock 742200）
        await conn.execute(text("SELECT pg_advisory_xact_lock(742200)"))
        await conn.execute("""CREATE TABLE IF NOT EXISTS pc_medical_patients ( ... )""")
        await conn.execute("""CREATE TABLE IF NOT EXISTS pc_medical_records ( ... )""")
        await conn.execute("""CREATE TABLE IF NOT EXISTS pc_medical_record_files ( ... )""")
        # ... 索引同理

    async with engine.begin() as conn:
        # 肿瘤专科表（lock 742201，含质子适应症评估字段）
        await conn.execute(text("SELECT pg_advisory_xact_lock(742201)"))
        await conn.execute("""CREATE TABLE IF NOT EXISTS pc_oncology_staging ( ... )""")
        await conn.execute("""CREATE TABLE IF NOT EXISTS pc_oncology_treatments ( ... )""")
        # ... 索引同理

    async with engine.begin() as conn:
        # 搜索/翻译表（lock 742202）
        await conn.execute(text("SELECT pg_advisory_xact_lock(742202)"))
        await conn.execute("""CREATE TABLE IF NOT EXISTS pc_medical_searches ( ... )""")
        await conn.execute("""CREATE TABLE IF NOT EXISTS pc_medical_translations ( ... )""")
        # ... 索引同理
```

---

## 四、MedicalBundle 实现设计

### 4.1 包结构

```
subpackages.medical/
  pyproject.toml
  src/subpackages.medical/
    __init__.py
    bundle.py                            # MedicalBundle

    tools/                               # Agent 可调用的外部能力
      smart_medical_record.py            # 病历文件解析（OCR/文本提取）+ 肿瘤专科结构化
      medical_translate.py               # 翻译辅助（术语库查询，含肿瘤学/放射医学）
      search_guideline.py                # 指南数据库检索（NCCN/CSCO/质子适应症）
      search_drug.py                     # 药品数据库检索（含化疗方案/靶向匹配/CYP3A4）
      search_literature.py               # 文献数据库检索（含GRADE/年会/临床试验）
      chemo_protocol.py                  # 化疗方案模板查询
      targeted_match.py                  # 靶向药-基因突变匹配
      proton_eligibility.py              # 质子治疗适应症评估（仅AI可执行的判断）

    hooks/
      phi_redact.py                      # PHI 条件脱敏（仅医疗域工具）

    governance/
      hipaa.py                           # HIPAA 合规 Reducer

    routes/
      __init__.py
      agent.py                           # 域 Agent 会话管理
      record.py                          # 智能病历页面 API
      translate.py                       # 医学翻译页面 API
      guideline.py                       # 搜指南页面 API
      drug.py                            # 搜药品页面 API
      literature.py                      # 搜文献页面 API

    persistence/
      medical_store.py                   # 病历/检索结果存储
      oncology_store.py                  # 肿瘤专科数据存储（分期/治疗/质子适应症）
      ensure_schema.py                   # 数据库迁移（幂等，3组advisory lock）

    skills/
      智能病历.SKILL.md                   # 定义 Agent 如何整理肿瘤专科病历
      医学翻译.SKILL.md                   # 定义 Agent 如何做肿瘤学术语翻译
      搜指南.SKILL.md                     # 定义 Agent 如何检索NCCN/CSCO指南
      搜药品.SKILL.md                     # 定义 Agent 如何查询肿瘤药品/方案/靶向匹配
      搜文献.SKILL.md                     # 定义 Agent 如何检索肿瘤文献

  web/                                   # 前端
    src/
      MedicalWorkbenchPage.tsx           # 医疗工作台（左侧菜单+右侧功能页面）
      SmartRecordPage.tsx
      MedicalTranslatePage.tsx
      SearchGuidelinePage.tsx
      SearchDrugPage.tsx
      SearchLiteraturePage.tsx
      components/
        FileUploader.tsx
        RecordCard.tsx
        OncologyDetailCard.tsx          # 肿瘤专科详情（TNM/病理/方案/质子适应症）
        TermTable.tsx
        SearchResults.tsx
        ResultCard.tsx
```

### 4.2 Bundle 实现

```python
# bundle.py
from prismclaw.schema.services.domain import (
    DomainBundle, DomainInfrastructureContext,
    DomainRuntimeContext, DomainLifecycleContext,
    DomainManifest, MenuEntry,
)
from prismclaw.engine.hooks import HookEvent


class MedicalBundle:
    name = "medical"
    required_core_version = ">=0.1.0"
    depends_on: list[str] = []

    def on_build_infrastructure(self, ctx: DomainInfrastructureContext) -> None:
        from .persistence.medical_store import MedicalStore
        from .persistence.oncology_store import OncologyStore
        from .persistence.ensure_schema import ensure_medical_schema
        import asyncio

        store = MedicalStore(ctx.db_engine)
        ctx.register_store("medical", store)

        oncology_store = OncologyStore(ctx.db_engine)
        ctx.register_store("oncology", oncology_store)

        asyncio.get_event_loop().run_until_complete(
            ensure_medical_schema(ctx.db_engine)
        )

    def on_build_runtime(self, ctx: DomainRuntimeContext) -> None:
        # 核心工具 — 5 个基础 + 3 个肿瘤专科（含质子适应症评估）
        from .tools.smart_medical_record import SmartMedicalRecordTool
        from .tools.medical_translate import MedicalTranslateTool
        from .tools.search_guideline import SearchGuidelineTool
        from .tools.search_drug import SearchDrugTool
        from .tools.search_literature import SearchLiteratureTool
        from .tools.chemo_protocol import ChemoProtocolTool
        from .tools.targeted_match import TargetedMatchTool
        from .tools.proton_eligibility import ProtonEligibilityTool

        ctx.register_tool(SmartMedicalRecordTool())
        ctx.register_tool(MedicalTranslateTool())
        ctx.register_tool(SearchGuidelineTool())
        ctx.register_tool(SearchDrugTool())
        ctx.register_tool(SearchLiteratureTool())
        ctx.register_tool(ChemoProtocolTool())
        ctx.register_tool(TargetedMatchTool())
        ctx.register_tool(ProtonEligibilityTool())

        # 钩子 — PHI 脱敏（仅对医疗域工具生效）
        from .hooks.phi_redact import PhiRedactHook
        ctx.register_hook(HookEvent.PRE_TOOL, PhiRedactHook())

        # 治理 — HIPAA 合规
        from .governance.hipaa import HipaaReducer
        ctx.register_governance(HipaaReducer())

        # 技能 — 肿瘤专科 SKILL.md
        from pathlib import Path
        skills_dir = Path(__file__).parent / "skills"
        for skill_file in skills_dir.glob("*.SKILL.md"):
            ctx.register_skill(str(skill_file))

    def on_build_lifecycle(self, ctx: DomainLifecycleContext) -> None:
        from .routes.agent import router as agent_router
        from .routes.record import router as record_router
        from .routes.translate import router as translate_router
        from .routes.guideline import router as guideline_router
        from .routes.drug import router as drug_router
        from .routes.literature import router as literature_router

        ctx.register_route(agent_router)
        ctx.register_route(record_router)
        ctx.register_route(translate_router)
        ctx.register_route(guideline_router)
        ctx.register_route(drug_router)
        ctx.register_route(literature_router)

    def get_manifest(self) -> DomainManifest:
        return DomainManifest(
            key="medical",
            label="医疗",
            icon="stethoscope",
            path="/medical",                    # 工作台路由，不再平铺子路由
            menus=[
                MenuEntry(key="record", label="智能病历", icon="file-text"),
                MenuEntry(key="translate", label="医学翻译", icon="languages"),
                MenuEntry(key="guideline", label="搜指南", icon="book-open"),
                MenuEntry(key="drug", label="搜药品", icon="pill"),
                MenuEntry(key="literature", label="搜文献", icon="search"),
            ],
        )
```

### 4.3 入口点注册

```toml
# pyproject.toml
[project]
name = "prismclaw-medical"
version = "0.1.0"
dependencies = ["prismclaw>=0.1.0,<2.0.0", "packaging>=21.0"]

[project.entry-points."prismclaw.domains"]
medical = "subpackages.medical.integration.bundle:MedicalBundle"
```

### 4.4 REST 路由实现

#### 域 Agent 会话路由

```python
# routes/agent.py
from fastapi import APIRouter
from prismclaw.schema.agent.run_request import RunRequest

router = APIRouter(prefix="/api/medical/agent", tags=["medical"])

@router.post("/run")
async def run_medical_agent(request: RunRequest):
    """创建医疗域 Agent 会话并执行"""
    chat_service = request._chat_service  # 通过依赖注入获取
    result = await chat_service.create_session(
        domain="medical",
        message=request.message,
        files=request.files,
        skill_hint=request.skill_hint,
    )
    return result
```

#### 页面功能 API（薄包装）

```python
# routes/record.py
from fastapi import APIRouter, UploadFile, File

router = APIRouter(prefix="/api/medical/records", tags=["medical"])

@router.post("/organize")
async def organize_record(
    files: list[UploadFile] = File(...),
    specialty: str = "oncology",
):
    """智能病历 — 通过 Agent 执行"""
    chat_service = ...  # 从 DomainLifecycleContext 注入
    result = await chat_service.create_session(
        domain="medical",
        message=f"请整理这些文件为结构化{'肿瘤' if specialty == 'oncology' else ''}病历",
        files=files,
        skill_hint="智能病历",
    )
    return result
```

### 4.5 工具实现示例

```python
# tools/targeted_match.py
from prismclaw.schema.tools import ToolResult

class TargetedMatchTool:
    name = "targeted_match"
    description = "基于基因突变结果匹配靶向药物（NCCN推荐）"
    parameters = {
        "type": "object",
        "properties": {
            "mutation": {"type": "string", "description": "基因突变名称，如 EGFR 19del"},
            "cancer_type": {"type": "string", "description": "肿瘤类型，如 非小细胞肺癌"},
            "line": {"type": "string", "enum": ["first", "second", "later"], "description": "治疗线数"}
        },
        "required": ["mutation", "cancer_type"]
    }
    tool_class = "read"
    side_effect = False
    effect_class = "unknown"

    async def execute(self, args: dict, context) -> ToolResult:
        mutation = args["mutation"]
        cancer_type = args["cancer_type"]
        line = args.get("line", "first")
        result = await self._query_match(mutation, cancer_type, line)
        return ToolResult(output=result)

    async def _query_match(self, mutation: str, cancer_type: str, line: str) -> str:
        # 查询靶向药-突变匹配数据库
        ...
```

```python
# tools/chemo_protocol.py
from prismclaw.schema.tools import ToolResult

class ChemoProtocolTool:
    name = "chemo_protocol"
    description = "查询化疗方案模板（剂量/周期/预处理/监测）"
    parameters = {
        "type": "object",
        "properties": {
            "protocol_name": {"type": "string", "description": "方案名称，如 培美曲塞+顺铂"},
            "cancer_type": {"type": "string", "description": "肿瘤类型"},
            "stage": {"type": "string", "description": "分期"}
        },
        "required": ["cancer_type"]
    }
    tool_class = "read"
    side_effect = False
    effect_class = "unknown"

    async def execute(self, args: dict, context) -> ToolResult:
        result = await self._query_protocol(args)
        return ToolResult(output=result)

    async def _query_protocol(self, args: dict) -> str:
        # 查询化疗方案模板数据库
        ...
```

```python
# tools/proton_eligibility.py
from prismclaw.schema.tools import ToolResult

class ProtonEligibilityTool:
    """质子治疗适应症评估 — 仅做AI可执行的判断（指南检索+推理），不做物理/工程流程"""
    name = "proton_eligibility"
    description = "评估质子治疗适应症等级（基于NCCN/ASTRO指南），仅做信息判断不做治疗计划"
    parameters = {
        "type": "object",
        "properties": {
            "diagnosis": {"type": "string", "description": "诊断，如 室管膜瘤"},
            "age": {"type": "integer", "description": "患者年龄"},
            "tumor_site": {"type": "string", "description": "肿瘤部位"},
            "prior_radiation": {"type": "boolean", "description": "是否既往放疗"}
        },
        "required": ["diagnosis", "age", "tumor_site"]
    }
    tool_class = "read"
    side_effect = False
    effect_class = "unknown"

    async def execute(self, args: dict, context) -> ToolResult:
        result = await self._assess(args)
        return ToolResult(output=result)

    async def _assess(self, args: dict) -> str:
        # 适应症评估逻辑：检索指南数据库 + 推理判定适应症等级
        # 返回：indication_level + indication_basis + guideline_refs + special_considerations
        # 不涉及：治疗计划/剂量计算/QA等物理流程
        ...
        ...
```

### 4.6 PHI 脱敏钩子（扩展肿瘤专科）

```python
# hooks/phi_redact.py
class PhiRedactHook:
    """PHI 脱敏 — 只对医疗域注册的工具生效"""

    _MEDICAL_TOOL_PREFIXES = (
        "smart_medical_record", "medical_translate",
        "search_guideline", "search_drug", "search_literature",
        "chemo_protocol", "targeted_match", "proton_eligibility",
    )

    # 肿瘤专科 PHI 敏感字段
    _ONCOLOGY_PHI_FIELDS = (
        "patient_name", "id_number", "phone", "address",
        "medical_record_number", "insurance_number",
    )

    async def execute(self, context) -> HookResult:
        tool_name = getattr(context, "tool_name", "")
        if not any(tool_name.startswith(p) for p in self._MEDICAL_TOOL_PREFIXES):
            return HookResult(passed=True)
        return self._redact_phi(context)

    def _redact_phi(self, context) -> HookResult:
        # PHI 脱敏逻辑：姓名 → ***、身份证号 → ***、电话 → *** 等
        # 肿瘤专科：患者编码系统生成，不存储明文姓名
        ...
```

---

## 五、前端组件设计

### 5.1 页面组件

#### SmartRecordPage.tsx

```tsx
import { useState, useCallback } from 'react'
import { Upload, FileText, Copy, Send, Download, Dna, Scan } from 'lucide-react'
import { PrimaryButton } from './ui/PrimaryButton'
import { SecondaryButton } from './ui/SecondaryButton'
import { useUiStore } from '../stores'
import { FileUploader } from './components/FileUploader'
import { RecordCard } from './components/RecordCard'
import { OncologyDetailCard } from './components/OncologyDetailCard'

interface UploadedFile {
  name: string
  type: string
  size: number
  file: File
}

interface StructuredRecord {
  chief_complaint: string
  present_illness: string
  past_history: string
  physical_examination: string
  auxiliary_examination: string
  diagnosis: { name: string; confidence: number }[]
  treatment_plan: string
}

interface OncologyDetail {
  tnm_staging: { t: string; n: string; m: string; stage: string; edition: string; basis: string }
  pathology: { type: string; subtype: string; grade: string; ihc: Record<string, string>; molecular: Record<string, string> }
  treatment_plan: {
    intention: string
    modalities: string[]
    chemo_protocol?: { name: string; cycles_planned: number; dose: Record<string, string>; cycle_length: string }
    targeted_therapy?: { drug: string; indication: string; dose: string }
  }
  performance_status: { ecog: number; kps: number }
}

export default function SmartRecordPage() {
  const [files, setFiles] = useState<UploadedFile[]>([])
  const [record, setRecord] = useState<StructuredRecord | null>(null)
  const [oncologyDetail, setOncologyDetail] = useState<OncologyDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const setActiveSection = useUiStore((s) => s.setActiveSection)
  const setPendingComposerPrefill = useUiStore((s) => s.setPendingComposerPrefill)

  const handleOrganize = useCallback(async () => {
    setLoading(true)
    const formData = new FormData()
    files.forEach(f => formData.append('files', f.file))
    formData.append('specialty', 'oncology')
    const res = await fetch('/api/medical/records/organize', { method: 'POST', body: formData })
    const data = await res.json()
    setRecord(data.record)
    setOncologyDetail(data.oncology_detail)
    setLoading(false)
  }, [files])

  return (
    <div className="flex-1 overflow-hidden flex flex-col min-w-[950px]">
      <div className="px-8 pt-7 pb-5 shrink-0 max-w-[1200px] mx-auto w-full">
        <h1 className="text-[var(--fs-title)] font-semibold text-[var(--c-text)]">智能病历 · 肿瘤专科</h1>
        <p className="text-[var(--fs-body)] text-[var(--c-text-secondary)] mt-1">
          上传文件，AI 自动整理成结构化肿瘤病历
        </p>
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6 max-w-[1200px] mx-auto w-full">
        <FileUploader onUpload={handleUpload} />

        {files.length > 0 && (
          <div className="flex gap-3 mt-4 flex-wrap">
            {files.map((f, i) => (
              <FileCard key={i} file={f} onRemove={() => removeFile(i)} />
            ))}
          </div>
        )}

        <div className="mt-4">
          <PrimaryButton
            className="h-10 px-6 rounded-lg font-semibold"
            disabled={files.length === 0 || loading}
            onClick={handleOrganize}
          >
            {loading ? 'AI 整理中...' : '整理病历'}
          </PrimaryButton>
        </div>

        {record && (
          <div className="mt-6">
            <RecordCard record={record} />
            {oncologyDetail && <OncologyDetailCard detail={oncologyDetail} />}
            <div className="flex gap-3 mt-4">
              <SecondaryButton className="h-9 px-4 rounded-lg font-medium" onClick={handleCopy}>
                <Copy className="w-4 h-4 mr-1.5" /> 复制
              </SecondaryButton>
              <SecondaryButton className="h-9 px-4 rounded-lg font-medium" onClick={handleExportPdf}>
                <Download className="w-4 h-4 mr-1.5" /> 导出PDF
              </SecondaryButton>
              <SecondaryButton className="h-9 px-4 rounded-lg font-medium" onClick={handleSendToChat}>
                <Send className="w-4 h-4 mr-1.5" /> 发送到对话
              </SecondaryButton>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
```

### 5.2 肿瘤专科子组件

#### OncologyDetailCard.tsx

```tsx
interface Props {
  detail: {
    tnm_staging: { t: string; n: string; m: string; stage: string; edition: string; basis: string }
    pathology: { type: string; subtype: string; grade: string; ihc: Record<string, string>; molecular: Record<string, string> }
    treatment_plan: {
      intention: string
      modalities: string[]
      chemo_protocol?: { name: string; cycles_planned: number; dose: Record<string, string>; cycle_length: string }
      targeted_therapy?: { drug: string; indication: string; dose: string }
    }
    performance_status: { ecog: number; kps: number }
    proton_eligibility?: {
      indication_level: 'strong' | 'conditional' | 'investigational' | 'not_indicated'
      indication_basis: string[]
      guideline_refs: { source: string; recommendation: string }[]
      special_considerations: string[]
    }
  }
}

/** 肿瘤专科详情卡片
 *  样式：glass-card px-6 py-5，分 TNM/病理/治疗方案/质子适应症 四大区块
 *  TNM：分期结果 text-[var(--c-accent)] font-bold font-mono
 *  IHC/分子：标签组 inline-flex gap-2 flex-wrap
 *  化疗方案：key-value 布局
 *  靶向药：匹配标记 bg-[var(--c-success)]/10
 *  质子适应症：等级徽章(Strong绿/Conditional黄/Investigational蓝) + 依据列表 + 指南引用
 */
export function OncologyDetailCard({ detail }: Props) { ... }
```

### 5.3 路由注册

前端路由注册 1 个工作台页面：

```tsx
import MedicalWorkbenchPage from './MedicalWorkbenchPage'

{ path: '/medical', component: MedicalWorkbenchPage }
```

工作台内部通过 `?tab=` 查询参数切换功能页面，不注册独立子路由。

### 5.4 MedicalWorkbenchPage 组件

```tsx
import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'

const TABS = [
  { key: 'record', label: '智能病历', icon: FileText, component: SmartRecordPage },
  { key: 'translate', label: '医学翻译', icon: Languages, component: MedicalTranslatePage },
  { key: 'guideline', label: '搜指南', icon: BookOpen, component: SearchGuidelinePage },
  { key: 'drug', label: '搜药品', icon: Pill, component: SearchDrugPage },
  { key: 'literature', label: '搜文献', icon: Search, component: SearchLiteraturePage },
]

export default function MedicalWorkbenchPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const activeTab = searchParams.get('tab') || 'record'
  const ActiveComponent = TABS.find(t => t.key === activeTab)?.component ?? SmartRecordPage

  return (
    <div className="flex-1 overflow-hidden flex min-w-[950px]">
      {/* 左侧内嵌菜单 */}
      <nav className="w-[200px] shrink-0 border-r border-[var(--c-border)] px-3 py-4 flex flex-col gap-1">
        {TABS.map(tab => (
          <button
            key={tab.key}
            className={`px-3 py-2.5 rounded-lg text-[var(--fs-body)] cursor-pointer text-left
              ${activeTab === tab.key
                ? 'bg-[var(--c-surface-2)] text-[var(--c-accent)]'
                : 'text-[var(--c-text-secondary)] hover:bg-[var(--c-surface-2)]/50'}`}
            onClick={() => setSearchParams({ tab: tab.key })}
          >
            {tab.label}
          </button>
        ))}
      </nav>
      {/* 右侧功能页面 */}
      <div className="flex-1 overflow-y-auto">
        <ActiveComponent />
      </div>
    </div>
  )
}
```

侧边栏菜单通过 `GET /api/domains/manifests` 动态渲染，无需手动配置。

---

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

### 6.2 质子适应症评估安全验证

| 验证项 | 触发时机 | 验证内容 |
|---|---|---|
| 适应症等级校验 | 质子治疗记录写入时 | 评估结果必须来自 `proton_eligibility` 工具，不能人工修改等级 |
| RBE 不确定性提示 | 展示适应症评估结果时 | 提示 RBE=1.1 为常规值，临床实际可能 1.0-1.3，需结合临床判断 |
| 评估数据来源审计 | 每次适应症评估 | 记录评估依据的指南版本和引用，确保可追溯 |

### 6.3 HIPAA 治理 Reducer

```python
# governance/hipaa.py
class HipaaReducer:
    """HIPAA 合规治理 — 限制医疗域工具的使用范围"""

    name = "hipaa_compliance"

    def reduce(self, tool_name: str, args: dict, context) -> dict | None:
        # 1. 限制 PHI 数据导出（禁止将包含 PHI 的结果写入非医疗域文件）
        # 2. 限制外部 API 调用（医疗数据不得发送至未授权端点）
        # 3. 审计记录（所有 PHI 访问记入审计日志）
        ...
```

---

## 七、与系统现有能力的映射

### 7.1 工具能力映射

| 医疗域工具 | 系统现有能力 | 扩展内容 |
|---|---|---|
| `smart_medical_record` | 系统 `RunRequest.files` + OCR | 新增：肿瘤专科结构化模板 |
| `medical_translate` | 系统翻译能力 | 新增：肿瘤学/放射医学术语库 |
| `search_guideline` | 系统 `WebSearch`/`WebFetch` | 新增：NCCN/CSCO/质子适应症结构化检索 |
| `search_drug` | 系统 `WebSearch`/`WebFetch` | 新增：化疗方案模板 + 靶向匹配 + CYP3A4 |
| `search_literature` | 系统 `WebSearch`/`WebFetch` | 新增：GRADE分级 + 年会追踪 + 临床试验 |
| `chemo_protocol` | 无 | **新建**：化疗方案模板库 |
| `targeted_match` | 无 | **新建**：靶向药-突变匹配库 |
| `proton_eligibility` | 无 | **新建**：质子治疗适应症评估（仅AI可执行的判断） |

### 7.2 数据表映射

| 表名 | 对应系统现有表 | 说明 |
|---|---|---|
| `pc_medical_patients` | 无（新建） | 患者信息，PHI脱敏 |
| `pc_medical_records` | 无（新建） | 病历记录，含 `oncology_detail` JSONB |
| `pc_oncology_staging` | 无（新建） | 肿瘤分期+质子适应症评估，独立表支持分期查询 |
| `pc_oncology_treatments` | 无（新建） | 肿瘤治疗记录，支持化疗/靶向/免疫/放疗 |
| `pc_medical_searches` | 无（新建） | 搜索记录，通用表 |
| `pc_medical_translations` | 无（新建） | 翻译记录 |
| `pc_medical_record_files` | 无（新建） | 文件关联 |

---

## 八、实施优先级

| 阶段 | 功能 | 依赖 | 理由 |
|---|---|---|---|
| P0 | 智能病历（肿瘤专科+质子适应症评估） | 无 | 核心功能，其他功能均依赖病历数据 |
| P0 | 搜药品（含化疗方案+靶向匹配） | 无 | 肿瘤治疗核心工具 |
| P1 | 搜指南（NCCN/CSCO） | 无 | 临床决策支持 |
| P1 | 搜文献（GRADE/年会） | 无 | 证据检索 |
| P1 | 医学翻译（肿瘤学/放射医学） | 无 | 辅助功能 |
