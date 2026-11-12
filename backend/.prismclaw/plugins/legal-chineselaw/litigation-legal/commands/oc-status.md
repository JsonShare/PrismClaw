---
name: oc-status
description: 为活跃投资组合生成每周状态请求电子邮件草稿——每个事项一个 markdown，以及 Gmail 草稿（当 MCP 可用时）。当用户要求外部律师状态请求、每周外部律师签到或想要从投资组合日志起草每个事项的状态电子邮件时使用。
argument_hint: '[--all | --slug=foo | --no-gmail]'
category: 争议诉讼
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /oc-status

要每周运行，请设置循环提醒来调用 `/litigation-legal:oc-status`。自动调度需要计划任务集成，未包含在内。

1. 加载 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/_log.yaml`，按默认规则（或按标志）过滤。
2. 加载 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` → 外部律师指令风格、签署人默认值、预算立场。
3. 遵循以下工作流和参考。
4. 对于范围内的每个事项：阅读 `matter.md` + `history.md`，起草每个事项的电子邮件。
5. 将 markdown 写入 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/oc-status/[YYYY-MM-DD]/[slug].md`。
6. 如果 Gmail MCP 已认证：创建 Gmail 草稿。否则：仅 markdown，在摘要中注明。
7. 写入 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/oc-status/[YYYY-MM-DD]/_summary.md`——运行了什么、跳过了什么以及原因。

---

# 外部律师状态

## 目的

每周为 5-15 个事项写同样的状态请求电子邮件是机械性认知负担。内容按事项一致（状态、待决决定、预算检查）。受众一致（外部律师主办合伙人）。语气一致（根据内部外部律师指令风格）。计划任务起草所有这些；律师审查并发送。

## 加载上下文

- `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/_log.yaml` — 过滤和字段来源
- `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/[slug]/matter.md` — 事项上下文（当前姿态、未决问题）
- `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/[slug]/history.md` — 近期事件以提供询问内容的参考
- `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` → 外部律师指令风格、签署人姓名/电子邮件、预算立场

## 过滤——哪些事项？

默认过滤器：

- `status != closed`
- `outside_counsel.firm != null` 且 `outside_counsel.lead != null`
- 要么：上次更新超过 10 天（有时间发生些什么）或有 `next_deadline` 在 21 天内

跳过最近 10 天内有状态更新的事项（无需重新催促）和 `outside_counsel.email` 为 null 的事项（Gmail 草稿需要电子邮件地址；仍然生成 markdown）。

标志：
- `--all` → 无论时效如何为每个活跃事项起草
- `--slug=[slug]` → 仅为一个事项起草（临时请求）
- `--no-gmail` → 即使 MCP 可用也跳过 Gmail 草稿创建

## 每个事项的电子邮件草稿

每封电子邮件有相同的骨架；内容按事项特定。

**主题行：** 按内部惯例（来自 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` 外部律师指令风格；后备：`[事项：[事项名称]] — 每周状态更新`）

**正文骨架：**

```
[主办合伙人名字]，

[一句开场白——自然，匹配内部语气。]

跟进 [事项名称]。几个事项：

1. **自 [history.md 中捕获的上次更新日期] 以来的状态** — 有什么进展、什么待决？自我们上次联系以来是否有任何提交、听证、通信或通话？

2. **即将到来的截止日期** — 我看到 [日志中的 next_deadline + matter.md 中的任何截止日期]。确认覆盖计划和任何我们应该添加的日期。

3. **待决决定** — [从 matter.md 中提取需要外部律师输入的未决问题；如果没有，省略此编号项并重新编号]

4. **预算** — [按 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` 预算立场为月度/季度/按请求]。相对于 [matter.md 中的预算授权] 我们在什么位置？有什么差异要标记？

[如果重要且相关：5. 具体要求——例如，"请在 [日期] 之前发送驳回动议的最新草稿"——从 matter.md 未决问题中提取。]

[签名——姓名、角色、联系方式。来自 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` 外部律师指令的签署人默认值。]
```

按 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` 外部律师指令风格调整语气——有些律所用"尊敬的律师"正式风格；有些用名字加要点。匹配。

