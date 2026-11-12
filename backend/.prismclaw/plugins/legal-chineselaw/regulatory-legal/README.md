<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# Regulatory Counsel Plugin

监视监管源，对比新规则与你的政策库，显示差距。了解你的重要性阈值，因此它不会对每个专员的演讲发出警报。为 Federal Register API、直接监管源和 CourtListener 连接。

**每个输出都是供律师审查的草稿——带引用、标记和把关——而不是法律结论。** plugin 完成工作：阅读文档、应用你的剧本、发现问题、起草备忘录。律师审查、验证并决定。引用按来源标记，以便你知道哪些来自研究工具，哪些需要检查。特权标记保守应用，因此不会意外放弃任何东西。重要行动——提交、发送、执行——在明确确认后进行。

## Who this is for

| 角色 | 主要工作流 |
|---|---|
| **合规 / 监管法律顾问** | 观察列表维护、差距分类、政策更新协调 |
| **隐私 / 产品法律顾问** | 接收与其领域相关的过滤警报 |
| **GC** | 带有截止日期的重要差距的升级接收方 |

## First run: cold-start

询问你监视哪些监管机构，连接你的政策文档文件夹，了解"重要"对你意味着什么。构建观察列表并索引你的政策库。

```
/regulatory-legal:cold-start-interview
```

## Skills

| Skill | 功能 |
|---|---|
| `/regulatory-legal:cold-start-interview` | 冷启动：观察列表 + 政策索引 + 重要性阈值 |
| `/regulatory-legal:reg-feed-watcher` | 现在检查源，报告新内容 |
| `/regulatory-legal:policy-diff [reg]` | 对比特定监管变更与政策库 |
| `/regulatory-legal:gaps` | 开放差距跟踪器——已标记但尚未关闭的内容 |
| `/regulatory-legal:comments` | 审查开放 NPRM 意见征询期、记录决定、跟踪截止日期 |
| `/regulatory-legal:policy-redraft` | 关闭差距的拟议带标记政策重写——供内部审查的初稿，而不是对源文档的直接编辑 |
| `/regulatory-legal:matter-workspace` | 管理事项工作区（仅多客户私人执业）——新建、列表、切换、关闭、无 |
| **gap-surfacer** *(参考)* | 由 `/gaps` 和 `/comments` 加载的共享差距和意见跟踪器框架 |

## Interactive skills vs. scheduled agents

上面的 skill 在你调用时运行——用于当你处理事项时。下面的 agent 按计划运行——用于当你不注意时发生变化的内容：

| Agent | 监视内容 | 默认节奏 |
|---|---|---|
| **reg-change-monitor** | 监管源——根据冷启动学到的重要性阈值过滤，并发布有信号而非噪音的摘要 | 每周（如果监管环境活跃则每天） |

## Connectors and citation verification

**首先连接研究工具——引用护栏依赖它。** 没有它，每个引用都被标记为 `[verify]`，并且每个可交付成果上方的审查者备注记录来源未经验证。plugin 可以以两种方式工作；当连接研究工具时，它只是为你做更多的验证工作。

此 plugin 中的法律研究连接器不仅仅是数据源——它们是经验证引用和你必须检查的引用之间的区别。通过连接的研究工具检索的引用标记有其来源并且可以追溯回来。来自模型知识或网页搜索的引用标记为 `[verify]` 或 `[verify-pinpoint]`，并且在任何人依赖它之前应该对照主要来源检查。plugin 对其引用进行分层，因此你的验证时间流向重要的地方。

## Integrations

附带在 `.mcp.json` 中的通用连接器：

- **Slack** ——搜索消息、阅读频道、查找讨论
- **Google Drive** ——搜索、阅读和获取文档

当合作伙伴 URL 可用时，可以添加额外的监管源连接器。直接监管机构 RSS/电子邮件作为回退。

## Prerequisites

所有者通知（差距分配、截止日期提醒、NPRM 警报）需要你的环境中的 Slack MCP 服务器。没有它，差距跟踪器和意见跟踪器仍然有效——通知只是不会发布，并且 skill 会在状态报告中标记未把关的项目。

## How it learns

你在 `~/.prismclaw/assets/plugins/legal-chineselaw/regulatory-legal/PRISMCLAW.md` 中的执业档案不是静态的——它会随着你使用 plugin 而改进。Skill 会告诉你何时输出使用了你应该调整的默认值。`reg-change-monitor` agent 监视监管源并针对你的政策库标记变更。你可以重新运行设置、直接编辑文件或告诉 skill 记录新立场。

## Notes

- 重要性过滤是价值。所有内容"技术上是监管变更"——plugin 了解这里实际重要的内容。
- 政策对比与索引政策进行比较。如果政策库未连接，对比会针对你粘贴的内容运行。
- 这是 privacy-legal 的 `reg-gap-analysis` 的自动化版本。配对使用：这个监视，那个深入研究。

## Configuration

你的配置存储在 `~/.prismclaw/assets/plugins/legal-chineselaw/regulatory-legal/PRISMCLAW.md`，并在 plugin 更新后保留——你只需运行一次设置。
