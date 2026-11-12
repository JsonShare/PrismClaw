---
name: portfolio-status
description: 从 _log.yaml 汇总投资组合——风险分布、即将到来的截止日期、过期事项、重大性汇总、阶段分布和标记的异常。当用户问"我们进展如何"、"有多少个活跃事项"或想要跨所有活跃事项的投资组合汇总或状态时使用。
argument_hint: '[--all | --risk=high | --stale]'
category: 争议诉讼
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /portfolio-status

1. 加载 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` → 风险校准（定义如何读取 `risk:` 字段）。
2. 遵循以下工作流和参考。
3. 解析 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/_log.yaml`。默认过滤关闭的事项（使用 `--all` 包含）。
4. 生成汇总：风险分布、14/30/60 天内的截止日期、超过 30 天无更新的事项、重大性汇总、阶段分布。
5. 标记异常——所有标记为关键的、逾期的 next_deadline、没有分配外部律师但风险为中或高的事项。

---

# 投资组合状态

## 目的

一次阅读回答：我现在拥有什么，什么需要关注，什么正在滑落？输出可扫描——为有三分钟直到下一个电话的律师设计。

## 加载上下文

- `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/_log.yaml` — 事实来源
- `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` — 风险校准（正确解读 risk/materiality 字段）

## 标志和过滤器

默认：仅活跃事项（排除 `status: closed`）。

标志：
- `--all` — 包含关闭的
- `--risk=high`（或 `critical` / `medium` / `low`）— 按风险区间过滤
- `--stale` — 仅 `last_updated` 超过 30 天的事项
- `--type=employment` — 按事项类型过滤
- `--owner=[name]` — 按业务/HR/通讯负责人过滤

## 汇总

```markdown
[工作产品标题——根据插件配置 ## Outputs——因角色而异；见 `## 谁在使用这个`]

# 投资组合状态 — [今天]

**活跃事项：** [N]
**已关闭（年初至今）：** [N] *（仅在使用 --all 时显示）*

---

## 按风险

| 风险 | 数量 | 事项 |
|---|---|---|
| 关键 | [N] | [slugs] |
| 高 | [N] | [slugs] |
| 中 | [N] | [仅计数——使用 `--risk=medium` 展开] |
| 低 | [N] | [仅计数] |

## 即将到来的截止日期

| 时间范围 | 事项 |
|---|---|
| 14 天内 | [slug — 截止日期 — 摘要] |
| 15–30 天 | [...] |
| 31–60 天 | [...] |

*逾期的 `next_deadline` 在下方单独标记。*

## 重大性

| 类别 | 数量 | 总敞口（中点） |
|---|---|---|
| 已计提准备金 | [N] | [$X] |
| 已披露 | [N] | [$X] |
| 监控中 | [N] | — |
| 无 | [N] | — |

## 按阶段

[表格：诉答 / 发现 / 实体动议 / 庭审准备 / 和解 / 上诉]

---

## ⚠️ 异常和标记

- **逾期截止日期：** [列出 next_deadline 已过的 slug]
- **过期（>30 天无更新）：** [列表]
- **冲突未解决：** [列出 `conflicts.status in [pending, not-run]` 的 slug]
- **冲突已绕过（覆盖活跃）：** [列出 `conflicts.override.by` 已填充的 slug——永久标记直到手动清除]
- **高/关键风险无外部律师：** [列表]
- **已计提准备金但 last_updated 超过 60 天：** [列表] — 准备金重新校准可能过期
- **活跃诉讼未发布保留：** [列表]
- **缺失字段：** [slug → 字段]

---

## 结束建议

[一两句关于首先看什么的话，如果有任何突出的内容。不是套话——仅当确实有突出内容时。]
```

## 异常规则

这些是使 skill 有用而非装饰性的检查：

1. **逾期截止日期：** `next_deadline < today` 且 `status != closed`
2. **过期：** `last_updated < today - 30d` 且 `status != closed`
3. **冲突未解决：** `conflicts.status in [pending, not-run]` 且 `status != closed`
3b. **冲突覆盖活跃：** `conflicts.override.by != null`（永不自动清除）
4. **高风险无外部律师：** `risk in [high, critical]` 且 `outside_counsel.firm == null`
5. **过期准备金：** `materiality == reserved` 且 `last_updated < today - 60d`
6. **保留缺口：** `status in [threatened, active, discovery, trial, appeal]` 且 `legal_hold.issued == false` — 保全义务在合理预期时即附加，因此 `threatened` 事项也在范围内。
7. **缺失字段：** 任何必需字段为 null——`risk`、`materiality`、`status`、`opened`、`conflicts.status`

## 以下一步决策树结束

根据 PRISMCLAW.md `## Outputs` 以下一步决策树结束。根据此 skill 刚生成的内容自定义选项——五个默认分支（起草 X、升级、获取更多事实、观察等待、其他）是起点，不是锁定。树就是输出；律师选择。

如果投资组合有超过约 10 个事项，或用户任何时候要求：提供仪表板（见 PRISMCLAW.md `## Outputs → 数据密集型输出的仪表板提议`）。为此输出定制提议——按风险层级计数、即将到来的截止日期时间线，以及带有状态、冲突检查和最后触碰日期的可排序事项分类账。

## 此 skill 不做什么

- 做决定。它浮出需要关注的内容；用户决定优先级。
- 假装不拥有的精确度。敞口中点是粗略的，应该这样标记。
- 替代真正的 MMS。这是工作记忆汇总，不是记录系统。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
