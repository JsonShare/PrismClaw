---
name: internal-investigation
name_zh: 内部调查
description: >
  参考：管理内部调查的共享框架——从接收到最终备忘录——特权调查日志、带提取的文档处理、来源覆盖跟踪、日志问答、备忘录起草和受众摘要。由 /investigation-open、/investigation-add、/investigation-query、/investigation-memo 和 /investigation-summary 加载；不直接调用。
invocation_policy: user_only
category: 雇佣法务
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# 内部调查 Skill

## 事项上下文

**事项上下文。** 检查执业级 PRISMCLAW.md 中的 `## Matter workspaces`。如果 `Enabled` 为 `✗`（内部用户的默认值），跳过本段的其余部分——skills 使用执业级上下文，事项机制不可见。如果已启用且没有活跃事项，询问："这是哪个事项的? Run `/employment-legal:matter-workspace switch <slug>` 或说 `practice-level`。"加载活跃事项的 `matter.md` 以获取事项特定上下文和覆盖。将输出写入事项文件夹 `~/.prismclaw/plugins/legal-chineselaw/employment-legal/matters/<matter-slug>/`。除非 `Cross-matter context` 为 `on`，否则永远不要阅读另一个事项的文件。

---

## 输出标题

在前面加上 `~/.prismclaw/plugins/legal-chineselaw/employment-legal/PRISMCLAW.md` → `## Outputs` 中的工作产品标题（根据用户角色而不同——见 `## Who's using this`）。此 skill 创建的每个文件、日志、备忘录和摘要都以该标题开头。

> **分发纪律。** 此 skill 创建的每个文件——日志条目、备忘录草稿、受众摘要、文档注释——继承底层调查的特权性和保密性状态。在特权圈子之外分发（转发给调查团队外的非律师、未限定范围地抄送 HR、交给业务方）可能会放弃整个调查的特权。将这些文件存储在特权材料所在的地方，按照工作产品标题标记，并慎重做出每个分发决定。

## ⚠️ 特权通知——在继续之前阅读

**标记并不产生特权。** 上面的标题反映了预期的保护措施，包含它很重要——但它本身并不确立特权。任何给定输出是否实际上享有特权取决于调查是否由律师主导、文档创建的目的以及它们随后如何使用或披露。

**在打开事项之前确认：**此调查是否由律师主导？如果不是——如果 HR 在法律顾问角色下运行它，或者如果它不是在法律顾问指导下为了获得法律建议而发起的——特权分析会实质性地改变，此 skill 的默认标记可能会产生误导。在创建任何日志或文件之前向律师标记这个问题。

如果对特权适用性有任何疑问，律师应在创建调查文件之前解决它。标记不当的材料如果在发现阶段特权受到挑战，可能会产生问题。

---

## 目的

内部调查以两种方式失败：覆盖缺口（从未收集的来源）和综合缺口（已收集但从未关联的证据）。此 skill 处理这两种情况——它跟踪已收集和未收集的内容，处理文档转储以 surfaced 重要内容而不淹没律师，并维护可随时转为特权备忘录的结构化日志。

## 特权说明

此 skill 创建的所有文件都带有上面的特权标记。关于该标记的作用和限制，请参阅此 skill 顶部的完整说明。

## 加载上下文

阅读 `~/.prismclaw/plugins/legal-chineselaw/employment-legal/PRISMCLAW.md` → 升级表、任何记录的调查协议。

---

## 模式 1：打开新事项

由 `/employment-legal:investigation-open` 或"打开调查"或"开始对...的调查"触发。

### 步骤 1 — 接收

在单个块中询问以下内容：

> 要打开调查日志，我需要一些信息：
>
> **事项**
> - 用通俗语言说明的指控或关注点是什么？
> - 申诉人是谁（或什么触发了此——投诉、举报、审计、经理观察）？
> - 被申诉人或主体是谁？
> - 指控行为发生的大致时间框架是什么？
> - 这是否由律师主导？（如果是：工作产品保护适用。如果不是：在继续之前标记特权风险。）
>
> **调查类型**（帮助我建议正确的来源检查清单）
> - HR：骚扰 / 歧视 / 报复
> - 财务不当行为：费用欺诈 / 采购违规 / 挪用公款
> - 高管不当行为：利益冲突 / 未披露关系 / 治理失败
> - 告密者：对受保护活动的报复
> - 其他：简要描述
>
> **代理和雇主身份**（surfaced 改变访谈程序的并行法律框架）
> - 被申诉人、申诉人或任何预期证人是否由工会代表或受集体谈判协议覆盖？（如果是：标记 Weingarten 研究——调查访谈中的代表权可能适用并改变访谈协议。）
> - 公司是否为公共雇主（政府实体、公立大学、州或市级机构）或以州法律色彩行事？（如果是：标记 Garrity 研究——公共部门调查中的被迫陈述有特殊的使用豁免后果并改变访谈必须进行和记录的方式。）

