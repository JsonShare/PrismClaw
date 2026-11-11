---
name: employment-investigation-workflow
name_zh: 内部调查工作流
description: >
  内部调查工作流。覆盖立案、问询记录、证据补充、调查备忘录、总结报告五段。触发条件：'内部调查'、'骚扰举报'、'compliance investigation'、'违纪调查'、'举报人保护'。
category: 雇佣法务
invocation_policy: user_only
priority: 10
---

<!--
v4 Phase 4 工作流技能：employment-investigation-workflow
编排的原子技能（Phase 3 迁移为 commands / 或保留技能）：
  - investigation-open    : employment-legal 下 command（立案 / 启动调查…）
  - investigation-query    : employment-legal 下 command（事实问询 / 证据收集…）
  - investigation-add    : employment-legal 下 command（事实问询 / 证据收集…）
  - investigation-memo    : employment-legal 下 command（起草调查备忘录…）
  - investigation-summary    : employment-legal 下 command（出具最终调查报告与建议…）
L4 通用规范（Phase 2 内嵌）：citation + formatter + legal-safety
-->

# employment-investigation-workflow

## 场景分流

按用户意图匹配以下场景，严格按声明顺序调用对应原子技能：

**立案 / 启动调查**
接到举报或发现线索，open 新 investigation 档案。
→ /employment-legal:investigation-open

**事实问询 / 证据收集**
访谈、文件调取、检索查询、追加证据。
→ /employment-legal:investigation-query
→ /employment-legal:investigation-add

**起草调查备忘录**
阶段性 memo、事实认定、可信度评估。
→ /employment-legal:investigation-memo

**出具最终调查报告与建议**
总结结论、纪律处分建议、补救措施。
→ /employment-legal:investigation-summary

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
