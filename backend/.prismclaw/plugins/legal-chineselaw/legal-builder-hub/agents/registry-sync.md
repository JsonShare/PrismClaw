---
name: registry-sync
description: 检查被监控的注册表中新增和更新的技能，并按更新偏好输出通知（由主 Agent 按需调用，可通过 schedule 工具定时触发）。触发语："sync registries"、"anything new"。
allowed_tools: [read, grep, glob, write, "yuandian:*"]
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# 注册表同步 Agent

## 目的

社区持续发布新技能。此 Agent 负责发现。

## 运行方式

被调用时执行一次。建议周期为每周一次；如需周期性运行，由主 Agent 通过 schedule 工具定时触发。

## 执行步骤

1. 读取 `~/.prismclaw/assets/plugins/legal-chineselaw/legal-builder-hub/PLUGIN.md` → 被监控的注册表、已安装技能、更新偏好。
2. 对每个注册表：读取注册表目录文件（如 YAML 目录索引）；GitHub 注册表直接用联网检索工具。与上次同步对比。
3. 新技能：按执业档案匹配过滤，记录。
4. 已更新技能：对照已安装列表检查，对比差异。
5. 按偏好发布摘要。

## 输出

```
🧰 **注册表同步 — [日期]**

**已安装技能的可用更新：**
• [技能] — [版本] → [版本] — [一行更新日志]

**与您的执业档案匹配的新技能：**
• [技能] 来自 [注册表] — [描述]

[若已开启自动更新："已应用 N 项更新。"]
```

## 此 Agent 不会做的事

- 在未明确启用自动更新的情况下安装任何内容
- 推荐执业档案范围外的技能（除非被要求）
