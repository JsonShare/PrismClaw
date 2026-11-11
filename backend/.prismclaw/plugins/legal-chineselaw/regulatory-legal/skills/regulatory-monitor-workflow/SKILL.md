---
name: regulatory-monitor-workflow
name_zh: 监管监控工作流
description: >
  监管监控工作流。监管资讯订阅源监控、入站来文分流、监管缺口识别。触发条件：'监管监控'、'新规'、'政策变化'、'regulatory'、'来文'、'监管来函'。
category: 监管合规
invocation_policy: user_only
---

<!--
v4 Phase 4 工作流技能：regulatory-monitor-workflow
编排的原子技能（Phase 3 迁移为 commands / 或保留技能）：
  - reg-feed-watcher    : regulatory-legal 下 command（监管资讯 Feed 持续监测…）
  - regulatory-incoming-letter    : regulatory-legal 下 command（入站监管来文 / 检查通知分流…）
  - regulatory-gaps    : regulatory-legal 下 command（监管要求 vs 现状差距识别…）
  - gap-surfacer    : regulatory-legal 下 保留技能（Gap 持续追踪与状态看板…）
L4 通用规范（Phase 2 内嵌）：citation + formatter + legal-safety
-->

# regulatory-monitor-workflow

## 场景分流

按用户意图匹配以下场景，严格按声明顺序调用对应原子技能：

**监管资讯 Feed 持续监测**
订阅源 watcher 抓取新规/征求意见稿并分类。
→ /regulatory-legal:reg-feed-watcher

**入站监管来文 / 检查通知分流**
收到监管函、问询函、现场检查通知时做分流与任务分配。
→ /regulatory-legal:regulatory-incoming-letter

**监管要求 vs 现状差距识别**
把新规条款映射到内部流程，找出 gap 与整改项。
→ /regulatory-legal:regulatory-gaps

**Gap 持续追踪与状态看板**
gap 跟踪关闭进度、责任人、到期提醒。
→ /regulatory-legal:gap-surfacer

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
