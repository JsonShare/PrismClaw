# 临床研究报告（CSR）模板

## 基于ICH E3指南 · 中文版（NMPA提交）

---

> **声明**：本模板遵循ICH E3《临床研究报告的结构与内容》指南（CPMP/ICH/288/95），
> 符合国家药品监督管理局（NMPA）临床试验报告格式要求。

---

## 封面信息

| 字段 | 内容 |
|------|------|
| **报告标题** | {{REPORT_TITLE}} |
| **研究方案编号** | {{PROTOCOL_ID}} |
| **方案版本号** | {{PROTOCOL_VERSION}} |
| **研究药物名称** | {{INVESTIGATIONAL_PRODUCT}} |
| **适应症** | {{INDICATION}} |
| **研究阶段** | {{STUDY_PHASE}} |
| **报告类型** | {{REPORT_TYPE}}（最终报告/中期报告/安全性报告） |
| **申办方** | {{SPONSOR}} |
| **主要研究者** | {{PRINCIPAL_INVESTIGATOR}} |
| **研究机构** | {{STUDY_CENTER}} |
| **报告日期** | {{REPORT_DATE}} |
| **声明类型** | {{DECLARATION_TYPE}}（CONSORT/STROBE/PRISMA） |

---

## 摘要（第1页及之后）

### 摘要表格

| 项目 | 内容 |
|------|------|
| **研究标识** | {{PROTOCOL_ID}}，版本 {{PROTOCOL_VERSION}}，日期 {{PROTOCOL_DATE}} |
| **研究题目** | {{REPORT_TITLE}} |
| **研究药物** | {{INVESTIGATIONAL_PRODUCT}}（{{DOSAGE_FORM}}，{{DOSAGE}}） |
| **对照药物** | {{COMPARATOR}} |
| **研究阶段** | {{STUDY_PHASE}} |
| **适应症** | {{INDICATION}} |
| **研究设计** | {{STUDY_DESIGN}} |
| **研究目的** | {{STUDY_OBJECTIVES}} |
| **样本量** | 计划 {{PLANNED_N}} 例，实际入组 {{ENROLLED_N}} 例，完成 {{COMPLETED_N}} 例 |
| **关键入排标准** | {{KEY_INCLUSION}}；{{KEY_EXCLUSION}} |
| **给药方案** | {{TREATMENT_REGIMEN}} |
| **主要疗效终点** | {{PRIMARY_ENDPOINT}} |
| **次要疗效终点** | {{SECONDARY_ENDPOINTS}} |
| **统计方法** | {{STATISTICAL_METHODS_SUMMARY}} |
| **主要疗效结果** | {{PRIMARY_EFFICACY_RESULTS}} |
| **安全性结果** | {{SAFETY_RESULTS_SUMMARY}} |
| **结论** | {{CONCLUSION_SUMMARY}} |
| **日期** | {{REPORT_DATE}} |

---

## 目录

{{TABLE_OF_CONTENTS}}

---

## 1. 研究报告基本信息

### 1.1 方案及 amendments

- 方案编号：{{PROTOCOL_ID}}
- 方案标题：{{PROTOCOL_TITLE}}
- 方案版本：{{PROTOCOL_VERSION}}（日期：{{PROTOCOL_DATE}}）
- 方案 amendments 列表：{{AMENDMENTS_LIST}}

### 1.2 伦理委员会信息

| 伦理委员会名称 | 批准日期 | 批准编号 |
|--------------|---------|---------|
| {{IRB_1_NAME}} | {{IRB_1_DATE}} | {{IRB_1_NUMBER}} |
| {{IRB_ADDITIONAL_ROWS}} |

### 1.3 试验注册信息

- 临床试验注册号：{{CLINICAL_TRIAL_ID}}
- 注册平台：{{REGISTRY_PLATFORM}}（如中国临床试验注册中心 ChiCTR）
- 注册日期：{{REGISTRATION_DATE}}

### 1.4 知情同意

- 知情同意书版本：{{ICF_VERSION}}（日期：{{ICF_DATE}}）
- 是否经伦理委员会批准：{{ICF_APPROVED}}

---

## 2. 声明

{{DECLARATION}}

> 根据 {{DECLARATION_TYPE}} 声明指南要求，本研究报告遵循以下规范进行撰写和报告。

---

## 3. 研究者及研究机构

### 3.1 主要研究者