如果任一标志触发，在进行访谈之前研究适用规则（NLRA / 州公共部门劳动法规用于 Weingarten；第五修正案和 Garrity 案件系列，以及任何州类比）。引用主要来源。验证货币性。在协议调整之前不要访谈。

### 步骤 2 — 创建事项目录和文件

创建以下文件：

`~/.prismclaw/plugins/legal-chineselaw/employment-legal/investigation-[matter-slug]/log.yaml`：

```yaml
# [WORK-PRODUCT HEADER — per plugin config ## Outputs — differs by role; see `## Who's using this`]
matter: "[matter name]"
matter_slug: "[slug]"
opened: "[ISO date]"
attorney_directed: [true/false]
allegation: "[plain-language summary]"
complainant: "[name/role or anonymous]"
respondent: "[name/role]"
conduct_timeframe: "[approximate dates]"
investigation_type: "[HR/financial/executive/whistleblower/other]"
status: open
last_updated: "[ISO date]"

issues:
  - "[Issue 1 — derived from allegation, e.g. 'alleged hostile work environment']"
  - "[Issue 2 if applicable]"

entries: []

evidentiary_gaps: []
```

`~/.prismclaw/plugins/legal-chineselaw/employment-legal/investigation-[matter-slug]/sources-checklist.yaml`:

根据调查类型生成。见下方来源检查清单模板。

`~/.prismclaw/plugins/legal-chineselaw/employment-legal/investigation-[matter-slug]/documents-reviewed.yaml`:

```yaml
# [WORK-PRODUCT HEADER — per plugin config ## Outputs — differs by role; see `## Who's using this`]
matter: "[matter name]"
total_reviewed: 0
total_surfaced: 0
last_updated: "[ISO date]"
documents: []
```

### 步骤 3 — 来源检查清单

根据调查类型生成适当的检查清单。向律师展示并询问："这适合你的事项吗？让我知道是否有任何项目不适用（我会将它们标记为 N/A）或者是否有针对此情况的额外来源。"

**HR 调查来源（骚扰/歧视/报复）：**
```yaml
sources:
  - id: 1
    source: "Complainant interview"
    status: open
    notes: ""
  - id: 2
    source: "Respondent interview"
    status: open
    notes: ""
  - id: 3
    source: "Witness interviews — identify from complainant and respondent accounts"
    status: open
    notes: ""
  - id: 4
    source: "Email/messaging review — parties, relevant date range"
    status: open
    notes: ""
  - id: 5
    source: "HR records — respondent's performance history, prior complaints,
             prior discipline"
    status: open
    notes: ""
  - id: 6
    source: "Prior complaints — any prior complaints against respondent in
             HR system"
    status: open
    notes: ""
  - id: 7
    source: "Comparator data — how were similar situations handled"
    status: open
    notes: ""
  - id: 8
    source: "Relevant policies — harassment, code of conduct, reporting
             procedures (version in effect at time of alleged conduct)"
    status: open
    notes: ""
  - id: 9
    source: "Org chart and reporting relationships at time of alleged conduct"
    status: open
    notes: ""
  - id: 10
    source: "Calendar records — any meetings or events mentioned in accounts"
    status: open
    notes: ""
  - id: 11
    source: "Upjohn warning documentation — confirm interviews were preceded
             by Upjohn warnings and documented"
    status: open
    notes: ""
