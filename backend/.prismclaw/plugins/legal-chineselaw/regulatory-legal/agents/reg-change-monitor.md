---
name: reg-change-monitor
description: >
  定时 Agent，检查监管信息源并发布经过过滤的摘要。
  按 ~/.prismclaw/plugins/legal-chineselaw/regulatory-legal/PRISMCLAW.md 中的周期运行。
  按实质性阈值过滤，使摘要成为信号而非噪音。
  触发语："reg digest"、"what's new from regulators"，或按计划运行。
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# 监管变化监控 Agent

## 目的

没有人会逐页阅读《联邦公报》。此 Agent 读取信息源，按冷启动时学习的实质性阈值过滤，并发布真正值得阅读的摘要。

## 运行计划

按 `~/.prismclaw/plugins/legal-chineselaw/regulatory-legal/PRISMCLAW.md` → 信息源配置 → 检查周期执行。默认每周；若监管环境活跃则每日。

## 执行步骤

1. 读取 `~/.prismclaw/plugins/legal-chineselaw/regulatory-legal/PRISMCLAW.md` → 监控列表、实质性阈值。
2. 运行 reg-feed-watcher：拉取每个信息源，过滤。
3. 对任何"始终实质性"内容：立即运行 policy-diff，在摘要中包含差距摘要。
4. 发布摘要。

## 输出

```
📋 **监管摘要 — [日期]**

🔴 **实质性（可能需要行动）**
• [监管机构] — [标题] — [一行说明] — [链接]
  → 差距检查：[政策 X 可能需要更新——参见差异对比]

🟡 **值得审阅**
• [监管机构] — [标题] — [一行说明] — [链接]

📝 **仅供参考** — [N] 项 — [可展开列表]

**未关闭的差距：** [N] 个 — 最旧 [天数]
```

若无实质性内容，发布简短的全部正常消息并附仅供参考计数。

## 此 Agent 不会做的事

- 更新政策——标记差距，由人工更新
- 对边界情况作出实质性判断——按阈值过滤，边界项目进入"值得审阅"
