---
name: client-comms-log
description: '记录客户沟通——电话、电子邮件、短信、信函、面对面、语音邮件。 仅追加的每个案件记录，带有日期条目、方向、媒介、摘要、行动项目。 与 /client-letter
  和 /status client 配合使用。当记录通话或客户电子邮件、 审查沟通日志或询问"上次我们告诉 [客户] 什么"时使用。

  '
argument_hint: '[case-id] [--add（默认） | --read | --summary | --patterns]'
category: 法律诊所
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /client-comms-log

1. 使用以下工作流。
2. 需要 case-id（如果未提供则提示）。
3. 按标记路由：
   - `--add`（默认）：捕获方向、媒介、学生、摘要、行动项目、后续到期。与用户确认。追加（最近优先）到 `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/client-comms/[case-id]/log.md`。
   - `--read`：显示最近的 N 个条目。
   - `--summary`：一段式压缩阅读。
   - `--patterns`：扫描未回复的沟通、错过的后续、语言差距、语气转变、联系空白。监督导向。
4. 集成：如果日志建立了截止期限，提议 `/legal-clinic:deadlines --add`；通过 `--summary` 路由到 `/legal-clinic:semester-handoff`。

---

# 客户沟通日志

## 目的

保留此日志的四个原因：

1. **不当执业辩护。** 如果客户声称"没有人告诉过我 [X]"，显示相反的日期条目就是答案。临床教授对学生工作承担职业责任；同期记录保护他们。
2. **交接时的连续性。** 下个学期的学生接手并阅读日志；他们不会重新询问客户已回答的问题。
3. **监督可见性。** 六周内五个未回复的语音邮件是一个模式。日志使模式可见，个别学生可能不会自己标记。
4. **文件保留。** 法学院诊所有义务维护完整的客户文件。沟通历史是其中的一部分。

轻量。仅追加。学生的工作是在每次联系后写一个两句话的条目；skill 格式化它并追加。

## 加载上下文

- `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/client-comms/[case-id]/log.md`（如果存在）— 追加目标
- `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/PRISMCLAW.md` → 不大量阅读；此 skill 是案件范围的

## 模式

标记：`--add | --read | --summary | --patterns`（默认：add）

### `--add`（默认）— 记录新条目

**输入：**
- 案件 ID（必需——哪个案件）
- 日期 + 时间（默认：现在）
- 方向：`in`（客户 → 诊所）| `out`（诊所 → 客户）
- 媒介：`call | email | text | letter | in-person | video | voicemail-left | voicemail-received`
- 谁（学生）：姓名
- 谁（客户方）：客户姓名，或如果来自对方律师、家庭成员等则为"third-party: [描述]"
- 持续时间/长度（例如，"10 min call"、"3-paragraph email"、"45 min in-person meeting"）
- 摘要：2-4 句话。发生了什么，什么是实质性的。
- 行动项目：
  - 学生欠客户的（带截止期限）
  - 客户欠学生的（带预期时间）
- 后续到期：如果适用
- 备注：任何重要但不适合上述内容——使用的语言、情感语气、观察到的家庭动态

**写入之前：** 向用户显示格式化的条目并请求确认。诊所记录应该在写入之前审查，而不是之后。

**追加**到 `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/client-comms/[case-id]/log.md`。如果日志不存在，创建它并带有标题：

```markdown
# 沟通日志——[案件名称]

**案件 ID：** [case-id]
**客户：** [姓名]
**开启日期：** [YYYY-MM-DD]

仅追加。最近在顶部。

---
```

然后在顶部添加新条目（最近优先）。

### `--read` — 显示最近的条目

打印最近的 N 个条目（默认 5 个）。在学期中接手案件或客户通话之前很有用。

### `--summary` — 压缩阅读

生成日志的一段式摘要——最近联系、总条目数、常见媒介、学生侧的任何未完成行动项目、任何未回复的沟通。供给 `/semester-handoff` 和 `/status`。

### `--patterns` — 标记日志中的关注点

扫描：

- **客户未回复的沟通。** 客户打电话或发电子邮件 N 次而没有回复条目。
- **错过的后续。** 带有后续到期日期的行动项目，且没有较新的条目解决它。
- **语言/便利问题。** 客户语言标记为非英语；检查外出沟通是否使用该语言。
- **升级模式。** 客户语气转变（沮丧/痛苦）跨越条目。
- **空白。** 活跃案件长期没有联系。

这是一个监督工具。临床教授在他们的案件上运行 `--patterns` 可以看到哪些学生可能需要支持。

## 集成

- **`/client-letter`:** 生成并发送信函后，提议将其记录为外发沟通。
- **`/status client`:** 生成客户面向状态摘要时，提议记录它（这些摘要通常发送给客户）。
- **`/client-intake`:** 每个新案件日志的第一个条目是摄取联系。
- **`/semester-handoff`:** 交接备忘录读取每个案件的 `--summary` 以填充沟通历史部分。
- **`/deadlines`:** 如果沟通建立了截止期限（"客户说他们需要在周五之前回复"），提议 `/deadlines --add`。

## 此 skill 不做什么

- **存储实质性法律分析。** 那存在于摄取、备忘录和状态文件中。日志是沟通记录——联系事实，而不是法律策略。
- **从外部系统自动记录。** 如果诊所使用案件管理系统（Clio），集成可以自动拉取通话日志和电子邮件。那是未来的附加；不是 v1。
- **编辑过去的条目。** 仅追加。如果条目错误，写一个新条目引用并更正它。日志的完整性取决于不重写历史。
- **强制记录纪律。** 如果学生没有记录通话，skill 无法知道。记录卫生是诊所文化问题；skill 只是使记录变得容易。
- **处理特权或仅限律师的备注。** 如果学生需要记录战略思维，那应该进入案件的内部分析文件，而不是沟通日志。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
