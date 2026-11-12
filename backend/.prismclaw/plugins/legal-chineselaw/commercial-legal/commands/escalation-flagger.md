---
name: escalation-flagger
description: 根据 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md`
  中的升级矩阵，将合同问题路由给正确的审批人，并起草请求。当用户说"谁需要审批这个"、"升级这个"、"这需要总法律顾问签署吗"、"为此路由审批"时使用，或当另一个技能发现超出审阅者权限的问题时使用。
argument_hint: '[描述问题，或参考审查备忘录]'
category: 商事合同
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /escalation-flagger

根据 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` 升级矩阵，为合同问题指定审批人并起草消息，这样您就不会在下午 5 点写"嘿有时间吗"。

## 指示

1. **加载 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md`** → 升级部分。如果缺失，说明——执业档案需要编辑。

2. **表征问题：** 美元阈值 / 条款偏离 / 自动触发器 / 业务决策。

3. **匹配到矩阵，指定审批人。** 要具体——人员或角色，而不是"法律领导层"。

4. **根据以下模板起草请求：** 合同说什么、剧本说什么、选项与建议、决策日期。

5. **不要发送。** 起草它、展示它、让律师发送。

## 示例

```
/commercial-legal:escalation-flagger
The Acme MSA has uncapped liability — who approves and what do I say?
（Acme MSA 有无限责任——谁批准，我该说什么？）
```

```
/commercial-legal:escalation-flagger
Reference: acme-review-memo.md
Issue: §8.2 indemnity carveouts
（参考：acme-review-memo.md
问题：§8.2 赔偿例外）
```

---

## 事项上下文

**事项上下文。** 检查执业级 PRISMCLAW.md 中的"## Matter workspaces"。如果 `Enabled` 是 `✗`（内部用户的默认值），跳过本段其余部分——skills 使用执业级上下文，事项机制不可见。如果启用且没有活跃事项，询问："这是哪个事项的？运行 `/commercial-legal:matter-workspace switch <slug>` 或说 `practice-level`。" 为事项特定上下文和覆盖加载活跃事项的 `matter.md`。将输出写入到 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/matters/<matter-slug>/` 的事项文件夹。永远不要阅读另一个事项的文件，除非 `Cross-matter context` 是 `on`。

---

## 目的

每个合同团队都有升级矩阵，无论是否书面。此技能读取书面矩阵（在 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` 中），将合同问题与其匹配，指定审批人，并起草请求，以便律师不会在下午 5 点写"嘿有时间吗"消息。

## 加载矩阵

**哪一方？** 在匹配到矩阵之前，确定公司在正在升级问题的合同中处于哪一方。通常很明显：如果对手方是提供货物或服务的供应商/供应商，您是采购方。如果对手方是购买您的产品/服务的客户，您是销售方。如果不明显，询问。阅读匹配的剧本部分（`### Sales-side playbook` 或 `### Purchasing-side playbook`）以评估条款是否在备用范围内或触发自动升级——在一方可行的条款在另一方可能是硬性拒绝。在起草的请求中注明哪一方，以便审批人知道应用了哪个剧本。

阅读 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` → `## Escalation`。如果它缺失或模糊，说明——冷启动访谈应该已经捕获了这一点，如果没有，执业档案需要编辑。

预期结构：

| 可审批 | 阈值 | 升级到 | 通过 |
|---|---|---|---|
| 法务专员/初级律师 | 标准条款，<¥50万 | 经办律师/法务经理 | 企业协作/钉钉 |
| 经办律师/法务经理 | 非标准但在备用范围内，<¥500万 | 合伙人/法务总监 | 企业协作/钉钉/邮件 |
| 合伙人/法务总监 | 其他所有 | 主任/总法律顾问/管委会 | 当面会议 |

加上 **自动升级触发器**——无论美元价值如何都会升级的东西。通常：无限责任、IP 转让、"永不接受"列表上的任何内容。