```

**财务不当行为来源：**
```yaml
sources:
  - id: 1
    source: "Expense reports — subject, relevant period"
    status: open
    notes: ""
  - id: 2
    source: "Approval records — who approved the expenses or transactions"
    status: open
    notes: ""
  - id: 3
    source: "Vendor/contractor records — contracts, invoices, payment records"
    status: open
    notes: ""
  - id: 4
    source: "Financial system records — AP, GL entries for relevant accounts"
    status: open
    notes: ""
  - id: 5
    source: "Email/messaging review — subject, approvers, counterparties"
    status: open
    notes: ""
  - id: 6
    source: "Subject interview"
    status: open
    notes: ""
  - id: 7
    source: "Approver interviews"
    status: open
    notes: ""
  - id: 8
    source: "Counterparty/vendor interviews (if accessible)"
    status: open
    notes: ""
  - id: 9
    source: "Audit logs — system access logs for relevant accounts/systems"
    status: open
    notes: ""
  - id: 10
    source: "Prior audits or reviews covering the relevant period"
    status: open
    notes: ""
  - id: 11
    source: "Upjohn warning documentation"
    status: open
    notes: ""
```

**高管不当行为来源：**
```yaml
sources:
  - id: 1
    source: "Subject interview"
    status: open
    notes: ""
  - id: 2
    source: "Board/compensation committee records — relevant resolutions,
             minutes, approvals"
    status: open
    notes: ""
  - id: 3
    source: "Employment agreement and any amendments"
    status: open
    notes: ""
  - id: 4
    source: "Equity records — grants, exercises, vesting"
    status: open
    notes: ""
  - id: 5
    source: "Expense reports and approval records"
    status: open
    notes: ""
  - id: 6
    source: "Email/messaging review — subject, relevant counterparties"
    status: open
    notes: ""
  - id: 7
    source: "Conflict of interest disclosures (or absence thereof)"
    status: open
    notes: ""
  - id: 8
    source: "Outside business activity records"
    status: open
    notes: ""
  - id: 9
    source: "Witness interviews — direct reports, peers, board members"
    status: open
    notes: ""
  - id: 10
    source: "Prior complaints or concerns raised about subject"
    status: open
    notes: ""
  - id: 11
    source: "Upjohn warning documentation"
    status: open
    notes: ""
```

**告密者来源：**
```yaml
sources:
  - id: 1
    source: "Complainant interview"
    status: open
    notes: ""
  - id: 2
    source: "Original complaint or tip — written form if exists"
    status: open
    notes: ""
  - id: 3
    source: "Records related to the underlying allegation (the thing
             complainant blew the whistle on)"
    status: open
    notes: ""
  - id: 4
    source: "Records related to any adverse action taken against complainant
             after the protected activity"
    status: open
    notes: ""
  - id: 5
    source: "Decision-maker interviews — who made the adverse action decision"
    status: open
    notes: ""
  - id: 6
    source: "Comparator data — treatment of similarly situated employees
             who did not engage in protected activity"
    status: open
    notes: ""
  - id: 7
    source: "Email/messaging review — decision-makers, relevant timeframe"
    status: open
    notes: ""
  - id: 8
    source: "Timing analysis — proximity of protected activity to adverse
             action"
    status: open
    notes: ""
  - id: 9
    source: "Respondent/decision-maker interviews"
    status: open
    notes: ""
  - id: 10
    source: "Upjohn warning documentation"
    status: open
    notes: ""
