# PrismClaw for Medical 医疗健康插件

面向医疗行业最常见工作流的参考 agent、skill 和数据连接器。涵盖临床诊疗、医疗合规、医学研究和医疗运营四大领域。

> **初次使用？** 请先阅读 [QUICKSTART.md](QUICKSTART.md)——60 秒完成安装。

## 插件列表

| 插件 | 功能说明 | 第一个命令 |
|---|---|---|
| **medical-clinical** | 症状查询、过敏检查、FDA 药物安全、药物召回、实验室结果分析、处方安全审查、生命体征记录 | `/clinical-cold-start-interview` |
| **medical-compliance** | HIPAA 合规审查、数据泄露响应、审计追踪、隐私影响评估 | `/compliance-cold-start-interview` |
| **medical-research** | 文献综述、研究设计、统计分析、临床试验工作流 | `/research-cold-start-interview` |
| **medical-operations** | 护理差距分析、保险理赔、病历管理、预约可用性、库存检查 | `/operations-cold-start-interview` |

## 托管 Agent Cookbook

| Cookbook | 功能说明 |
|---|---|
| **chart-watcher** | 自动监控电子病历中的异常生命体征和关键指标变化 |
| **compliance-monitor** | 定期审查数据处理活动的合规状态，识别潜在合规风险 |

## 工具矩阵

### medical-clinical（临床诊疗）

| 命令 | 安全钩子 | 分类 |
|---|---|---|
| `/medical-clinical:symptom-lookup` | Pre: allergy_safety_verifier → Post: confidence_scorer + claim_verifier | 临床评估 |
| `/medical-clinical:allergy-check` | Pre: allergy_safety_verifier → Post: confidence_scorer + claim_verifier | 临床评估 |
| `/medical-clinical:fda-drug-safety` | Pre: allergy_safety_verifier → Post: confidence_scorer + claim_verifier | 药物安全 |
| `/medical-clinical:drug-recall-check` | Pre: allergy_safety_verifier → Post: confidence_scorer + claim_verifier | 药物安全 |
| `/medical-clinical:lab-results-analysis` | Pre: allergy_safety_verifier → Post: confidence_scorer + claim_verifier | 临床评估 |
| `/medical-clinical:prescription-review` | Pre: drug_safety_verifier → Post: confidence_scorer + claim_verifier | 处方安全 |
| `/medical-clinical:vital-signs` | Pre: drug_safety_verifier → Post: confidence_scorer + claim_verifier | 生命体征 |
| `/medical-clinical:patient-summary` | — | 临床评估 |

### medical-operations（医疗运营）

| 命令 | 安全钩子 | 分类 |
|---|---|---|
| `/medical-operations:care-gap-analysis` | Pre: confidence_scorer | 护理管理 |
| `/medical-operations:update-care-gap` | Pre: confidence_scorer | 护理管理 |
| `/medical-operations:insurance-claim` | Pre: claim_verifier | 保险理赔 |
| `/medical-operations:record-management` | Pre: claim_verifier | 病历管理 |
| `/medical-operations:appointment-availability` | — | 运营管理 |
| `/medical-operations:inventory-check` | — | 运营管理 |
| `/medical-operations:provider-search` | — | 运营管理 |

### medical-compliance（医疗合规）

| 命令 | 安全钩子 | 分类 |
|---|---|---|
| `/medical-compliance:hipaa-review` | Pre: confidence_scorer (fail_on_low_confidence) → Post: claim_verifier | 合规审查 |
| `/medical-compliance:data-breach-response` | Pre: confidence_scorer (fail_on_low_confidence) → Post: claim_verifier | 数据安全 |
| `/medical-compliance:audit-trail` | Pre: confidence_scorer (fail_on_low_confidence) → Post: claim_verifier | 审计追踪 |

### medical-research（医学研究）

| 命令 | 安全钩子 | 分类 |
|---|---|---|
| `/medical-research:literature-review` | Pre: confidence_scorer | 研究方法 |
| `/medical-research:study-design` | Pre: confidence_scorer | 研究方法 |
| `/medical-research:statistics` | Pre: confidence_scorer | 研究方法 |

## 安全验证机制

本插件内置 4 个安全验证脚本，通过 Hooks 系统与工具调用深度集成，为医疗工作流提供实时安全审查。

### 四大安全脚本

| 脚本 | 功能说明 | 触发时机 |
|---|---|---|
| **allergy_safety_verifier.py** | 过敏安全验证器——将患者过敏史与用药进行交叉比对，识别 DIRECT（直接过敏）、CLASS（药物类别交叉过敏）、CROSS-REACTIVE（交叉过敏反应）三级风险 | PreToolUse：症状查询、过敏检查、药物安全查询、药物召回、实验室分析 |
| **drug_safety_verifier.py** | 药物安全验证器——内置 50+ 危险药物相互作用知识库，识别 CRITICAL 和 WARNING 两级药物相互作用风险 | PreToolUse：处方审查、生命体征记录 |
| **confidence_scorer.py** | 置信度评分器——基于工具使用数量、数据来源丰富度、模糊表述程度和工具错误率，量化输出的置信度（0.0–1.0） | PostToolUse（临床所有工具）、PreToolUse（合规、研究、运营工具） |
| **claim_verifier.py** | 声明验证器——检查 AI 输出中的声明是否基于工具结果（grounding rate），衡量声明的可追溯性 | PostToolUse（临床所有工具、合规工具、运营工具） |

### Hooks 集成架构

```
工具调用 → PreToolUse 安全脚本 → 工具执行 → PostToolUse 安全脚本 → 最终输出
```

- **PreToolUse**：在工具执行前运行安全验证脚本。若 `fail_on_critical: true`，检测到严重风险时将中断工具调用；若 `fail_on_low_confidence: true`，低置信度输出将被阻断。
- **PostToolUse**：在工具执行后运行质量验证脚本，对输出进行置信度评分和声明验证。

## 安全声明

**所有输出均为供医疗专业人员审阅的草稿——不构成医疗诊断、治疗方案或医嘱。** 本插件的所有功能输出均须经持执业资质的医疗专业人员审核后方可用于临床实践。本插件不替代医生的临床判断。

## 许可证

Apache License 2.0