## 工作流

### 步骤 1：表征问题

正在升级什么？

- **美元阈值：** 合同价值超过某人的审批权限
- **条款偏离：** 条款超出剧本备用范围——需要更高级别的人决定是否接受
- **自动触发器：** 存在始终升级的项目之一
- **业务决策：** 不是法律判断——需要业务所有者，而不是法律领导层

不要升级实际上可以的事情。如果条款在 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` 的备用范围内，则不需要升级。

### 步骤 2：匹配到矩阵

```
问题是自动触发器吗？
  → 是：升级到 [为该触发器指定的人员]
  → 否：继续

合同价值是否超过审阅者的阈值？
  → 是：升级到在该美元级别拥有权限的任何人
  → 否：继续

条款偏离是否超出所有记录的备用范围？
  → 是：升级到任何可以批准非标准条款的人
  → 否：审阅者可以批准——无需升级
```

### 步骤 3：指定审批人

要具体。不是"升级到法律领导层"——指定 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` 中的人员或角色。如果矩阵没有为此情况指定任何人，说明："升级矩阵未涵盖 [情况]。建议询问 [合伙人/法务总监姓名] 谁负责处理这个。"

### 步骤 4：起草请求

审批人应该能够仅从消息中决定——无需"让我拉起合同"。

```markdown
**升级到：** [姓名]
**通过：** [企业协作群/钉钉/邮件/当面会议——根据执业档案]
**紧急度：** [如有截止期限]

---

嘿 [姓名] —

需要您对 [对手方] [协议类型] 的决定。[一句话交易上下文。]

**问题：** [通俗英语，一段话。他们想要什么、为什么它超出我们的标准、实际风险是什么。]

**合同说什么：**
> "[exact quote]"（[确切引用]）

**我们的剧本说什么：** [来自 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` 的引用]

**选项：**
1. **接受**——[一行关于为什么这可能可以的说明]
2. **反驳：** "[proposed counter-language]"（[提议的反向语言]）——[一行关于可能的对手方反应]
3. **放弃**——[一行关于在业务上下文中这是否现实的说明]

**我的建议：** [哪个选项及原因，简短]

**需要在之前决定：** [日期，如有截止期限]

[完整审查备忘录的链接]
```

### 步骤 5：记录升级

如果此团队使用工单系统或 [合同管理系统] 审批工作流，记录它。如果没有，在审查备忘录中注明升级已发送、发给谁以及何时发送。下一个阅读备忘录的人应该看到状态。

## 校准：有疑问时，升级并附注

不必要升级的成本是审批人时间的 ~30 秒——他们阅读，说"可以，继续"，记录显示他们已看到。错过升级的成本是签署未经批准的条款，这是单向门。成本不对称。**有疑问时，升级。**

升级的校准标准存在于 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` 中，而不是在此 skill 中。检查剧本的明确立场、其备用范围及其"无论美元价值如何都自动升级"列表：

- **明显在备用范围内：** 无需升级。
- **明显超出范围，或在自动升级列表上：** 升级。
- **不确定——条款模糊、新颖，或者可以说在范围内但论点是牵强的：** 无论如何升级，并明确注明不确定性。草稿标记审批人需要决定的具体问题，以及为什么 skill 无法自信地将其放在备用范围内。审批人缩小范围；skill 不缩小。

不要因为过度升级可能会让审批人养成略读习惯而抑制升级。这是审批人体验问题，律师通过调整剧本中的阈值来解决，而不是 skill 通过对其不确定的条款做出自己的主观判断来解决的问题。

如果出现剧本未解决的条款，不要猜测阈值——询问审阅律师此类问题是否应该升级，并提供将答案记录在 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` 中，以便未来审查一致。

## 此技能不做什么

- 它不批准任何东西。它路由。
- 它不在选项之间做决定。草稿包括建议，但审批人决定。
- 它不发送升级消息——它起草它。律师在阅读后发送。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
