---
name: integration-management
description: '交易后 M&A 整合跟踪器——分阶段工作计划、同意跟踪、大规模合同转让、每周状态报告。从可用的任何交易文件（购买协议、交易摘要、关闭检查清单）初始化，并连接到
  M&A 冷启动中的 deal-context.md 和 closing-checklist.yaml。当用户说"integration"、"post-close"、"post-closing"、"consents
  outstanding"、"contract assignment"、"integration status"或"what''s left on the deal"时使用。

  '
argument_hint: '[--init | --contracts | --report | --update | --export [--format csv|table]
  [--section all|consents|contracts|workplan]] [--deal [code]]'
category: 公司法务
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /integration-management

1. 加载 deal-context.md 以获取交易代码、目标公司、关闭日期、交易主管。
2. 加载 integration-tracker.yaml（如果存在）（或在 --init 时创建）。
3. 使用以下工作流。
4. 按标志路由：
   - `--init`：模式 1 — 读取购买协议，构建分阶段工作计划、同意跟踪器
   - `--contracts`：模式 2 — 导入合同列表（存储库或上传），分层分类
   - `--report`：模式 3 — 生成状态报告
   - `--update`：模式 4 — 手动更新或解析上传的状态文件
   - `--export`：模式 5 — CSV 或表格导出
5. 读/写 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/deals/[code]/integration-tracker.yaml`。
6. 任何写入后：显示变更摘要并浮现任何新标志。

---

## 事项上下文

**事项上下文。** 检查执业级 PRISMCLAW.md 中的 `## Matter workspaces`。如果 `Enabled` 为 `✗`（内部用户的默认值），跳过本段的其余部分——skills 使用执业级上下文，事项机制不可见。如果已启用且没有活跃事项，询问："这是哪个事项的？运行 `/corporate-legal:matter-workspace switch <slug>` 或说 `practice-level`。"加载活跃事项的 `matter.md` 以获取事项特定上下文和覆盖。将输出写入事项文件夹 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/matters/<matter-slug>/`。除非 `Cross-matter context` 为 `on`，否则永远不要阅读另一个事项的文件。

---

## 目的

外部律师关闭了交易。法律继承了这个烂摊子。此 skill 是交易后整合的项目管理层——不是业务整合，不是 IT 系统，不是 HR 组织设计。法律工作流：同意、合同转让、实体合理化、IP 记录、购买协议义务。它跟踪什么已完成、什么到期、什么受阻，以及什么需要决定。

---

## 跟踪器文件

位于 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/deals/[code]/integration-tracker.yaml`。读取 `deal-context.md` 获取交易代码、目标名称、关闭日期和交易主管。如果 `closing-checklist.yaml` 存在，从中继承任何交易后项目。

```yaml
# integration-tracker.yaml

metadata:
  deal_code: "[code]"
  target: "[company name]"
  close_date: "[YYYY-MM-DD]"
  deal_lead: "[name]"
  outside_counsel: "[firm and lead attorney]"
  last_updated: "[date]"
  last_status_report: "[date or null]"

pa_dates:
  required_consents_deadline: "[YYYY-MM-DD — extract from PA]"
  rep_survival_expires: "[YYYY-MM-DD]"
  escrow_release: "[YYYY-MM-DD or null]"
  earnout_milestones:
    - description: "[milestone]"
      measurement_date: "[YYYY-MM-DD]"
      payment_date: "[YYYY-MM-DD]"
      owner: "finance"   # always finance — legal tracks date only

workplan:
  day_1:
    target_date: "[close_date + 7 days]"
    items: []
  day_30:
    target_date: "[close_date + 30 days]"
    items: []
  day_90:
    target_date: "[close_date + 90 days]"
    items: []
  day_180:
    target_date: "[close_date + 180 days]"
    items: []

required_consents: []
desired_consents: []

contracts:
  source: "[repository / manual-upload / disclosure-schedule]"
  repository_path: "[path or null]"
  last_imported: "[date]"
  total: 0
  tier_1: []
  tier_2: []
  tier_3: []
  tier_4: []