| 姓名 | 职称 | 机构 | 联系方式 |
|------|------|------|---------|
| {{PI_NAME}} | {{PI_TITLE}} | {{PI_INSTITUTION}} | {{PI_CONTACT}} |

### 3.2 各中心研究者列表

| 中心编号 | 中心名称 | 主要研究者 | 入组例数 |
|---------|---------|-----------|---------|
| {{CENTER_LIST}} |

---

## 4. 引言

### 4.1 研究背景

{{INTRODUCTION_BACKGROUND}}

### 4.2 研究药物

{{DRUG_DESCRIPTION}}

### 4.3 适应症及现有治疗

{{INDICATION_AND_EXISTING_TREATMENT}}

### 4.4 研究理论基础

{{SCIENTIFIC_RATIONALE}}

### 4.5 研究目的

**主要目的**：{{PRIMARY_OBJECTIVE}}

**次要目的**：{{SECONDARY_OBJECTIVES}}

**探索性目的**：{{EXPLORATORY_OBJECTIVES}}

---

## 5. 研究目的

### 5.1 主要目的

{{PRIMARY_OBJECTIVE_DETAIL}}

### 5.2 次要目的

{{SECONDARY_OBJECTIVES_DETAIL}}

### 5.3 探索性目的

{{EXPLORATORY_OBJECTIVES_DETAIL}}

### 5.4 研究假设

{{STUDY_HYPOTHESIS}}

---

## 6. 研究设计与方法

### 6.1 总体研究设计

- **研究类型**：{{STUDY_TYPE}}（如随机对照、前瞻性、多中心）
- **研究设计**：{{STUDY_DESIGN_DETAIL}}
- **设盲方式**：{{BLINDING_METHOD}}
- **研究分期**：{{STUDY_PHASE}}
- **研究分组**：{{TREATMENT_ARMS}}

### 6.2 研究人群

#### 入选标准
{{INCLUSION_CRITERIA}}

#### 排除标准
{{EXCLUSION_CRITERIA}}

#### 退出标准
{{WITHDRAWAL_CRITERIA}}

### 6.3 给药方案

| 组别 | 药物 | 剂量 | 给药途径 | 给药频率 | 疗程 |
|------|------|------|---------|---------|------|
| {{TREATMENT_REGIMEN_TABLE}} |

### 6.4 疗效评价指标

#### 主要疗效终点
{{PRIMARY_ENDPOINT_DEFINITION}}

#### 次要疗效终点
{{SECONDARY_ENDPOINTS_DEFINITION}}

#### 探索性终点
{{EXPLORATORY_ENDPOINTS_DEFINITION}}

### 6.5 安全性评价指标

{{SAFETY_ENDPOINTS_DEFINITION}}

### 6.6 有效性评价时间窗

| 评价时间点 | 评价内容 |
|-----------|---------|
| {{EVALUATION_SCHEDULE}} |

### 6.7 统计分析方法

#### 样本量估算
{{SAMPLE_SIZE_CALCULATION}}

> 数据来源：{{POWER_ANALYSIS_REFERENCE}}

#### 分析人群定义
- **全分析集（FAS）**：{{FAS_DEFINITION}}
- **符合方案集（PPS）**：{{PPS_DEFINITION}}
- **安全性分析集（SS）**：{{SS_DEFINITION}}

#### 主要疗效分析方法
{{PRIMARY_ANALYSIS_METHOD}}

#### 次要疗效分析方法
{{SECONDARY_ANALYSIS_METHOD}}

#### 安全性分析方法
{{SAFETY_ANALYSIS_METHOD}}

#### 缺失数据处理
{{MISSING_DATA_HANDLING}}

#### 亚组分析
{{SUBGROUP_ANALYSIS}}

#### 多重比较校正
{{MULTIPLE_COMPARISON_CORRECTION}}

---

## 7. 研究受试者

### 7.1 受试者处置

#### 筛选与入组

| | 试验组 (N={{N_EXP}}) | 对照组 (N={{N_CTRL}}) | 合计 (N={{N_TOTAL}}) |
|---|---|---|---|
| 筛选 | {{SCREENED_EXP}} | {{SCREENED_CTRL}} | {{SCREENED_TOTAL}} |
| 随机化 | {{RANDOMIZED_EXP}} | {{RANDOMIZED_CTRL}} | {{RANDOMIZED_TOTAL}} |
| 完成 | {{COMPLETED_EXP}} | {{COMPLETED_CTRL}} | {{COMPLETED_TOTAL}} |
| 提前退出 | {{DISCONTINUED_EXP}} | {{DISCONTINUED_CTRL}} | {{DISCONTINUED_TOTAL}} |

