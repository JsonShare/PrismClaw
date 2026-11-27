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
→ `skills/clinical-med-playbook/references/commands/prescription-review.md`

**过敏安全检查**
检查目标药物与患者过敏史的交叉反应，覆盖 DIRECT/CLASS/CROSS-REACTIVE 三级冲突检测。
→ `skills/clinical-med-playbook/references/commands/allergy-check.md`

**药品说明书与上市后警戒核对**
核对中国境内药品说明书中的禁忌、注意事项、相互作用与最高级别警示；并查国家药监局（NMPA）发布的召回与安全性公告，确认在用药批次是否受影响。本包未内置任何药品数据库连接器：无连接器时按用户提供的说明书与公告原文核对，核不到的项标 `[待核验]`，**不得用美国 FDA 数据代替中国说明书结论**。

**药物相互作用检查**
检查特定药物组合的相互作用风险。
→ `skills/clinical-med-playbook/references/commands/prescription-review.md`

**剂量调整建议**
根据肝肾功能、年龄、体重等因素提供剂量调整建议。
→ `skills/clinical-med-playbook/references/commands/prescription-review.md`

**特殊人群处方评估**
针对儿童、老年、孕妇、免疫抑制患者等特殊人群评估处方安全性。
→ `skills/clinical-med-playbook/references/commands/prescription-review.md`

若场景模糊，先向用户澄清。

---

## 强制校验（输出前必做）

本技能目录 `scripts/` 提供 4 个确定性校验脚本，凡涉及下列情形必须先运行对应脚本、以脚本结果为准，不得仅凭模型知识下结论：

- `allergy_safety_verifier.py` — 过敏冲突校验（过敏史与药物交叉反应）
- `drug_safety_verifier.py` — 药物相互作用校验
- `claim_verifier.py` — 来源标注核对（核对结论是否有工具依据）
- `confidence_scorer.py` — 置信度评分

**触发条件**：输出涉及过敏史与药物、药物组合相互作用，或需要声明"有工具依据"时，必须先运行对应脚本。仅凭记忆或推测断言药物安全属于禁止行为。

**命令用法**：
- 自检：`python3 scripts/allergy_safety_verifier.py --selftest`（其余脚本同理）。
- 实际校验：`python3 scripts/<脚本>.py --input <输入json>`，或通过 stdin 传入输入。具体参数以脚本头部 docstring 的用法说明为准（脚本由配套实现提供，勿臆造参数）。

**结果处理**：
- `blocking`：必须停止该结论，说明冲突点，并给出需人工/医师确认的替代建议。
- `warning`：保留结论但显式标注"须医师复核"；不得把脚本的 warning 当作已确认的安全结论。

**边界**：过敏校验为宽口径保守筛查，可能存在假阳性；最终禁忌判定由执业医师作出。

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

正文之后按需追加 `<!--references ... -->` 来源块（见系统提示 `<reference_sources>`），
正文不再单列「信息来源」段。

---

## 医疗安全声明

本回复由 AI 辅助生成，不构成医疗建议。处方决策须由执业医师基于患者具体情况作出。所有审查结果须经执业医师或药剂师审核确认。
