# 临床诊疗 Plugin

跨五个核心临床领域的工作流：症状查询、过敏检查、药物安全、处方安全审查和生命体征监测。所有工具均集成安全验证钩子，在执行前进行过敏/药物交叉比对，执行后进行置信度和声明验证。仅激活适用于你角色的模块。

**每个输出都是供医疗专业人员审阅的草稿——带引用、标记和把关——而不是医疗诊断或治疗方案。** Plugin 完成工作：阅读病历、应用循证指南、查找药物相互作用、起草摘要。医疗专业人员审阅、验证并决定。

## 目标用户

| 角色 | 活跃模块 |
|---|---|
| **执业医师** | 全部五个 |
| **住院医师** | 症状查询 + 过敏检查 + 处方安全 |
| **药剂师** | 处方安全 + 药物安全 |
| **护士** | 生命体征监测 + 症状查询 |
| **医学生/医疗助理** | 症状查询 + 过敏检查 |

## 首次运行

```
/clinical-cold-start-interview
```

## 命令

| 命令 | 作用 | 安全钩子 |
|---|---|---|
| `/clinical-cold-start-interview` | 模块化冷启动，配置执业档案 | — |
| `/symptom-lookup` | 患者症状查询与鉴别诊断辅助 | Pre: allergy_safety_verifier → Post: confidence_scorer + claim_verifier |
| `/allergy-check` | 患者过敏史检查与用药交叉比对 | Pre: allergy_safety_verifier → Post: confidence_scorer + claim_verifier |
| `/fda-drug-safety` | FDA 药物安全信息查询 | Pre: allergy_safety_verifier → Post: confidence_scorer + claim_verifier |
| `/drug-recall-check` | 药物召回信息查询 | Pre: allergy_safety_verifier → Post: confidence_scorer + claim_verifier |
| `/lab-results-analysis` | 实验室结果分析与解读 | Pre: allergy_safety_verifier → Post: confidence_scorer + claim_verifier |
| `/prescription-review` | 处方安全审查——药物相互作用、剂量、禁忌症 | Pre: drug_safety_verifier → Post: confidence_scorer + claim_verifier |
| `/vital-signs` | 生命体征记录与趋势分析 | Pre: drug_safety_verifier → Post: confidence_scorer + claim_verifier |
| `/patient-summary` | 患者病史摘要生成 | — |

## 专项技能

命令之外，本插件提供四个可自动触发的技能，覆盖「复盘—抽取—计划—找试验」的完整链路。

| 技能 | 用途 | 触发示例 |
|---|---|---|
| `clinical-case-review` | 病例结构化复盘：事实时间轴、资料一致性审计、问题地图、决策节点。带七类证据标签与急症/隐私双闸门，内置肿瘤、慢病、质子放疗三套场景适配卡 | 「复盘这个病例」「MDT 准备」「这个病例哪里有问题」 |
| `clinical-note-extract` | 按字段清单从病历抽取结构化数据，每个值带原文出处，缺失显式标理由，并按 presence/temporality/experiencer 三轴标注断言 | 「提取这些字段」「把病历转成表格」 |
| `treatment-plan-writer` | 生成含 SMART 目标、干预、监测、时间轴的治疗与健康管理计划，覆盖慢病、康复、围手术期、肿瘤支持治疗 | 「写个治疗计划」「随访计划怎么定」 |
| `clinical-trial-lookup` | 检索 ClinicalTrials.gov 在研试验，按疾病、干预、期别、地点筛选，标注中国中心 | 「有没有相关临床试验」「哪些中心在招」 |

抽取结果可用 `scripts/validate_extraction.py` 校验出处；临床试验检索用 `scripts/query_trials.py`，二者均为纯标准库脚本，无需额外依赖。

## 安全验证

本插件通过 Hooks 集成了 4 个安全验证脚本，为所有工具调用提供实时安全审查：

### PreToolUse（执行前验证）

- **allergy_safety_verifier.py**：在症状查询、过敏检查、药物安全查询、药物召回、实验室分析前运行，将患者过敏史与用药进行交叉比对，识别 DIRECT、CLASS、CROSS-REACTIVE 三级风险。检测到严重风险时将中断工具调用（`fail_on_critical: true`）。
- **drug_safety_verifier.py**：在处方审查、生命体征记录前运行，检查 50+ 危险药物相互作用知识库，识别 CRITICAL 和 WARNING 两级风险。检测到 CRITICAL 级相互作用时将中断工具调用（`fail_on_critical: true`）。

### PostToolUse（执行后验证）

- **confidence_scorer.py**：在所有临床工具执行后运行，基于工具使用数量、数据来源丰富度、模糊表述程度和工具错误率，量化输出的置信度（0.0–1.0）。
- **claim_verifier.py**：在所有临床工具执行后运行，检查 AI 输出中的声明是否基于工具结果（grounding rate），衡量声明的可追溯性。

### 验证流程

```
用户调用工具
    ↓
PreToolUse: allergy_safety_verifier / drug_safety_verifier
    ↓ (通过)
工具执行
    ↓
PostToolUse: confidence_scorer → claim_verifier
    ↓
最终输出（附带置信度评分和声明验证结果）
```

## Skills

| Skill | 模块 | 目的 |
|---|---|---|
| **clinical-assessment-workflow** | 临床评估 | 患者病史摘要、鉴别诊断辅助、检查结果解读 |
| **prescription-safety-workflow** | 处方安全 | 处方安全审查、药物相互作用检查、剂量调整 |

## Agents

| Agent | 模块 | 它监视什么 | 默认节奏 |
|---|---|---|---|
| **chart-watcher** | 生命体征 | 电子病历中的异常生命体征和关键指标变化 | 每小时 |

## 安全声明

本插件生成的所有内容均为草稿，不构成医疗诊断、治疗方案或医嘱。在临床实践中使用之前，必须由持相应资质的医疗专业人员审核确认。