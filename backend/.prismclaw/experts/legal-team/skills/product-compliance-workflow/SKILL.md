---
name: product-compliance-workflow
name_zh: 产品合规工作流
description: >
  产品合规工作流。覆盖'这是问题吗'快速分诊、上线前 Launch Review、营销宣传合规三块。触发条件：'产品合规'、'launch review'、'功能风险'、'宣传合规'、'marketing claims'、'feature risk'。不适用于合同条款审查、隐私影响评估、AI 治理记录与诉讼事务，这些分别由合同、隐私、AI 治理专家与诉讼专家承接。
category: 产品合规
invocation_policy: user_only
priority: 10
---

# product-compliance-workflow

## 场景分流

按用户意图匹配以下场景，严格按声明顺序调用对应原子技能：

**功能/行为是否有法律问题快速分诊**
产品提一个做法/功能，快速定性风险。
→ `skills/product-law-playbook/references/commands/is-this-a-problem.md`

**产品正式上线前 Launch Review**
新功能/新产品进入市场前做完整八类合规审查（框架见 `skills/product-law-playbook/references/launch-review/eight-category-framework.md`）。
→ `skills/product-law-playbook/references/commands/launch-review.md`

**营销宣传文案/素材审查**
广告语、海报、短视频、直播脚本、用户生成内容合规。
→ `skills/product-law-playbook/references/commands/marketing-claims-review.md`

**功能级风险评估与分级**
按功能/模块维度做风险矩阵与缓解方案。
→ `skills/feature-risk-assessment/SKILL.md`

若场景模糊，先向用户澄清匹配哪一分支，不要盲跳步骤。

---

## 输出规范

### 来源标注（citation）
所有结论附来源条目编号。引用合同、法规、通信原文时，格式为：
`[序号] 文件名:行号 | 原文片段或条款编号`

示例：
- `[1] 供货合同.pdf:第4.2条 | 买方应于收到发票后30日内付款`
- `[2] 民法典.pdf:第577条 | 当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任`
- `[3] 2024-03-15对方邮件.eml:L12-15 | "我方将于本月底安排付款"`

引用判例时标注案号：`[4] (2023)京01民终12345号判决书:L45-50`

### 输出格式（formatter）
所有最终输出严格分四段，每段独立成章：

1. **结论** — 你判断的事项核心观点、建议采取的下一步动作（一句话 + 详细展开）
2. **依据** — 支撑结论的合同条款、法律规定、证据要点（逐条列出，附来源标注）
3. **风险** — 不按建议行动的后果、对方可能的抗辩、时效/程序风险（逐条列出，附触发条件和概率）
4. **来源** — 本回复所依据的全部材料清单，对应依据段落的 `[序号]` 引用

草稿阶段（未最终确认前）允许插入 `[PLACEHOLDER——待核实:____]` 占位符，但必须标明确切待核实点与负责角色。

---

## 免责声明

**法律安全声明（legal-safety）**

本回复由 AI 辅助生成，仅提供一般性参考框架，不构成法律意见，也不替代具有执业资质律师的专业判断。具体法律问题，请咨询持有效执业证的律师或获准从事法律业务的机构。
中国法律服务市场监管：未经司法行政机关许可，任何 AI 系统不得出具法律意见书或从事有偿诉讼代理。涉及起诉状、答辩状、法律意见书等需由律师签名的正式文书，须经委托律师独立审查、修改并以律师事务所名义出具后方可使用。
本回复中的法规条款、裁判观点或监管口径为参考时点的有效状态，后续修订或地方实施细则差异请以最新正式发布版本为准。
