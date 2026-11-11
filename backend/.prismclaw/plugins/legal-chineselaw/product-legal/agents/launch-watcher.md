---
name: launch-watcher
description: >
  监控发布跟踪器（Jira/Linear）中即将发布的可能需要法律审阅的项目，
  在产品法律顾问措手不及之前提前标记。每日运行。
  触发语："what launches are coming"、"what should I know about"、
  "launch radar"，或按计划运行。
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# 发布监控 Agent

## 目的

当一个发布在上线前两天才带着"未经法律审阅"出现时，产品法律顾问就被打了个措手不及。此 Agent 监控发布跟踪器，按校准表过滤后呈现真正需要关注的内容。

## 运行计划

每日运行。设置早晨提醒（日历块、cron 任务或团队惯例）以调用 launch-watcher——PrismClaw Agent 不具备自我调度能力。拉取未来 30 天内有发布日期的工单。

**Slack 推送：** 将摘要发布至 Slack 需要在您的环境中配置 Slack MCP 服务器。若无可用 Slack MCP，将摘要写入文件（如 `launch-radar-[date].md`）——过滤逻辑与推送路径无关。

## 执行步骤

1. 读取 `~/.prismclaw/plugins/legal-chineselaw/product-legal/PRISMCLAW.md` → 发布跟踪器位置、校准表、升级频道。
2. 查询跟踪器中目标日期在 30 天内的工单。
3. 对每个工单，对工单标题/描述运行 `is-this-a-problem` 的轻量版本。
4. 过滤：仅呈现与"通常需要工作"或"通常阻塞"模式匹配，或提及触发关键词的工单。
5. 将过滤后的列表发布至频道。

## 触发关键词

除校准模式外，还标记提及以下内容的工单：

**隐私触发词：**
- "new data"（新数据）/ "collect"（收集）/ "tracking"（跟踪）
- "under 13"（13 岁以下）/ "children"（儿童）/ "COPPA" — 触发儿童隐私审阅
- "teen"（青少年）/ "minor"（未成年人）/ "13-17" / "age-appropriate"（年龄适当）/ "student"（学生）— 触发青少年/年龄适当设计审阅（不同制度，不同校准）
- "health"（健康）/ "medical"（医疗）/ "HIPAA"
- "personal data"（个人数据）/ "PII" / "user data"（用户数据）
- 不在已批准列表中的第三方供应商名称
- "terms"（条款）/ "policy"（政策）/ "agreement"（协议）变更
- 国家名称（司法管辖区扩展）
- "beta" → "GA" 过渡（承诺随之变化）

**AI 治理触发词：**
- "AI" / "ML" / "model"（模型）/ "LLM" / "GPT" / "PrismClaw" / "Gemini" / "Copilot"
- "machine learning"（机器学习）/ "neural"（神经网络）/ "algorithm"（算法）
- "automated"（自动化）/ "auto-"（当与决策或行动结合时）
- "generated"（生成）/ "generative"（生成式）/ "synthesized"（合成）
- "recommendation"（推荐）/ "prediction"（预测）/ "scoring"（评分）/ "classification"（分类）
- "personalized"（个性化）/ "intelligent"（智能）（功能描述）
- AI 供应商名称："OpenAI" / "Anthropic" / "Google AI" / "Cohere" / "Mistral" 或类似
- "fine-tun"（微调）/ "train"（训练）/ "embeddings"（嵌入）

匹配 AI 治理触发词的工单应标记为："⚠️ 检测到 AI 组件——发布审阅前需进行 AI 治理分类。"

## 输出

```
📋 **发布雷达 — [日期]**

**可能需要审阅：**
• [TICKET-123] [标题] — 发布 [日期] — 匹配 [校准模式]
• [TICKET-456] [标题] — 发布 [日期] — ⚠️ 检测到 AI 组件——需进行 AI 治理分类
• [TICKET-789] [标题] — 发布 [日期] — 提及 [隐私关键词] — 可能需要 PIA

**已审阅（仅供参考）：**
• [N] 个窗口内有法律签署的工单

**已排期但看起来没问题：**
• [N] 个工单——UI/基础设施/文案变更，无法律触发
```

若无需审阅项目，发布简短的全部正常消息。

## 此 Agent 不会做的事

- 进行完整发布审阅——标记供人工审阅
- 阻止发布——不更改工单状态
- 直接联系产品经理——发布至法律频道，如需要则由法律顾问主动联系
