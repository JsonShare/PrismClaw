---
name: prescription-safety-workflow
name_zh: 处方安全工作流
description: >
  处方安全工作流入口。覆盖处方审查、药物相互作用检查、剂量调整、特殊人群评估及疗程合理性判断。触发条件：'处方'、'药物审查'、'用药安全'、'drug interaction'、'prescription'。
category: 临床诊疗
invocation_policy: user_only
---

# prescription-safety-workflow

## 场景分流

按用户意图匹配以下场景：

**处方全面审查**
对处方进行系统性安全审查。
→ /medical-clinical:prescription-review

**过敏安全检查**
检查目标药物与患者过敏史的交叉反应，覆盖 DIRECT/CLASS/CROSS-REACTIVE 三级冲突检测。
→ /medical-clinical:allergy-check

**FDA 药品安全查询**
查询 FDA 药品标签安全信息，包括黑框警告、禁忌症、药物相互作用和 FAERS 不良事件统计。
→ /medical-clinical:fda-drug-safety

**药品召回检查**
查询 FDA 药品召回数据库，确保用药批次安全。
→ /medical-clinical:drug-recall-check

**药物相互作用检查**
检查特定药物组合的相互作用风险。
→ /medical-clinical:prescription-review

**剂量调整建议**
根据肝肾功能、年龄、体重等因素提供剂量调整建议。
→ /medical-clinical:prescription-review

**特殊人群处方评估**
针对儿童、老年、孕妇、免疫抑制患者等特殊人群评估处方安全性。
→ /medical-clinical:prescription-review

若场景模糊，先向用户澄清。

---

## 审查框架

### 必查项目
1. **适应症** — 药品是否对症
2. **剂量** — 是否在推荐范围内
3. **相互作用** — 与当前用药的相互作用
4. **禁忌症** — 是否存在禁忌症
5. **特殊人群** — 特殊人群调整
6. **疗程** — 疗程是否合理

### 严重程度分级
- 🔴 禁忌/严重 — 必须避免
- 🟠 中等 — 建议调整
- 🟡 轻微 — 监测即可
- 🟢 通过 — 无明显问题

### 输出格式
1. **处方概要**
2. **审查结果**（分级）
3. **安全建议**
4. **信息来源**

---

## 医疗安全声明

本回复由 AI 辅助生成，不构成医疗建议。处方决策须由执业医师基于患者具体情况作出。所有审查结果须经执业医师或药剂师审核确认。