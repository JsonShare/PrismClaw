---
name: deal-team-summary
name_zh: 交易团队简报
description: >
  将尽调发现聚合为适合受众层级的交易团队简报——为领导层提供执行摘要，
  为团队提供工作摘要。当用户说"向交易团队简报"、"尽调状态如何"、
  "为 [受众] 总结发现"、"交易更新"，或在简报节奏触发时使用。
category: 公司法务
invocation_policy: user_only
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# 交易团队摘要

## 事项上下文

**事项上下文。** 检查执业级 PRISMCLAW.md 中的 `## Matter workspaces`。如果 `Enabled` 是 `✗`（内部用户的默认值），跳过本段其余部分——skills 使用执业级上下文，事项机制不可见。如果启用且没有活跃事项，询问："这是哪个事项的？Run `/corporate-legal:matter-workspace switch <slug>` or say `practice-level`。"加载活跃事项的 `matter.md` 以获取事项特定上下文和覆盖。将输出写入事项文件夹 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/matters/<matter-slug>/`。除非 `Cross-matter context` 是 `on`，否则永远不要阅读另一个事项的文件。

---

## 目的

交易主管不会阅读 200 个发现。他们阅读的是：什么是重要的，自上次简报以来有什么变化，什么需要决策。此 skill 将尽调输出压缩到适合读者的层级。

## 加载上下文

- `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` → 交易团队简报（节奏、格式、业务方阅读什么）
- `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/deals/[code]/deal-context.md` → 交易主管、时间线
- diligence-issue-extraction 输出中的当前发现

## 受众层级

根据 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md`——业务方阅读什么 vs. 留档内容。默认层级：

| 受众 | 获得 | 不获得 |
|---|---|---|
| **董事会 / 执行赞助人** | 前 3-5 个重要问题、价格/结构影响、决策事项 | 类别细节、绿色发现、流程 |
| **交易主管** | 所有红色、所有黄色、进展、决策事项、下一步 | 绿色发现细节 |
| **工作团队** | 所有内容——完整发现、按类别状态、差距 | 无保留 |

如果不明显，询问是哪个层级。

## 摘要

### 执行层级

```markdown
[WORK-PRODUCT HEADER — per plugin config ## Outputs — differs by role; see `## Who's using this`]

> 此简报聚合了特权尽调发现，继承来源的特权和保密状态。超出特权圈的分发（包括更广泛的业务团队）可能放弃特权——在发送前确认分发列表与特权圈匹配。

# [交易代码] — 尽调简报 — [日期]

**状态：** [按计划 / 已发现问题 / 重要发现]
**覆盖率：** 已审查 VDR 的 [X]%

## 重要发现

[最多 3-5 个。每个一段。是什么，为什么对交易重要，我们在做什么。]

## 需要决策

- [ ] [具体决策——价格调整、赔偿要求、退出触发]
  — [谁决定] — [何时之前]

## 自上次简报以来

[什么变化了。新发现、已解决发现、覆盖进展。]
```

### 交易主管层级

同上，加上：

```markdown
## 按类别的所有未解决问题

### 🔴 红色
[发现标题 + 一行——链接到完整发现以获取详情]

### 🟡 黄色
[同上]

## 进展

| 类别 | 已审查文档 | 覆盖率 | 红色 | 黄色 | 状态 |
|---|---|---|---|---|---|
| [名称] | [N/M] | [%] | [N] | [N] | [完成 / 进行中 / 受阻] |

## 差距和后续

- [待补充请求项]
- [向管理层的问题]

## 未来 72 小时

[将审查什么，安排了什么简报]
```

### 工作团队层级

完整发现细节。结构与上述相同，但每个发现获得完整的内部格式块，而不是一行。

## 增量

如果这是定期简报（根据 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` 节奏），以变化为首：

- 自上次简报以来的新发现
- 严重性升级/降级的发现
- 已解决的发现（获得同意、问题澄清消除）
- 覆盖进展

交易主管更关心变化而非状态。"仍然是 12 个黄色"不如"2 个新黄色，3 个已解决"有用。

## 交接

- **来自 diligence-issue-extraction：** 此 skill 阅读累积的发现。
- **交给 closing-checklist：** 任何解决为关闭条件的"需要决策"项进入检查清单。

## 以下一步决策树结束

根据 PRISMCLAW.md `## Outputs` 以下一步决策树结束。根据此 skill 刚刚生成的内容自定义选项——五个默认分支（起草 X、升级、获取更多事实、观察等待、其他）是起点，而非锁定。树就是输出；律师选择。

## 此 skill 不做什么

- 它不做重要性判断——它报告在提取时做出的判断。
- 它不决定交易团队对发现做什么——它呈现决策。
- 它不分发简报——它起草，人工发送。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
