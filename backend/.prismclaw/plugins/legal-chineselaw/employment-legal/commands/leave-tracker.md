---
name: leave-tracker
description: '检查未结束休假的截止日期警报和所需决策。仅提示需要行动的休假并解释原因——不是状态板。每周使用，或当律师需要知道哪些休假有即将到来的审批、证明或耗尽截止日期时使用。

  '
argument_hint: '[no arguments — works from HRIS or leave-register.yaml]'
category: 劳动用工
---

<!--
This file is a Chinese translation and adaptation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
改造为中国劳动法语境。
-->


# /leave-tracker

检查所有有硬性法律期限的未结束休假，仅提示需要决策或行动的项目。这不是状态板——而是告诉你需要做什么以及为什么。

## 说明

1. 加载 `leave-tracker` 代理并运行完整工作流。

2. 如果未连接 HRIS 且不存在 `~/.prismclaw/assets/plugins/legal-chineselaw/employment-legal/leave-register.yaml`，提示律师上传休假电子表格或使用 `/employment-legal:log-leave` 添加条目。

3. 仅对需要行动的休假发出警报。无需处理的休假每行汇总一条。

## 示例

```
/employment-legal:leave-tracker
```

每周运行一次——设置周一早晨提醒来调用 `/employment-legal:leave-tracker`。自动化调度需要单独的集成（日历提醒、cron 作业等）；PrismClaw 代理不会自动调度。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
