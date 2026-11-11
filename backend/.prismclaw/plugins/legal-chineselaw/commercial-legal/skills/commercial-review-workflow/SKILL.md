---
name: commercial-review-workflow
name_zh: 合同审查工作流
description: >
  合同审查工作流入口。覆盖通用合同审查、NDA、SaaS/MSA、供应商协议、修订历史、对方条款回函、升级红灯条款七块。触发条件：'合同审查'、'审合同'、'review contract'、'NDA'、'MSA'、'SaaS 协议'、'供应商合同'。
category: 商事合同
invocation_policy: auto
priority: 10
---

<!--
v4 Phase 4 工作流技能：commercial-review-workflow
编排的原子技能（Phase 3 迁移为 commands / 或保留技能）：
  - review    : commercial-legal 下 command（通用合同审查…）
  - nda-review    : commercial-legal 下 保留技能（NDA 保密协议审查…）
  - saas-msa-review    : commercial-legal 下 保留技能（SaaS / MSA 主服务协议审查…）
  - vendor-agreement-review    : commercial-legal 下 保留技能（供应商/采购/Vendor 协议审查…）
  - amendment-history    : commercial-legal 下 command（合同修订历史对比…）
  - review-proposals    : commercial-legal 下 command（对方条款/反要约回函建议…）
  - escalation-flagger    : commercial-legal 下 command（红灯条款升级 / Escalation…）
L4 通用规范（Phase 2 内嵌）：citation + formatter + legal-safety
-->

# commercial-review-workflow

## 场景分流

按用户意图匹配以下场景，严格按声明顺序调用对应原子技能：

**通用合同审查**
没有匹配模板的任意商事合同审查。
→ /commercial-legal:review

**NDA 保密协议审查**
单向/双向 NDA、互密协议。
→ /commercial-legal:nda-review

**SaaS / MSA 主服务协议审查**
SaaS 订阅、长期主服务/框架协议。
→ /commercial-legal:saas-msa-review

**供应商/采购/Vendor 协议审查**
采购/供应商/vendor agreement 模板。
→ /commercial-legal:vendor-agreement-review

**合同修订历史对比**
收到对方 redline，做多轮版本差异追踪。
→ /commercial-legal:amendment-history

**对方条款/反要约回函建议**
回应对方 markup 及变更建议时的策略与回文。
→ /commercial-legal:review-proposals

**红灯条款升级 / Escalation**
触发禁止条款或重大风险，需升级到资深法务或外部律师。
→ /commercial-legal:escalation-flagger

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