> 数据来源：{{DATA_PREPARATION_REFERENCE}}

#### 受试者流程图

{{CONSORT_FLOW_DIAGRAM}}

> 建议附CONSORT流程图，符合CONSORT声明要求。

#### 提前退出原因

| 退出原因 | 试验组 | 对照组 | 合计 |
|---------|--------|--------|------|
| {{DISCONTINUATION_REASONS_TABLE}} |

### 7.2 方案违背

| 违背类型 | 试验组 | 对照组 | 合计 |
|---------|--------|--------|------|
| {{PROTOCOL_DEVIATIONS_TABLE}} |

### 7.3 重要方案违背列表

{{MAJOR_DEVIATIONS_DETAIL}}

---

## 8. 有效性分析

### 8.1 分析人群

- 主要分析人群：{{PRIMARY_ANALYSIS_POP}}
- 敏感性分析人群：{{SENSITIVITY_ANALYSIS_POP}}

### 8.2 受试者人口学特征与基线特征（Table 1）

> 数据来源：{{DIFFERENTIAL_ANALYSIS_REFERENCE}}

{{DEMOGRAPHICS_TABLE}}

{{DEMOGRAPHICS_COMPARISON_NOTE}}

### 8.3 既往病史与合并用药

{{MEDICAL_HISTORY_AND_CONMEDS}}

### 8.4 基线疾病特征

{{BASELINE_DISEASE_CHARACTERISTICS}}

### 8.5 主要疗效终点分析

#### 全分析集（FAS）

| 统计量 | 试验组 | 对照组 | 组间差异 (95% CI) | P值 |
|--------|--------|--------|-------------------|-----|
| {{PRIMARY_ENDPOINT_RESULTS}} |

> 效应量：{{PRIMARY_EFFECT_SIZE}}

#### 符合方案集（PPS）（敏感性分析）

{{PRIMARY_PPS_RESULTS}}

### 8.6 次要疗效终点分析

{{SECONDARY_ENDPOINTS_RESULTS}}

> 数据来源：{{DIFFERENTIAL_RESULTS_REFERENCE}}

### 8.7 亚组分析

{{SUBGROUP_ANALYSIS_RESULTS}}

### 8.8 多因素分析

> 数据来源：{{REGRESSION_ANALYSIS_REFERENCE}}

{{REGRESSION_RESULTS}}

### 8.9 生存分析（如适用）

> 数据来源：{{SURVIVAL_ANALYSIS_REFERENCE}}

{{SURVIVAL_RESULTS}}

### 8.10 有效性结论

{{EFFICACY_CONCLUSION}}

> 以下为Plain Language解释：
> {{PLAIN_LANGUAGE_EFFICACY}}

---

## 9. 安全性分析

> ⚠️ **本章为CSR必需章节，不可省略。**

### 9.1 安全性分析人群

- 安全性分析集例数：{{N_SAFETY}}

### 9.2 暴露程度

| | 试验组 | 对照组 |
|---|---|---|
| 暴露例数 | {{EXPOSED_EXP}} | {{EXPOSED_CTRL}} |
| 暴露时间（中位数，月） | {{EXPOSURE_DURATION_EXP}} | {{EXPOSURE_DURATION_CTRL}} |
| 剂量强度（%） | {{DOSE_INTENSITY_EXP}} | {{DOSE_INTENSITY_CTRL}} |

### 9.3 不良事件（AE）总体分析

#### 任意不良事件

| | 试验组 (N={{N_EXP}}) | 对照组 (N={{N_CTRL}}) |
|---|---|---|
| 任意AE | {{ANY_AE_EXP}} (%) | {{ANY_AE_CTRL}} (%) |
| 相关AE | {{RELATED_AE_EXP}} (%) | {{RELATED_AE_CTRL}} (%) |
| SAE | {{SAE_EXP}} (%) | {{SAE_CTRL}} (%) |
| 导致停药AE | {{AE_LEAD_DISCONT_EXP}} (%) | {{AE_LEAD_DISCONT_CTRL}} (%) |
| 死亡 | {{DEATH_EXP}} | {{DEATH_CTRL}} |

#### 不良事件列表

| 系统器官分类 (SOC) | 首选术语 (PT) | 试验组 n/N (%) | 对照组 n/N (%) |
|---|---|---|---|
| {{AE_LIST_TABLE}} |