```

**工作计划项目结构：**
```yaml
- id: "W-001"
  description: "[action item]"
  phase: "[day_1 / day_30 / day_90 / day_180]"
  owner: "[legal-owns / legal-supports]"
  workstream: "[legal / hr / it / finance / real-estate / other]"
  priority: "[critical / high / medium / low]"
  deadline: "[YYYY-MM-DD or null]"
  deadline_basis: "[pa-obligation / regulatory / best-practice]"
  status: "[not_started / in_progress / complete / blocked / deferred]"
  blocker: "[description or null]"
  depends_on: "[item id or null]"
  notes: ""
```

**同意条目结构：**
```yaml
- id: "CON-001"
  counterparty: "[name]"
  contract_type: "[customer / vendor / lease / IP-license / financial / other]"
  required_consent: true        # true = named in PA Required Consents schedule
  pa_deadline: "[YYYY-MM-DD]"   # only for required_consent: true
  status: "[not_started / outreach_sent / in_negotiation / obtained / waived / refused]"
  assigned_to: "[name or null]"
  outreach_date: "[date or null]"
  obtained_date: "[date or null]"
  notes: ""
```

**合同条目结构：**
```yaml
- id: "C-001"
  name: "[contract name or filename]"
  counterparty: "[party name]"
  contract_type: "[MSA / SaaS / lease / IP-license / employment / NDA / other]"
  annual_value: "[amount or unknown]"
  assignment_mechanism: "[auto-assign / consent-required / coc-provision / silent]"
  tier: 1   # 1=Required Consent, 2=material+consent-required, 3=CoC, 4=auto-assign
  required_consent: false
  pa_deadline: "[YYYY-MM-DD or null]"
  status: "[not_reviewed / no_action / consent_pending / outreach_sent / in_negotiation / consent_obtained / assignment_complete / waived / refused / coc_triggered]"
  assigned_to: "[name or null]"
  notes: ""
  last_updated: "[date]"
```

---

## 模式 1：初始化

```
/corporate-legal:integration-management --init [--deal [code]]
```

### 步骤 1：加载交易上下文

读取 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/deals/[code]/deal-context.md`。如果未找到：询问交易代码名、目标公司、关闭日期、交易主管和外部律师。如果 deal-context.md 不存在则写入它。

读取 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/deals/[code]/closing-checklist.yaml`（如果存在）。任何标记为交易后的项目都成为第 1 天或第 30 天的工作计划项目（从 closing-checklist 继承状态）。

### 步骤 2：读取交易输入

**完整的购买协议产生最完整的跟踪器。** 购买协议的必要同意清单和交易后契约章节是硬性截止日期和法律义务的权威来源。但 skill 可以从任何可用的内容进行有用的初始化——部分输入产生律师填写的入门跟踪器，而不是空白页。

> 你有哪些交易文件？共享任何存在的内容：
>
> **理想情况：** 购买协议（上传或连接的文档路径）。我将读取交易后契约、必要同意清单、存续期、托管条款和收益分期条款。
>
> **也有用——共享以下任何组合：**
> - 交易摘要或条款清单（给我关键经济条款和时间表）
> - 来自外部律师的整合待办事项或交易后检查清单
> - 现有工作计划或整合跟踪器（我将导入并从中继续）
> - 关闭检查清单——如果由 M&A 冷启动 skill 生成，我将从 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/deals/[code]/closing-checklist.yaml` 自动继承它
> - 仅必要同意列表（如果购买协议由外部律师持有）
>
> **如果你没有任何书面内容：** 用简单语言告诉我交易——谁被收购、何时关闭、主要开放项目是什么——我将从标准的第 1/30/90/180 天工作计划构建一个入门跟踪器，供你编辑。

**根据提供的内容会有什么变化：**

| 输入 | 你得到什么 |
|---|---|
| 完整购买协议 | 完整工作计划 + 附截止日期的必要同意 + 购买协议日期 |
| 购买协议 + 合同列表 | 完整跟踪器 + 合同转让层级列表 |
| 交易摘要/待办事项列表 | 标准工作计划框架，必要同意作为占位符 |
| 什么都没有 | 标准工作计划框架；律师填写同意和合同列表 |

跟踪器被设计为逐步构建——今天一个框架，随着更多信息可用而填充。

**从购买协议提取：**

*必要同意清单：*
- 对于每个同意：对手方名称、合同类型和合同截止日期。设置为 required_consent: true，并填充 pa_deadline。

*交易后义务：*
- 将每个义务映射到工作计划项目。根据截止日期分配到正确的阶段。在 deadline_basis 中标记为 pa-obligation。

