---
name: dataroom-watcher
description: >
  监控 VDR 中新上传的文件，并按计划发布关闭检查清单状态。
  标记与高优先级类别匹配的新上传文件。
  触发语："what's new in the data room"、"VDR updates"，或按计划运行。
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# 数据室监控 Agent

## 目的

VDR 往往在电话会议前一晚 11 点才更新。此 Agent 监控新上传内容并告知团队新增文件。同时按配置周期运行关闭检查清单状态。

## 运行计划

尽职调查活跃期间每日运行。检查清单状态按 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` → 交易团队简报周期执行。

## 集成说明

向企业协作/钉钉发布需要在您的环境中配置相应 MCP 服务器（lark MCP）。此插件不内置。若未配置即时通讯 MCP，将数据室更新和检查清单状态写入 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/deals/[code]/updates/[date].md` 并通知用户——不得静默失败。

VDR 工具（Box、Intralinks、Datasite）同为外部 MCP——若均未连接，提示用户提供 VDR 导出文件，或请其手动更新 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/deals/[code]/vdr-inventory.md`。

## 执行步骤

1. 查询 VDR，获取上次运行后新增的文件。
2. 将新文件映射至请求清单类别。
3. 标记高优先级类别中的任何文件（重大合同、诉讼、知识产权）。
4. 若为简报日，运行关闭检查清单模式 4。
5. 发布至交易频道。

## 输出

```
📁 **VDR 更新 — [交易代码] — [日期]**

**自 [上次运行] 起新增：** [N] 份文件

**优先级类别：**
• /02-Contracts/Customer/ — [N] 份新增（[文件名]）
• /05-Litigation/ — [N] 份新增 ⚠️

**其他：** [N] 份文件，位于 [类别]

[若为简报日：按模式 4 显示关闭检查清单状态]
```

## 此 Agent 不会做的事

- 读取新文件——标记供人工审阅
- 更新关闭检查清单——报告状态，由人工更新
