---
name: review-proposals
description: '审查并批准（或拒绝）playbook-monitor agent 的待定剧本更新提案， 并将批准的变更应用到执业档案。当 playbook-monitor
  agent 已提出提案， 或用户说"审查剧本提案"、"有什么剧本更新待处理"，或希望逐步处理 偏差驱动的剧本变更时使用。

  '
argument_hint: '[无需参数——从待处理提案文件工作]'
category: 商事合同
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /review-proposals

逐步处理 monitor agent 的待定剧本更新提案，并将批准的变更应用到 `~/.prismclaw/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md`。

## 说明

1. **加载 playbook-monitor agent** 并运行步骤 5（审查和批准流程）。

2. **如果没有提案文件存在**或为空：回复 *"没有待处理提案。剧本已是最新。"* 不要继续。

3. **逐一展示提案。** 对每个提案，显示完整的提案块并提供四个选项：接受、拒绝、编辑、推迟。

4. **对于接受或编辑：** 在写入之前显示对 `~/.prismclaw/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` 的精确差异。仅在律师明确确认后应用。

5. **对于拒绝或推迟：** 记录决定。不要修改 `~/.prismclaw/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md`。

6. **所有提案解决后：** 显示变更摘要，然后归档提案文件。

## 示例

```
/commercial-legal:review-proposals
```

```
/commercial-legal:review-proposals
（在 playbook-monitor 通知你后自动运行）
```


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
