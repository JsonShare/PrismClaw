---
name: comments
description: 审查开放的征求意见期，记录决定，跟踪截止日期。当征求意见稿有开放的评议窗口且你需要呈现截止日期、决定是否提交意见，或记录提交/不提交/放弃决定（--decide
  CMT-ID）时使用。
argument_hint: '[可选：--decide CMT-ID]'
category: 监管合规
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0

中国法改造说明：2026-06
- 替换 NPRM/notice-and-comment rulemaking 为中国监管征求意见语境
- 通知渠道替换为企业协作/钉钉/企业微信，Slack 已删除
- 替换外国律师协会引荐为中国律师协会
-->


# /comments

## 目的

监管征求意见稿有截止日期。提交或不提交意见的决定是律师的判断——但截止日期在没有记录决定的情况下消失是风险。此技能呈现开放的评议期并记录决定。

## 加载上下文

`~/.prismclaw/assets/plugins/legal-chineselaw/regulatory-legal/comment-tracker.yaml` → 所有跟踪的征求意见稿及其状态。
`~/.prismclaw/assets/plugins/legal-chineselaw/regulatory-legal/PRISMCLAW.md` → 默认评议决策所有者。

## 默认视图——开放评议期

```markdown
## 评议期台账 — [日期]

### ⏰ <14 天内截止

| ID | 征求意见稿 | 截止日期 | 剩余天数 | 决定 | 所有者 |
|---|---|---|---|---|---|
| CMT-001 | [名称] | [日期] | [N] | 未决定 | [所有者] |

### 🟡 开放 (>14 天)

[相同表格]

### 最近决定

| ID | 征求意见稿 | 决定 | 理由 |
|---|---|---|---|
| CMT-002 | [名称] | 不提交 | [理由] |

---

**总计开放：** [N]  **<30 天内未决定：** [N]
```

## 记录决定

```
/regulatory-legal:comments --decide CMT-001
决定：[filing / not-filing / waived]
理由："[简短——例如，'本规则不适用于我们的业务模式' 或 '就第 3 条提交意见']"
```

更新台账。如果决定是"filing"：提示提交截止提醒（评议截止前 5 个工作日用于内部审查）。

## 通知

首次检测到征求意见稿时（由 reg-feed-watcher 填充）：如果配置了企业协作/钉钉/企业微信 MCP 且设置了 `owner_dm`，则向评议决策所有者发送 DM。

如果决定仍为"未决定"，则在截止前 14 天提醒。
如果在截止前 3 天仍未决定——提升紧迫性。

## 后果行动关卡（向监管机构提交评议意见）

**在将决定记录为"filing"之前——并且始终在生成提交就绪的意见函或监管机构回应草案之前：** 阅读 `~/.prismclaw/assets/plugins/legal-chineselaw/regulatory-legal/PRISMCLAW.md` 中的 `## 谁在使用这个`。如果角色是 **非律师**：

> 向监管机构提交评议意见有法律后果。这是公司立场的公开声明，它在规则制定或执法事项中记录在案，此处采取的立场约束公司，并可在后续程序中对其使用。你是否已与律师审查此内容？如果是，继续。如果否，这是带给他们的简报：
>
> - 规则制定或征询（监管机构、征求意见文号、截止日期）
> - 拟提交的意见说什么以及针对哪些部分
> - 未解决的问题和待议事项
> - 可能出什么问题（不利承认、与先前立场不一致、与行业协会评论的协调问题）
> - 向律师问什么（我们是否应该提交；我们是否应该通过行业协会联合提交；有哪些我们不应该采取的立场）
>
> 如果你需要找到律师：律师协会的引荐服务是最快的起点（上海市律师协会、北京市律师协会等地方律协；中华全国律师协会有指引）。

中国监管征求意见的主要渠道：
- 国家法律法规数据库（flk.npc.gov.cn）——人大立法征询
- 中国政府法制信息网（chinalaw.gov.cn）——行政法规和规章征询
- 各部委官网"征求意见"专栏——部门规章和规范性文件征询
- Regulations.gov 的中国对应页面不存在；需直接关注各部委官网

在没有明确"是"的情况下，不要在此关卡之后记录"filing"决定或产生提交就绪的草案。跟踪视图、截止提醒和"不提交/放弃"决定不需要关卡。

---

## 此技能不做什么

- 起草评议意见函。那是单独的律师任务。
- 做出提交决定。它跟踪决定；律师做出决定。
- 监控评议后活动。一旦提交决定，此台账的工作就完成了——通过 `/regulatory-legal:reg-feed-watcher` 跟进规则制定进展。

> `comment-decision` `gap_type` 语义、每次发送的通知确认规则和 comment-tracker.yaml schema 存在于 **gap-surfacer** 参考技能中——在进行实质性工作之前加载它。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
