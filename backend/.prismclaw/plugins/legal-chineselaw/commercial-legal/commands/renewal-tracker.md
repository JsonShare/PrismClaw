---
name: renewal-tracker
description: 显示即将到期的取消合同取消截止期限，并在通知窗口关闭前发出警告，从维护的续约登记册工作。当用户问"什么即将续约"、"什么续约到期"、"我们是否错过了取消窗口"、"将此添加到续约跟踪器"或定期使用时使用。接收来自
  saas-msa-review 的交接。
argument_hint: '[--days N to change window | --missed for lapsed windows]'
category: 商事合同
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /renewal-tracker

显示什么续约以及你必须在何时之前取消。

## Instructions

1. **阅读 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/renewal-register.yaml`**（配置目录——在 plugin 更新后保留）。
2. **默认模式：** 模式 2 ——接下来 90 天内将发生什么，使用半开区间按紧急程度分组，使每个截止期限恰好落入一个区间：🔴 0–13 天、🟠 14–44 天、🟡 45–89 天。第 14、45 和 90 天是边界——每个属于恰好一个区间，而不是两个。
3. **`--days N`：** 更改窗口。
4. **`--missed`：** 模式 4 ——已过且没有记录取消的取消截止期限。
5. **如果登记册为空且 [CLM] 已连接：** 提供模式 3 ——扫描 [CLM] 中具有续约日期的有效协议并批量加载。
6. **输出包括建议操作：** 联系谁（每个登记册条目的业务所有者）、哪些有无限制定价（在窗口关闭前获得杠杆）。

## Examples

```
/commercial-legal:renewal-tracker
```

```
/commercial-legal:renewal-tracker --days 180
```

```
/commercial-legal:renewal-tracker --missed
```

---

## Purpose

没有人会阅读合同两次。续约日期在审查时提取一次，然后它就存在于某个地方——理想情况下，它会在取消截止期限前 45 天对你大喊大叫，而不是 45 天后。

此 skill 维护续约登记册并显示即将到来的内容。

## 登记册

位于 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/renewal-register.yaml`（配置目录——在 plugin 更新后保留）。每个条目：

```yaml
- counterparty: "示例科技有限公司"
  agreement: "示例平台订阅服务协议"
  signed_date: 2025-06-15
  initial_term_end: 2026-06-15
  current_term_end: 2026-06-15     # 每次自动续约后向前滚动；从这计算 cancel-by_*
  renewal_mechanism: "auto-renew annual"
  notice_period_days: 60
  notice_method: "email"           # email / portal / certified mail / registered post / courier / per contract §X
  transit_buffer_days: 0           # 电子为 0，国内认证邮件为 5，国际挂号邮件为 10——或如合同指定
  cancel_by_calendar: 2026-04-16    # current_term_end 减去 notice_period_days
  cancel_by_effective: 2026-04-16   # 如需要回滚到上一个工作日
  send_by_effective: 2026-04-16    # cancel_by_effective 减去 transit_buffer_days——你必须发送通知的日期
  cancel_by_roll_note: ""           # 例如，"rolled back from Sunday 2026-11-01; verify against contract's business-day definition"
  cancel_by_provenance: "[model calculation — verify against the notice clause]"
  price_on_renewal: "续期时按届时现行定价（无上限）"
  annual_value: 480000             # 人民币金额
  business_owner: "zhang@company.com"
  clm_id:        "CLM-12345"      # 如果已连接合同管理系统
  esign_id:      "esign-abc-123"  # 如果已连接电子签名平台
  status: "active"               # active / cancelled / renewed / lapsed
  notes: "Pricing uncapped — revisit before renewal. Alt vendors: X, Y."
```

**通知传递时间——警报 `send_by_effective`，而不是 `cancel_by_effective`：** 带认证邮件要求的 60 天窗口实际上是 ~55 天。在收到日期发出警报的跟踪器是错过截止期限的跟踪器。计算 `send_by_effective = cancel_by_effective - transit_buffer_days` 并发出警报（模式 2 中的 🔴 / 🟠 / 🟡 紧急区间）从 `send_by_effective` 开始。模式 2 输出的紧急性列显示 `send_by_effective`；详细列显示 `cancel_by_effective`、`notice_method` 和 `transit_buffer_days`，以便读者可以看到增量并挑战缓冲区。

