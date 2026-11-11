---
name: regulatory-policy-workflow
name_zh: 监管政策对齐
description: >
  监管政策对齐工作流。内部政策与新规对齐修订、公开征求意见提交 comment、识别政策与外部规则差异。触发条件：'政策对齐'、'修订制度'、'征求意见'、'policy diff'、'comment letter'。
category: 监管合规
invocation_policy: auto
priority: 10
---

<!--
v4 Phase 4 工作流技能：regulatory-policy-workflow
编排的原子技能（Phase 3 迁移为 commands / 或保留技能）：
  - policy-diff    : regulatory-legal 下 command（新旧政策/规则差异 Diff…）
  - policy-redraft    : regulatory-legal 下 command（内部政策重写/修订（Redraft）…）
  - comments    : regulatory-legal 下 command（监管征求意见 Comment 撰写…）
  - gaps    : regulatory-legal 下 command（差异条目批量识别…）
L4 通用规范（Phase 2 内嵌）：citation + formatter + legal-safety
-->

# regulatory-policy-workflow

## 场景分流

按用户意图匹配以下场景，严格按声明顺序调用对应原子技能：

**新旧政策/规则差异 Diff**
新规 vs 旧规或行业实践的差异分析。
→ /regulatory-legal:policy-diff

**内部政策重写/修订（Redraft）**
根据新规把内部制度/policy 整体重写。
→ /regulatory-legal:policy-redraft

**监管征求意见 Comment 撰写**
起草提交给监管机构的 comment letter/意见反馈。
→ /regulatory-legal:comments

**差异条目批量识别**
把规则条目转成差距清单。
→ /regulatory-legal:gaps

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