*关键日期：*
- 必要同意截止日期——从购买协议中提取
- 陈述与保证存续到期——从购买协议中提取具体存续期。一般、基本和税务陈述通常有不同的存续期；提取购买协议定义的每一个并分别记录。不要假设默认值。
- 托管释放日期——从购买协议中提取
- 任何收益分期计量和支付日期——添加到 pa_dates.earnout_milestones，所有者始终设置为"finance"

### 步骤 3：构建分阶段工作计划

为每个阶段生成标准工作计划项目。添加在步骤 2 中提取的购买协议义务。从关闭检查清单继承的项目已预先填充。

**第 1 天——法律主导：**
- 实体名称变更申报（如果收购的实体正在更名）[优先级：关键]
- 银行账户签署人更新——向银行提供关闭文件通知 [优先级：关键]
- 注册代理人所有权变更通知 [优先级：高]
- 关键 IP 转让执行——如果任何 IP 转让从关闭延迟 [优先级：关键]
- 域名和社交媒体账户转让 [优先级：高]
- D&O 保险——确认收购实体董事的追溯保单已绑定 [优先级：关键]
- 州法律要求的州务卿所有权通知 [优先级：高]

**第 1 天——法律支持：**
- 员工公告和沟通（HR 主导，法律审查）[优先级：关键]
- 福利第 1 天覆盖确认（HR 主导，法律就 COBRA 和计划条款提供建议）
- 客户沟通信函（业务主导，法律审查准确性）

**第 30 天——法律主导：**
- 必要同意初步推进——联系所有对手方，记录外联 [优先级：关键]
- IP 转让在 USPTO 的记录（专利、商标）[优先级：高]
- 版权转让申报 [优先级：中]
- 商标转让记录 [优先级：高]
- 重大合同审查——完成第 1 层和第 2 层合同转让分析 [优先级：高]
- 保险追溯保单最终确认 [优先级：高]

**第 30 天——法律支持：**
- 数据迁移隐私审查（IT 主导，法律就数据传输机制提供建议）
- 房地产租赁转让条款审查（设施主导，法律提供建议）

**第 90 天——法律主导：**
- 必要同意截止日期——所有必要同意必须获得或升级 [优先级：关键，截止日期：pa_dates.required_consents_deadline]
- 实体合理化决定——建议保持独立/合并/解散 [优先级：高]
- 福利计划承接或终止文件 [优先级：高]
- 次要同意推进——剩余未决同意 [优先级：高]
- 第 3 层控制权变更合同解决 [优先级：关键]

**第 90 天——法律支持：**
- 完整 HR 协调文件（HR 主导，法律就就业法提供建议）

**第 180 天——法律主导：**
- 实体合并申报——如果合理化决定是合并 [优先级：高]
- 实体解散申报——如果合理化决定是关闭 [优先级：高]
- 完整合同变更——需要收购方名称的合同 [优先级：高]
- 陈述存续跟踪——注意即将到来的到期日期 [优先级：中]

生成后显示摘要：

```
整合跟踪器已初始化——[交易代码] / [目标公司]

关闭日期：[date]
必要同意截止日期：[date]（距今 [N] 天）
陈述存续到期：[date]

工作计划项目：[N]（[N] 个法律主导，[N] 个法律支持）
必要同意：[N]（来自购买协议清单）
期望同意：[N]（来自尽调——无购买协议截止日期）

合同转让：尚未导入——运行 --contracts 以填充

下一步：运行 /corporate-legal:integration-management --contracts 导入合同列表，然后运行 --report 查看你的第一个状态摘要。
```

---

## 模式 2：合同转让

```
/corporate-legal:integration-management --contracts [--deal [code]]
```

这是专用的合同转让初始化。与主初始化分开，以便可以独立运行并在合同列表变化时重新运行。

### 步骤 1：获取合同列表

两条路径——使用适用的那条：

**路径 A：已连接的存储库**

> 你的合同存储库已连接吗？（Google Drive、Box、SharePoint 或交易后仍可访问的 VDR？）
>
> 如果是：给我被收购公司合同的文件夹路径或文件夹名称。我将拉取那里的列表并读取每份合同的转让条款和对手方。

搜索已连接的存储库。对于找到的每个文档：
- 提取文件名和文件路径
- 读取文档——识别：合同对手方（对手方名称）、合同类型（从标题或主题事项）、转让条款文本、如果存在的控制权变更条款文本，以及如果说明的年度价值。