**滚动续约——不向前滚动的登记册只正确一次：** 为记录存储 `initial_term_end`，但从 `current_term_end` 计算 `cancel_by_*`。当续约触发时（取消窗口过去且未发出通知），提示：

> 此合同于 [日期] 自动续约。更新登记册：新的 `current_term_end` 是 [date + renewal period]，新的 `cancel_by_effective` 是 [计算出的]，新的 `send_by_effective` 是 [计算出的]。确认？

一年后，`initial_term_end` 是错误的，只有 `current_term_end` 产生正确的取消日期。

## 每个取消日期的工作日检查

**登记册的取消日期必须是通知有效的最后一个工作日，而不是日历日期。** 落在周末的日历日期是错过续约截止期限的最常见方式。登记册会捕捉它。

当你计算（或摄取）取消日期时：

1. **计算日历日期。** `cancel_by_calendar = initial_term_end - notice_period_days`（或条款指定的任何内容）。这是原始算术。
2. **根据适用法律的工作日回滚。** 合同的适用法律决定哪些节假日计算。中国内地：法定节假日（春节/清明/劳动节/端午/中秋/国庆等），每年国务院公告的放假安排 `[待核验当年具体日期]`。如果是周六，回滚到周五。如果是周日，回滚到周五。如果是法定节假日，回滚到前一个工作日。永远回滚，从不向前——向前意味着通知在窗口关闭后到达。对于港澳台或境外适用法律，如果你无法确定节假日日历，标记它："适用法律为[X]——工作日回滚使用中国内地节假日作为占位符。在依赖有效日期之前，请核验[司法管辖区]的节假日日历。"
3. **检查合同自己的天数计算规则。** 寻找"工作日"、"收到于"、"视为收到"、"当地时间下午 5:00"或通知方式条款。如果合同定义了"工作日"或指定接收机制（认证邮件、带已读回执的电子邮件），该定义控制。标记默认回滚与合同自己规则之间的任何不匹配。
4. **在登记册中记录两个日期。** `cancel_by_calendar` 是原始算术；`cancel_by_effective` 是通知有效的最后一个工作日；`cancel_by_roll_note` 记录它们为何不同（例如，"rolled back from Sunday 2026-11-01; verify against contract's business-day definition"）。每个计算的 `cancel_by_effective` 都带有 `[model calculation — verify against the notice clause]` 的 `cancel_by_provenance` 标签，以便验证标志随日期一起传播，而不是周围的散文。
5. **根据有效日期而不是日历日期发出警报。** 紧急区间（模式 2 中的 🔴 / 🟠 / 🟡）使用 `cancel_by_effective`。模式 2 输出在紧急性列中显示 `cancel_by_effective`，并在详细列中显示 `cancel_by_calendar` 和 `cancel_by_roll_note`，在那里发生了回滚，以便读者可以看到并挑战它。

打印 `cancel_by: 2026-11-01`（星期日）且没有工作日和警告的模式 2 报告是一个无声的错误的有效截止期限。登记册是在摄取时捕捉它的地方——不是后来，当窗口已经移动时。

## 模式

### 模式 1：摄取续约（来自审查的交接）

当 saas-msa-review 或 vendor-agreement-review 发现续约条款时，它交接一个记录。将其附加到登记册。如果对手方已经有条目，询问这是替换（续约协议）还是附加协议。

### 模式 2：即将到来

**默认回看窗口：** 接下来 90 天。

**紧急区间是半开区间——截止期限恰好存在于一个区间中。** 使用距离取消日期的天数（`cancel_by_effective - today`）。第 14、45 和 90 天各自属于恰好一个区间，而不是两个；这里的偏差一错误会将最紧急的项目放入不太紧急的存储桶。