## 输出

### Markdown 草稿

写入：`~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/oc-status/[YYYY-MM-DD]/[slug].md`

每个文件是一封电子邮件，格式为：

```markdown
[工作产品标题——根据插件配置 ## Outputs——因角色而异；见 `## 谁在使用这个`]

# [事项名称] — 外部律师状态请求 — [YYYY-MM-DD]

**收件：** [日志中的 outside_counsel.email]（[outside_counsel.lead]，[outside_counsel.firm]）
**发件：** [来自 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` 的签署人姓名/电子邮件]
**主题：** [主题行]

> 上方的工作产品标题适用于此内部记录。下方的外发电子邮件正文发送给已聘用的外部律师，这本身是特权通信——在发送的电子邮件顶部应用内部特权标记（`~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` 特权惯例），通常是 `Privileged & Confidential — Attorney-Client Communication / Attorney Work Product`，不是此内部工作产品标题。

---

[按骨架的正文]
```

### 发送门槛（每个草稿的结束说明）

在每个 markdown 草稿的正文下方和运行元数据上方附加以下内容——发送前删除：

> 这是供律师审查的草稿状态电子邮件，在发送给外部律师之前。检查是否包含您无意在聘用范围外分享的特权内容、事实准确性、语气和预算立场。不要未经审查发送——即使常规每周签到也可能浮出理论、策略或让发件人无意中写下的让步。

### Gmail 草稿（如果 MCP 可用）

如果 Gmail 草稿创建 MCP 已认证：

- 为每个事项在用户的 Gmail 中创建草稿，填充 `to`、`from`、`subject`、`body`
- 草稿在草稿文件夹中；用户审查并在周一早上发送
- 如果 Gmail MCP 不可用或失败：退回到仅 markdown 并告知用户

### 运行摘要

处理所有事项后，写入 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/oc-status/[YYYY-MM-DD]/_summary.md`：

```markdown
# 外部律师状态运行 — [YYYY-MM-DD]

**处理的事项：** [N]
**创建的草稿：** [N]
**Gmail 草稿：** [已创建 / 已跳过——原因]

## 起草对象

| 事项 | 外部律师主办 | 最后更新 | 包含原因 |
|---|---|---|---|
| [slug] | [lead] | [date] | [过期 / 即将截止 / --all / --slug] |

## 已跳过

| 事项 | 原因 |
|---|---|
| [slug] | 最近更新（最后触碰 [date]） |
| [slug] | 日志中无外部律师电子邮件——使用 `/matter-update [slug]` 更新 |

## 异常

- 未分配外部律师的事项：[列表——如有高/关键风险则标记]
- 有外部律师但日志中无电子邮件的事项：[列表]
```

## 调度

此 skill 设计为每周运行。自动调度需要未随插件提供的计划任务集成。要每周运行，请设置循环提醒来调用 `/litigation-legal:oc-status`——例如，周一早上在您的日历上。

临时：随时 `/oc-status`。`/oc-status --slug=foo` 用于单个事项。

## 此 skill 不做什么

- **发送电子邮件。** 仅草稿。律师审查并发送。
- **生成它没有的内容。** 如果 `matter.md` 很薄，电子邮件就短，询问广泛的状态问题。skill 不会凭空发明具体问题。
- **重试失败。** 如果 Gmail 草稿创建在运行中途失败，skill 记录失败并继续使用 markdown。用户可以在修复认证后重试。
- **重写 history.md。** 读取它获取上下文；不修改。（如果外部律师的回复浮出新事件，使用 `/matter-update [slug]` 记录。）
- **强制最低模板。** 如果内部语气是"一行，名字，结束"，草稿遵守这一点并跳过要点结构。匹配 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md`。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