**路径 B：手动列表上传**

> 上传合同列表。这可以是：
> - 购买协议披露清单中的重大合同清单
> - 来自其合同管理系统的 CSV 或 Excel 导出
> - 手动准备的列表
>
> 最低必需列：合同名称、对手方。有用但可选：合同类型、年度价值、转让条款文本。

读取上传的列表。对于未提供转让条款文本的合同，将 assignment_mechanism 设置为"not_reviewed"并标记以供后续处理。

**路径 C：披露清单**

如果既没有存储库也没有列表可用，读取购买协议披露清单中的重大合同清单（来自 --init 中上传的购买协议）。这给出最低必需列表——各方和合同类型。转让条款将需要手动审查。

### 步骤 2：确定转让机制

对于每份合同，对转让机制进行分类：

| 机制 | 定义 | 层级 |
|---|---|---|
| `consent-required` | 明确条款禁止未经对手方同意转让 | 1 或 2 |
| `coc-provision` | 控制权变更条款给予对手方由交易触发的终止或同意权 | 3 |
| `auto-assign` | 无限制，或明确允许转让给附属公司或继承者 | 4 |
| `silent` | 无转让条款——默认适用管辖法律。研究合同沉默时转让的管辖法律默认规则并引用控制规则。标记供律师审查。 | 2 |
| `not_reviewed` | 无法读取或找到转让条款 | 标记供手动审查 |

对于购买协议必要同意清单中标记的合同：无论转让机制分类如何，覆盖层级为 1。

### 步骤 3：层级分配

```
第 1 层——必要同意：[N] 份合同
  在购买协议清单中命名，硬性截止日期 [date]，必须获得同意

第 2 层——重大，需要同意：[N] 份合同
  存在转让限制，不在购买协议清单中
  建议时间表：在第 90 天内获得

第 3 层——控制权变更条款：[N] 份合同 ⚠️
  对手方有由关闭触发的终止或同意权
  需要立即行动：立即联系对手方——控制权变更可能已经触发

第 4 层——自动转让/无需行动：[N] 份合同
  自动转让或通过附属公司/继承者条款
  仅跟踪——无需外联

未审查：[N] 份合同
  无法确定转让机制——需要手动审查
```

单独突出显示第 3 层。控制权变更条款可能已经在关闭日期触发——对手方可能有正在运行的终止权。

### 步骤 4：生成状态条目

对于每份合同，创建一个跟踪器条目，包含：
- 所有提取的字段（对手方、类型、价值、机制、层级）
- 初始状态：第 4 层 → `no_action`；第 3 层 → `coc_triggered`；第 1/2 层 → `consent_pending`；未审查 → `not_reviewed`
- 从必要同意清单为第 1 层填充 pa_deadline

---

## 模式 3：状态报告

```
/corporate-legal:integration-management --report [--deal [code]]
```

读取当前跟踪器状态。生成：