- 🔴 **0–13 天**（14 天内取消——包括今天）
- 🟠 **14–44 天**
- 🟡 **45–89 天**
- （90+ 天的所有内容都在默认回看窗口之外；仅当用户传递 `--horizon` 超过 90 天时才包括）

```markdown
## Renewals — next 90 days

### 🔴 Cancel-by deadline in 0–13 days

| 对手方 | 取消截止日期 | 续期日期 | 年度金额(¥) | 负责人 | 备注 |
|---|---|---|---|---|---|
| [名称] | **[日期]** | [日期] | ¥[金额] | [邮件] | [备注] |

### 🟠 取消截止日期在 14–44 天内

[相同的表格]

### 🟡 取消截止日期在 45–89 天内

[相同的表格]

---

**建议操作：**
- [ ] [Counterparty] —联系 [业务所有者]：我们要保留这个吗？
- [ ] [Counterparty] ——定价未限制；在我们失去杠杆之前从替代方案获取报价
```

如果登记册在窗口中有超过约 10 个续约，或任何时候用户询问：提供仪表板（请参阅 PRISMCLAW.md `## Outputs → Dashboard offer for data-heavy outputs`）。为此输出塑造提议——按紧急层级的计数（🔴 / 🟠 / 🟡）、取消时间线和带有对手方、续约日期、年度 $ 和所有者的可排序登记册。

### 模式 3：扫描 [CLM]/电子签名工具以填充登记册

如果 MCP 已连接且登记册为空或过时：

1. 查询 [CLM] 中状态为"Active"且具有续约日期字段的所有协议
2. 查询电子签名平台（e签宝/契约锁/上上签等）中过去 24 个月内元数据中包含"订阅"/"续期"/"自动续期"/"自动续约"的已完成签署记录
3. 对于每个命中，提取续约机制并添加到登记册
4. 标记任何无法从元数据确定续约日期的——这些需要人工阅读合同

这是一次性批量加载。之后，摄取在审查时发生。

### 模式 4：错过的窗口（坏消息报告）

```markdown
## Missed cancellation windows

以下协议的取消截止期限已过，且未记录取消：

| 对手方 | 原取消截止日期 | 续期日期 | 状态 |
|---|---|---|---|
| [名称] | [日期] | [日期] | 将于 [日期] 自动续期 |

**选项：**
- 协商延迟取消（很少有效但值得询问）
- 接受续约，现在标记下一年的取消日期
- 检查协议是否有其他终止权利（为方便、因故）
```

## 关卡：接受或拒绝续约

跟踪续约日期是研究。对其采取行动*——发送不续约通知、让自动续约在取消日期过去后触发，或签署续约表格——是后果法律步骤。

**在继续接受或拒绝续约之前（包括发送不续约通知或让自动续约在取消日期之后运行）：** 阅读执业档案中的 `## 谁在使用这个`。如果角色是非律师：

> 此步骤有法律后果（你要么承诺另一个期限，要么终止关系）。你是否已与律师审查此内容？如果是，继续。如果否，这是带给他们的简报：
>
> [生成一页摘要：对手方、当前期限结束和取消日期、续期定价机制、如果我们不做任何事情会发生什么、如果我们想更换有哪些替代供应商，以及在窗口关闭前要问律师的三件事。]
>
> 如果你需要找到律师：联系你所在地区的律师协会（各省市律师协会均提供律师查询和推荐服务）。

没有明确的是，不要越过此关卡继续。

## 集成：renewal-watcher agent

此 plugin 中的 renewal-watcher agent 按计划（默认每周）运行此 skill，并将"即将到来"报告发布到 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` → `## House style` → `## House style` → 工产品去向的命名频道。模式 2 是代理的主要输出。

## 此 skill 不做的事

- 它不取消合同。它告诉你何时决定。
- 它不决定是否续约。它显示截止期限和业务所有者。
- 它不阅读合同以查找续约日期——这在审查时发生。如果合同在登记册中没有续约日期，它是手动添加的，有人需要填补空白。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