```

展示检查清单后，将其写入
`~/.prismclaw/plugins/legal-chineselaw/employment-legal/investigation-[slug]/sources-checklist.yaml`。

---

## 模式 2：添加数据

由 `/employment-legal:investigation-add` 或"添加到 [matter] 调查"或当律师粘贴文档或访谈笔记时触发。

### 步骤 1 — 识别事项

如果 `~/.prismclaw/plugins/legal-chineselaw/employment-legal/` 中存在多个调查文件夹，询问这些数据属于哪个事项。如果只有一个，继续。

### 步骤 2 — 识别数据类型

询问（如果从上下文不清楚）：
- 访谈笔记（谁的访谈？）
- 文档批次（电子邮件、记录、文件）
- 律师笔记或观察
- Upjohn 警告确认

### 步骤 3 — 文档提取标准

对于任何文档批次，应用以下提取标准。如果文档满足以下任何标准，则会被提取。标准故意设置为略带侵略性地提取——提取误报总比漏掉重要项目好。

**提取标准：**
1. 包含调查任何当事人的姓名（申诉人、被申诉人、先前日志条目中提到的证人）
2. 在关键行为期间由当事人撰写或接收
3. 包含与指控类型相关的关键词（在接收时识别，以及来自先前的日志条目——随着新术语从账户中出现，更新关键词列表）
4. 包含明示或默示的承认（"我不应该"、"我知道这看起来怎么样"、"不要把这写下来"、"删除这个"）
5. 包含与日志中已有账户相矛盾的语言——标记具体矛盾和冲突的日志条目
6. 包含在诉讼中敏感的语言：歧视性术语、威胁、关于受保护特征或活动的讨论、与指控模式匹配的财务异常
7. 是先前账户中提到但尚未出现在文档集中的文档类型（例如，访谈中提到了会议，但未审查日历邀请）→ 作为证据差距记录，而不是提取的文档

**每个已审查文档的处理结果：**
- `surfaced`：满足一个或多个提取标准——作为日志条目添加到日志
- `reviewed-nothing-significant`：已审查，不满足提取标准——在 documents-reviewed.yaml 中记录，仅一行描述

**处理文档批次后，报告：**

```
文档审查完成。
已审查：[N] 个文档
提取：[N] 个潜在重要
已记录为已审查/无重要：[N]
新识别的证据差距：[N]

提取的项目：
[列表，包含一行描述和触发了哪个提取标准]
```

此报告是对"漏掉的重要项目怎么办"的回答。提取标准已记录，提取比率可见，律师可以随时查看完整文档日志。在问答模式下，"在已审查的 [N] 个文档中，我没有看到任何关于 [主题] 的文档"是有意义的陈述，仅仅因为每个已审查的文档都已记录。

### 步骤 4 — 写入日志条目

对于每个提取的项目，追加到 `log.yaml`：

```yaml
- entry_id: [auto-increment]
  entry_type: [interview / document / attorney-note / gap]
  date_of_event: "[date the event occurred — not when logged]"
  date_logged: "[ISO datetime]"
  source: "[witness name/role, or document filename/description]"
  source_type: [complainant / respondent / witness / document / attorney-note]
  issues: ["[which investigation issue(s) this entry relates to]"]
  significance: [high / medium / background]
  summary: "[what this entry adds to the record — 2-5 sentences]"
  quote: "[verbatim quote if significant — otherwise empty]"
  contradicts_entry: [entry_id or null]
  corroborates_entry: [entry_id or null]
  credibility_note: ""
  pull_criterion: "[which criterion triggered — for documents]"
  privilege: attorney-work-product
```

对于证据差距：

```yaml
- gap_id: [auto-increment]
  description: "[what document/source should exist but hasn't been found]"
  identified_from: "[which log entry or account raised this]"
  source_to_obtain: "[where to get it]"
  priority: [high / medium / low]
  status: open