### 9.4 严重不良事件（SAE）

{{SAE_DETAIL_TABLE}}

### 9.5 导致退出研究的不良事件

{{AE_DISCONTINUATION_TABLE}}

### 9.6 重要不良事件

{{IMPORTANT_AE_TABLE}}

### 9.7 实验室检查

{{LABORATORY_RESULTS}}

### 9.8 生命体征

{{VITAL_SIGNS_RESULTS}}

### 9.9 体检

{{PHYSICAL_EXAMINATION_RESULTS}}

### 9.10 心电图

{{ECG_RESULTS}}

### 9.11 安全性结论

{{SAFETY_CONCLUSION}}

> 以下为Plain Language解释：
> {{PLAIN_LANGUAGE_SAFETY}}

---

## 10. 个体受试者数据列表

{{INDIVIDUAL_SUBJECT_DATA}}

> 建议以附录形式提供脱敏个体数据。

---

## 11. 疗效分析详情

### 11.1 主要疗效终点分析详情

{{PRIMARY_EFFICACY_DETAIL}}

### 11.2 次要疗效终点分析详情

{{SECONDARY_EFFICACY_DETAIL}}

### 11.3 其他疗效分析

{{ADDITIONAL_EFFICACY_ANALYSES}}

---

## 12. 安全性分析详情

### 12.1 不良事件详情

{{AE_DETAIL}}

### 12.2 实验室检查详情

{{LAB_DETAIL}}

### 12.3 其他安全性数据

{{ADDITIONAL_SAFETY_DATA}}

---

## 13. 讨论与结论

### 13.1 疗效讨论

{{EFFICACY_DISCUSSION}}

### 13.2 安全性讨论

{{SAFETY_DISCUSSION}}

### 13.3 研究局限性

{{STUDY_LIMITATIONS}}

### 13.4 结论

{{OVERALL_CONCLUSION}}

> 以下为Plain Language解释：
> {{PLAIN_LANGUAGE_CONCLUSION}}

---

## 14. 表格与图表列表

### 14.1 列表清单

| 编号 | 标题 | 页码 |
|------|------|------|
| {{LIST_OF_TABLES}} |

### 14.2 图表清单

| 编号 | 标题 | 页码 |
|------|------|------|
| {{LIST_OF_FIGURES}} |

> 图表来源：{{CHART_GENERATOR_REFERENCE}}

---

## 15. 参考文献

{{REFERENCES}}

> 引用格式：GB/T 7714（中文NMPA提交）或 Vancouver（英文FDA提交）

---

## 16. 附录

### 附录A：研究者信息
{{APPENDIX_A}}

### 附录B：统计分析计划
{{APPENDIX_SAP}}

### 附录C：统计分析程序代码
{{APPENDIX_CODE}}

### 附录D：病例报告表（CRF）
{{APPENDIX_CRF}}

### 附录E：知情同意书
{{APPENDIX_ICF}}

### 附录F：原始数据
{{APPENDIX_RAW_DATA}}

### 附录G：受试者个体数据
{{APPENDIX_INDIVIDUAL_DATA}}

### 附录H：证书与声明
{{APPENDIX_CERTIFICATES}}

---

## 17. 补充信息

### 17.1 药物非临床研究摘要
{{PRECLINICAL_SUMMARY}}

### 17.2 药品说明书
{{PRODUCT_LABELING}}

### 17.3 临床药理学数据
{{CLINICAL_PHARMACOLOGY}}

### 17.4 受试者日记卡/问卷
{{PATIENT_DIARIES}}

---

## 18. 数据质量与质量控制

### 18.1 数据管理
{{DATA_MANAGEMENT}}

### 18.2 数据质量
{{DATA_QUALITY}}

> 数据质量报告来源：{{DATA_PREPARATION_QUALITY_REPORT}}

### 18.3 监查与稽查
{{MONITORING_AND_AUDITING}}

---

## 19. 最终报告签署页

| 角色 | 姓名 | 签名 | 日期 |
|------|------|------|------|
| 主要研究者 | {{PI_NAME}} | | |
| 统计分析负责人 | {{STATISTICIAN_NAME}} | | |
| 申办方代表 | {{SPONSOR_REP_NAME}} | | |

---

> **术语表**：{{GLOSSARY}}

---

*本报告模板遵循ICH E3指南（CPMP/ICH/288/95）和NMPA临床试验报告格式要求生成。*
