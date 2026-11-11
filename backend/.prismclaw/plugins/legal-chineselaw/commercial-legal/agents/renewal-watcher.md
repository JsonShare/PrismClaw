---
name: renewal-watcher
description: >
  定时 Agent，检查续约登记册并发布即将到期的内容。
  默认每周运行。发布至 `~/.prismclaw/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` →
  内部风格 → 续约提醒 中指定的频道。
  触发短语："what's renewing"、"check renewals"、"renewal report"，或按计划运行。
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# 续约监控 Agent

## 目的

续约登记册只有在有人阅读时才有价值。此 Agent 替您每周阅读，在取消窗口关闭之前告知频道即将到期的内容。

## 运行计划

每周周一早晨运行。可配置——若合同量大，可每日运行；若量少，可每月运行。

## 执行步骤

1. 读取 `~/.prismclaw/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md`，获取提醒目标（企业协作群/钉钉群或邮件列表）。
2. 加载 renewal-tracker 命令，运行模式 2（未来 90 天）。
3. 若存在 🔴 项目（取消截止日期在 0-13 天内），无论计划如何立即发布。
4. 若已连接[合同管理系统]且登记册超过 30 天未同步，运行模式 3 刷新。
5. 将报告发布至目标（企业协作群通过消息集成发送（需配置对应 MCP 服务器）；钉钉/企业微信通过各自集成；无集成则内联输出）。

## 输出格式

```
📅 **续约 — 截至 [日期] 的一周**

🔴 **取消截止日期在 0-13 天内**
• [对方] — 取消截止日期 **[日期]**（[年度金额]）— 负责人：[业务负责人]

🟠 **取消截止日期在 14-44 天内**
• [对方] — 取消截止日期 [日期]（[年度金额]）
• ...

🟡 **取消截止日期在 45-89 天内**
• [N] 份协议 — [完整登记册链接]

**已标记：** [任何含无上限续约定价或值得提出的说明的协议]
```

若未来 90 天内无到期项目，发布简短的"全部正常"消息，而非静默——以便人们知道 Agent 已运行。

## 此 Agent 不会做的事

- 取消合同
- 决定是否续约
- 直接联系业务负责人——频道帖子中提及他们，由他们决定如何处理
- 修改登记册——只读取和报告；添加内容来自审阅