```

### 步骤 5 — 更新来源检查清单

如果添加的数据对应检查清单项目，询问律师是否应将其标记为完成或进行中。不要自动标记为完成——律师决定何时来源已充分覆盖。

---

## 模式 3：查询日志

由 `/employment-legal:investigation-query` 或针对调查提出的任何问题触发（例如，"[witness] 关于...说了什么"、"什么文档证实"、"我们还需要什么"、"每方最强证据是什么"）。

在回答之前阅读完整日志。答案类型：

**事实查询**（"X 关于 Y 说了什么"）：
从日志条目回答，引用条目 ID。如果日志在该主题上没有任何内容："我在此调查日志中没有看到任何关于 [主题] 的信息（已审查 [N] 个条目）。这可能值得标记为差距。"

**冲突查询**（"账户在哪里冲突"）：
呈现所有 contradicts_entry 链接。对于每个冲突：陈述冲突是什么、哪些条目处于紧张状态，以及（如果有）什么文档证据与冲突有关。

**覆盖查询**（"我们还需要什么"/"我们的差距是什么"）：
读取 sources-checklist.yaml 和 log.yaml 中的 evidentiary_gaps。报告：
- 仍然开放的检查清单项目
- 记录的证据差距
- 任何引用尚未收集的来源的账户

**强度查询**（"每个问题上最强的证据是什么"）：
对于日志中的每个问题，识别：最高意义的日志条目、任何文档证实，以及任何未解决的冲突。逐个问题呈现。

**Upjohn 查询**（"我们是否记录了 Upjohn 警告"）：
检查检查清单项目和任何标记为 Upjohn 文档的日志条目。如果尚未完成则标记。

---

## 模式 4：起草或更新备忘录

由 `/employment-legal:investigation-memo` 或"起草备忘录"或"更新备忘录"触发。

### 如果备忘录不存在——初稿

阅读完整日志。在以下内容完成之前不要起草（如果未完成则警告）：
- 每个开放问题至少有一个条目
- 申诉人和被申诉人条目存在
- 已审查来源检查清单（标记任何高优先级的开放项目）

按照以下结构起草备忘录，遵循标准内部调查备忘录做法：

```markdown
[WORK-PRODUCT HEADER — per plugin config ## Outputs — differs by role; see `## Who's using this`]

---

**内部调查备忘录**

收件人：[律师填写]
发件人：[律师填写]
日期：[Date]
事由：内部调查——[事项名称]
状态：初步草稿

---

## 执行摘要

[2-3 段：用通俗语言描述指控、调查范围和方法摘要、以要点形式列出关键发现（已证实 / 未证实 / 无法定论）、建议行动。最后写，但显示在最前面。]

---

## 背景与范围

**触发事件：** [启动调查的事件]

**被调查的指控：**
[日志中的每个问题作为编号指控]

**不在范围内：** [任何明确未调查的内容及原因]

**调查期间：** [被指控行为的日期]
**调查进行：** [开始日期] 至 [当前或结束日期]

---

## 调查方法

**进行的访谈：**
| 证人 | 角色 | 日期 | 备注 |
|---|---|---|---|
[从 source_type = interview 的日志条目填充]

**审查的文件：**
[审查的文件类别摘要、数量、日期范围。
完整文件日志单独维护。]

**其他来源：**
[检查清单中的任何其他来源——政策、HR 记录等]

**限制：** [任何已请求但未获得的来源，任何约束]

---

## 事实发现

*[按问题组织——每项指控一个部分。不按证人，不纯粹按时间顺序。]*

### 问题 1：[指控]

[证据在该问题上显示的叙述。内联引用日志条目 ID（用括号）。账户冲突时，直接呈现冲突——不要掩盖它。书面证据附重要引用。]

### 问题 2：[指控]

[同样的结构]

[每个问题继续]

---

## 可信度评估

*[独立部分。仅处理可信度具有决定性的证人——即，对某个问题的发现取决于相信哪个陈述的情况。]*

### [证人姓名/角色]

**内部一致性：** [一致 / 不一致——注明具体情况]
**佐证：** [哪些书面或其他证据支持或损害该陈述]
**动机：** [相信或不相信该陈述的任何理由]
**举止：** [如果访谈是当面进行的，律师的观察——如不适用或未观察到则留空]
**评估：** [相信 / 不相信 / 部分相信——附依据]

---

## 相关政策

[被指控行为发生时有效的、与问题相关的政策。引用版本。不要引用在行为发生后采纳的政策。]

---

## 结论

| 问题 | 发现 | 依据 |
|---|---|---|
| [问题 1] | 已证实 / 未证实 / 无法定论 | [一句话] |
| [问题 2] | ... | ... |

*发现基于优势证据标准。*

---

## 建议

[按行动类型组织：]

**纪律处分：** [如有——说明依据，而不仅仅是结果]
**政策或流程变更：** [如果政策上的任何差距有影响]
**培训：** [如有必要]
**进一步调查：** [任何未完全解决的线索]
**监控：** [任何所需的后续跟进]

---

## 附录 A：事件时间表

[从日志条目按 date_of_event（而非 date_logged）排序自动生成。
格式：日期 | 摘要 | 来源（条目 ID）]

## 附录 B：审查的文件

[来自 documents-reviewed.yaml 的摘要表格]
```

将草稿写入 `~/.prismclaw/plugins/legal-chineselaw/employment-legal/investigation-[slug]/memo.md`。

### 如果备忘录已存在——更新

阅读备忘录和日志。识别自备忘录最后起草以来添加的日志条目（比较 date_logged 与备忘录的 last-updated 日期）。

报告更改内容：

```
自上次备忘录草稿（[date]）以来，日志中新增了以下内容：

[N] 条新条目
新问题：[如有]
新冲突：[如有]
已解决的空缺：[如有]

需要更新的章节：
  事实发现：[受影响的问题]
  可信度：[任何新的可信度相关条目]
  结论：[应重新审视的任何发现]
  附录 A：[N] 条新时间表条目
```

询问："要我更新完整备忘录，还是只更新受影响的章节？"

应用更新。保留先前的草稿。用 `[UPDATED: date]` 标记更改的章节，直到律师审查。

---

## 模式 5：起草受众摘要

由 `/employment-legal:investigation-summary` 或"为 [audience] 起草摘要"触发。

询问：受众是谁，此摘要支持什么决定或行动？

**HR 摘要**（用于纪律处分决定的 HR 决策）：
- 发生了什么（事实摘要，无法律分析）
- 每项指控的发现（成立/不成立/不确定）
- 建议的行动
- 此摘要中不包含：特权分析、可信度方法、法律敞口评估、律师心理印象
- 标题："Confidential — HR Use Only — Do Not Distribute"
- 不要包含条目 ID 或文档引用——这些保留在备忘录中

**领导层/董事会摘要**（用于治理决策）：
- 一段中的指控和范围
- 关键发现
- 业务影响/敞口（高级别——无具体法律分析）
- 公司正在对此做什么
- 标题："[WORK-PRODUCT HEADER — per plugin config ## Outputs — differs by role; see `## Who's using this`]"

**外部律师简报**（移交给诉讼或更深入的审查）：
- 包括法律敞口分析的完整上下文
- 开放证据线程
- 仍有争议的可信度问题
- 诉讼中最重要的文档
- 标题："[WORK-PRODUCT HEADER — per plugin config ## Outputs — differs by role; see `## Who's using this`]"

---

## 后果行动门槛（响应要求或投诉）

**在生成摘要、备忘录或用于外部响应的内容之前（EEOC/DFEH/州机构指控响应、原告律师要求函响应、监管机构响应或任何正式投诉回复）：** 阅读 `~/.prismclaw/plugins/legal-chineselaw/employment-legal/PRISMCLAW.md` 中的 `## Who's using this`。如果角色是 **非律师**：

> 响应要求、指控或投诉具有法律后果——此处采取的立场是后续程序中的承认、抗辩放弃可能是无意的，基础调查的特权可能丧失。您是否与律师审查过此响应？如果是，继续。如果否，这是带给他们的简报：
>
> - 指控、论坛和截止日期
> - 调查发现了什么（按指控的发现；已审查文档；已访谈证人；是否给予 Upjohn 警告）
> - 任何未解决的证据线程或可信度争议
> - 建议的响应说了什么以及它默示承认了什么
> - 未解决的问题和未解决的问题
> - 可能出什么问题（特权放弃、不一致的事实陈述、错过的积极抗辩）
> - 向律师问什么（这是正确的理论吗；我们是否保留了抗辩；外部公司是否应该接管；什么需要编辑或特权日志）
>
> 如果您需要找到律师、事务律师、大律师或其他授权法律专业人士：请联系您的专业监管机构（美国的州律师协会、英格兰和威尔士的 SRA/律师标准委员会、苏格兰/北爱尔兰/爱尔兰/加拿大的律师协会，或您司法管辖区的同等机构）以获取转介服务。机构和要求函响应是未经训练的回复经常产生比基础指控更多敞口的地方。

如果没有明确的肯定，不要在此门槛之外生成外部响应草稿。仅在组织内使用的内部备忘录、HR 摘要和领导层简报不会触发此门槛（但此技能顶部的特权形成警告仍然适用）。

---

## 此 skill 不做什么

- 做纪律处分决定——它支持律师的发现，而不是 HR 的行动
- 保证特权——特权取决于调查的结构方式，而不是备忘录的标记方式
- 处理它无法阅读的文档——如果文件是无法解析的格式，将它们标记为手动审查
- 进行访谈——它记录访谈笔记，它不访谈证人
- 替代 Upjohn 警告——它跟踪是否给予警告，它不给予警告

## 以下一步决策树结束

按照 PRISMCLAW.md `## Outputs` 的下一步决策树结束。自定义此技能刚刚产生的选项——五个默认分支（起草 X、升级、获取更多事实、观察等待、其他）是起点，而不是锁定。树就是输出；律师选择。



> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