```
[工作产品标题——根据 plugin 配置 ## Outputs——因角色而异；见"## 谁在使用这个"]

> 此状态报告来自购买协议、尽调发现和交易后整合记录。它继承其特权和机密状态——超出特权圈（对手方、更广泛的业务团队）的分发可能放弃特权。在发送前确认收件人列表。

整合状态——[交易代码] / [目标公司]
[日期]——交易后第 [N] 天

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

执行摘要
[2-3 句段落：整体状态、最大风险、上次报告以来的关键进展]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

必要同意  [截止日期：DATE——剩余 N 天]
  已获得：        [N] / [total]  ████████░░  [%]
  谈判中：        [N]
  已发送外联：    [N]
  未开始：        [N]
  已拒绝：        [N] ⚠️

⚠️ 风险中：[对手方]——[N] 天后截止，外联无回应
⚠️ 已拒绝：[对手方]——购买协议义务未满足；升级到外部律师

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

合同转让
  第 1 层（必要同意）：   [N] 完成 / [N] 进行中 / [N] 待处理
  第 2 层（重大合同）：   [N] 完成 / [N] 进行中 / [N] 待处理
  第 3 层（控制权变更条款）：[N] 已解决 / [N] 未决 ⚠️
  第 4 层（自动转让）：   [N]——无需行动

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

工作计划——法律主导
  🔴 已逾期（[N]）：
    [item]——原定于 [date]

  ⏰ 本周到期（[N]）：
    [item]——[date] 到期

  ✅ 上次报告以来已完成（[N]）：
    [item]——[date] 完成

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

受阻和需要决定的事项
  [item]——受阻于：[description]——负责人：[name]
  [item]——需要决定：[description]——建议：[option]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

即将到来的关键日期
  [date]——[里程碑/截止日期]
  [date]——陈述存续到期——确认无未决赔偿主张

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 模式 4：更新

```
/corporate-legal:integration-management --update [--deal [code]]
```

**手动更新：** 律师告诉 PrismClaw 什么发生了变化。

> "我们获得了 Salesforce 同意。将其标记为已获得，分配给 [name]，日期为今天。"
> "实体合理化决定是合并。更新状态并将合并申报添加到第 180 天。"
> "[对手方] 拒绝了同意。标记它并注意我们需要外部律师就这是否触发购买协议赔偿主张提供建议。"

PrismClaw 更新相关跟踪器条目，重新计算任何下游状态（例如，如果所有第 1 层同意现在都已获得，将购买协议义务标记为已满足），并显示变更内容。

**上传更新：** 工作流负责人或外部律师发送状态文件。

> 上传来自 [外部律师/HR 负责人/公司发展团队] 的状态更新。我将解析它并更新跟踪器。

读取上传的文件。通过对手方名称或工作计划项目描述将描述的项目与跟踪器条目匹配。更新状态字段。标记更新中与现有跟踪器条目不匹配的任何项目——可能是需要添加的新项目。

任何更新后，显示：
```
更新了 [N] 个项目。

变更：
  CON-003 Salesforce: not_started → obtained
  W-014 实体合理化: in_progress → complete

新标志：
  CON-007 [对手方]: 已拒绝——购买协议义务可能未满足。考虑：
  外部律师审查赔偿主张。⚠️
```

---

## 模式 5：导出

```
/corporate-legal:integration-management --export [--format csv|table] [--section all|consents|contracts|workplan]
```

生成平面 CSV 或 markdown 表。默认：所有章节，CSV。

CSV 格式——每个项目一行，由 `section` 列指示章节。
列因章节而异：

*工作计划：* id, phase, description, owner, workstream, priority, deadline, status, blocker

*同意：* id, counterparty, contract_type, required_consent, pa_deadline, status, assigned_to, obtained_date, notes

*合同：* id, name, counterparty, contract_type, annual_value, assignment_mechanism, tier, required_consent, pa_deadline, status, assigned_to, notes

导出是可共享的格式——适合外部律师、公司发展或董事会整合更新。

---

## 此 skill 不做什么

- 它不管理业务整合工作流（IT、HR、财务、房地产）。它跟踪法律在这些工作流中的接触点，并在需要法律意见时发出标志。所有权保留在业务职能部门。
- 它不起草同意请求信函或变更协议——这些由 written-consent skill 或外部律师生成。
- 它不就赔偿主张或购买协议违约提供建议。当同意被拒绝或截止日期错过时，它标记情况——后果的法律分析是律师的判断。
- 它不跟踪收益分期绩效。收益分期里程碑和支付日期作为参考日期出现在跟踪器中，所有者设置为财务。业务驱动数字。
- 它在状态报告时不实时读取合同。合同状态是律师在跟踪器中更新的内容。skill 在报告时读取跟踪器，而不是合同。


## 公式注入防护

在 Excel、Sheets 或 CSV 输出中写入任何单元格之前，中和公式注入。来自对手方的文本（合同引用、当事方名称、注册代理人数据、CLM 导出）是攻击者控制的。以 `=`、`+`、`-`、`@`、制表符、回车或换行符开头的单元格将被解释为公式或破坏行结构。

- **以单引号为前缀：** `'=SUM(A1:A10)` → `=SUM(A1:A10)`（显示为文本，不执行）
- **适用于包含从文档、工具结果或用户粘贴来源的文本的每个单元格。** 你控制的列标题和你产生的计算值是安全的。
- **CSV：还要转义嵌入的逗号、双引号、换行符**（RFC 4180 引用）。
- 这不是可选的。你的用户在 Excel 中打开的电子表格触发宏或通过 DDE 渗染数据是对你的用户的供应链攻击。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
