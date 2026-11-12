---
name: matter-briefing
description: 对单个事项进行深度简报——当前姿态、最新变化、下一个截止日期、未决问题和风险重新评估检查，为总法律顾问更新或外部律师通话做好准备。当用户说"简报
  [事项]"、"我们在 [事项] 的进展"或需要了解特定事项时使用。
argument_hint: '[slug]'
category: 争议诉讼
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /matter-briefing

1. 加载 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` → 风险校准 + 相关利益相关者。
2. 遵循以下工作流和参考。
3. 阅读 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/[slug]/matter.md` + `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/[slug]/history.md` + `_log.yaml` 中的日志行。
4. 生成简报：当前姿态、自上次更新以来的变化、下一个截止日期、未决问题、风险重新评估检查（"`risk:` 字段是否仍然反映现实？"）。
5. 标记过期：如果 `last_updated` 超过 30 天，说明这一点。

---

# 事项简报

## 目的

在走到会议室的时间里给律师一个事项的清晰了解。当前姿态、最新变化、下一步、值得重新考虑的内容。

## 加载上下文

- `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/_log.yaml` — 结构化行
- `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/[slug]/matter.md` — 叙述性 intake
- `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/[slug]/history.md` — 事件日志
- `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` — 风险校准（使 "risk: high" 具有特定含义，而非泛泛而谈）

**冲突门槛——不可绕过。** 在简报之前，检查 `_log.yaml` 中的事项 slug。如果事项不在 `_log.yaml` 中，拒绝并路由：

> "我在事项日志中看不到 [matter slug]。首先运行 `/litigation-legal:matter-intake`，以便冲突检查运行并设置事项工作区。我不会对未接收的事项构建简报——冲突检查是门槛。"

## 输入

Slug（必需）。如果模糊或缺失，让用户从活跃事项列表中选择。

## 简报

```markdown
[工作产品标题——根据插件配置 ## Outputs——因角色而异；见 `## 谁在使用这个`]

# [事项名称] — 简报截至 [今天]

**状态：** [status / stage]
**风险：** [rating]（[severity] × [likelihood]）
**重大性：** [category]
**外部律师：** [firm — lead]
**最后更新：** [date] [如果 >30 天标记 ⚠️ 过期]
**冲突：** [status——如果 `pending` 或 `not-run` 标记 ⚠️]

---

## 一段话摘要

[当前姿态。我们在做什么以及为什么。如果捕获了转折事实，请命名。]

## 最新变化

[history.md 中最近 3-5 条记录，最新优先。如果历史很薄，说明这一点。]

## 下一步

- **即将到来的截止日期：** [next_deadline + 它是什么]
- **即将到来的里程碑：** [matter.md 或近期历史中的任何有日期的事项]
- **待决决定：** [matter.md 中标记的未决问题]

## 敞口

[范围 + 自 intake 以来的任何变化。如果已计提准备金，当前准备金 + 是否需要重新校准。]

## 内部负责人

[谁在跟进；是否有人应该加入但尚未加入]

## 风险重新评估检查

*是一个提示，不是答案。*

- `risk: [rating]` 是否仍然感觉合适，还是案件已经发生变化？
- `materiality: [category]` 是否仍然匹配？（新事实可能推动准备金或披露。）
- 是否有事项需要新的利益相关者（例如，发现发展后 CISO 变得相关）？

## 未决问题

[来自 matter.md 和 history 中未解决的任何事项]

## 为对话准备

[如果用户指定了目的——"在外部律师通话前简报我"——定制最后部分：要问的问题、要获取的决定、要提取的更新。如果未给出目的，省略此部分。]
```

## 过期

如果 `last_updated > 30 天前`：在顶部标记并建议会后运行 `/litigation-legal:matter-update [slug]` 以捕获讨论的内容。

## 语气

这不是营销。说出已知的内容；标记未知的。如果事项历史很薄且刚刚开启，简报就很短——这是正确的。不要填充。

## 以下一步决策树结束

根据 PRISMCLAW.md `## Outputs` 以下一步决策树结束。根据此 skill 刚生成的内容自定义选项——五个默认分支（起草 X、升级、获取更多事实、观察等待、其他）是起点，不是锁定。树就是输出；律师选择。

## 此 skill 不做什么

- 预测结果。风险评级是捕获的判断，不是预测。
- 推荐策略。浮出问题；律师回答它们。
- 重新分流。如果用户想重新分流，那是带字段变更的 `/matter-update`——此 skill 读取，不写入。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
