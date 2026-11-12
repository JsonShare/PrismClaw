<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# 公司法律顾问 Plugin

跨四个执业领域的内部公司法律顾问工作流：并购交易、董事会和公司秘书、上市公司治理以及实体管理。仅激活适用于你角色的模块。冷启动访谈是模块化的——它针对每个活跃领域提出针对性问题，并仅将相关部分写入你的执业档案。

**每个输出都是供律师审查的草稿——带引用、标记和把关——而不是法律结论。** Plugin 完成工作：阅读文档、应用你的剧本、查找问题、起草备忘录。律师审查、验证并决定。引用按来源标记，以便你知道哪些来自研究工具，哪些需要检查。特权标记保守应用，因此不会意外放弃任何东西。重要行动——提交、发送、执行——在明确确认后进行。

## 目标用户

| 角色 | 活跃模块 |
|---|---|
| **内部并购法律顾问** | 并购 |
| **公司/助理秘书** | 董事会和秘书 |
| **上市公司 GC** | 并购 + 上市公司 + 董事会和秘书 |
| **私营公司 GC** | 并购 + 董事会和秘书 + 实体管理 |
| **法律 ops / 独立 GC** | 任何适用的——混合和匹配 |

## 首次运行

```
/corporate-legal:cold-start-interview
```

引导你完成模块选择，然后针对每个活跃领域进行简短的针对性访谈。写入仅包含相关部分的模块化 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md`。你的配置存储在该路径，并在 plugin 更新后保留。

每笔交易设置（仅并购模块）：

```
/corporate-legal:cold-start-interview --new-deal
```

## 命令

| 命令 | 作用 |
|---|---|
| `/corporate-legal:cold-start-interview` | 模块化冷启动，或 `--new-deal` / `--module [m&a \| board \| public \| entities]` |
| `/corporate-legal:diligence-issue-extraction [folder]` | 阅读 VDR 文档，以内部格式提取问题 |
| `/corporate-legal:tabular-review` | 表格审查——每个文档一行，每个数据点一列，每个单元格都引用来源，Excel 输出 |
| `/corporate-legal:material-contract-schedule` | 从尽调发现中获取的重大合同披露时间表 |
| `/corporate-legal:closing-checklist` | 关闭检查清单——什么在阻塞，关键路径 |
| `/corporate-legal:written-consent` | 一致书面同意——先例匹配的草稿 + 签署人跟踪器 |
| `/corporate-legal:entity-compliance` | 实体合规跟踪器——初始化、报告、更新、审计、导出 |
| `/corporate-legal:integration-management` | 关闭后集成工作计划、同意跟踪器、合同转让、状态报告 |
| `/corporate-legal:matter-workspace` | 管理事项工作区（仅多客户私人执业）——新建、列表、切换、关闭、无 |

## 前提条件

多个功能引用 Slack、Google Drive、SharePoint、Box、Intralinks 或 Datasite 集成。这些需要在你的环境中配置的 MCP 服务器——它们**不与 plugin 捆绑**。没有它们，plugin 回退到文件输出（草稿写入本地而不是发布到频道，跟踪器文件写入磁盘而不是从连接的存储库读取）。

在仓库或用户级别的 `.mcp.json` 中配置 MCP 服务器。Skills 和 agents 将在运行时检测可用内容并调整行为。

## Skills

| Skill | 模块 | 目的 |
|---|---|---|
| **cold-start-interview** | 全部 | 模块化访谈——仅激活相关部分 |
| **diligence-issue-extraction** | 并购 | VDR 文档 → 按分类的内部格式问题 |
| **tabular-review** | 并购 | 根据类型化列模式审查文档集；带引用的单元格；`.xlsx` / `.csv` / markdown 输出；提供给 material-contract-schedule |
| **deal-team-summary** | 并购 | 分级简报：高管 / 交易主管 / 工作团队 |
| **material-contract-schedule** | 并购 | 根据购买协议定义的披露时间表 |
| **closing-checklist** | 并购 | 自我更新：从尽调中获取并通过时间表构建 |
| **ai-tool-handoff** | 并购 | Luminance/Kira 集成——批量提取 + QA 层 |
| **board-minutes** | 董事会和秘书 | 日历检测的会议 → 内部格式的草稿会议纪要 |
| **written-consent** | 董事会和秘书 | 一致书面同意，带来自同意存储库的先例搜索；针对重大一次性行动的范围警告 |
| **entity-compliance** | 实体管理 | 合规日历跟踪器（YAML）；按实体和州的申报截止日期；健康审计；CT Corp 报告摄入；CSV 导出 |
| **integration-management** | 并购 | 关闭后集成跟踪器；分阶段工作计划（第 1/30/90/180 天）；带有 PA 截止日期的所需同意跟踪器；大规模合同转让（存储库或手动列表）；每周状态报告 |
| **matter-workspace** | 为多客户执业创建、列表、切换和关闭事项工作区；隔离每个客户/事项，因此上下文不会在它们之间泄漏 |

*上市公司技能将在下一版本中推出。*

## 交互式命令 vs 计划 agents

上面的命令在你调用它们时运行——适用于当你正在处理事项时。下面的 agents 按计划运行——适用于当你不注意时发生变化的内容：

| Agent | 模块 | 它监视什么 | 默认节奏 |
|---|---|---|---|
| **dataroom-watcher** | 并购 | VDR 中的新文档上传；标记匹配高优先级类别的上传；运行关闭检查清单状态 | 每周 |

## 集成

**首先连接研究工具——引用护栏依赖它。** 没有研究工具，每个引用都被标记为 `[verify]`，并且每个可交付成果上方的审阅者备注记录来源未经验证。Skills 都可以工作；研究工具（CourtListener）只是将验证工作从你的盘子上移开。

附带：

- **Slack** — 搜索消息、阅读频道、查找讨论（通用存储桶）
- **Google Drive** — 搜索、阅读和获取文档（通用存储桶）
- **Box** — 数据室和文档管理

当合作伙伴 URL 可用时，可以将 Intralinks、Datasite 和其他 VDR 连接器添加到 `.mcp.json`。

## 它如何学习

你在 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` 中的执业档案不是静态的——它随着你使用 plugin 而改进。Skills 告诉你输出何时使用了你应该调整的默认值。你可以重新运行设置、直接编辑文件，或者告诉 skill 记录新立场。

## 并购说明

- 问题提取应用重要性阈值——如果阈值说按价值排名前 N，则不阅读每个文档。
- 买方和卖方均受支持。执业档案捕获适用于此交易的哪一方；skills 相应调整姿态。
- AI 工具交接（Luminance/Kira）是可选的。如果 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` 说没有工具，则所有提取都通过直接 skill 运行。
- 关闭检查清单从购买协议初始化，然后随着尽调显示所需同意而自我更新。
