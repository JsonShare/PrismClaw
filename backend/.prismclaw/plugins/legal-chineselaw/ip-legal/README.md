<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# IP Counsel Plugin

知识产权执业：商标、版权、专利、商业秘密和开源。起草和分类停止侵权函和 DMCA 删除通知（发送和响应），运行第一遍商标查询和自由实施分类，审查协议中的知识产权条款，跟踪注册和续展截止日期，并检查开源许可证合规。围绕由冷启动访谈编写的执业档案构建——plugin 学习*你的*执法姿态、投资组合和审批矩阵，而不是通用的。

**每个输出都是供律师审查的草稿——带引用、标记和把关——而不是法律结论。** plugin 完成工作：阅读文档、应用你的剧本、发现问题、起草备忘录。律师审查、验证并决定。引用按来源标记，以便你知道哪些来自研究工具，哪些需要检查。特权标记保守应用，因此不会意外放弃任何东西。重要行动——提交、发送、执行——在明确确认后进行。

## Who this is for

| 角色 | 主要工作流 |
|---|---|
| **企业内部 IP 法律顾问 / 法务** | 执法决定、条款审查、IP 投资组合监督、FTO 分类 |
| **专利代理师 / IP 律师助理** | 投资组合和续展跟踪、商标查询初筛、事项接收 |
| **品牌保护经理** | 停止侵权函、通知-删除请求（电商平台）、监控服务跟进 |
| **IP 检察官（商标 / 版权）** | 查询、条款审查、投资组合维护——*不是专利权利要求起草* |
| **律所 IP 律师 / 专代师** | 每个客户的事项工作区、查询和 FTO 分类、条款审查 |
| **管理 IP 投资组合的法律运营** | 注册跟踪器、续展截止日期（CNIPA 维权期）、开源合规检查 |

这个 plugin **不**起草专利权利要求。带有权利要求策略的专利申请是需要专利代理师或专利律师的专业技艺，不应外包给通用工具。这里的专利工作限于 FTO 分类（这个产品被其他人的专利阻止了吗？）、协议中的 IP 条款审查、投资组合续展跟踪和侵权分类。

## First run: the cold-start interview

首次使用时，plugin 对你进行访谈——十到十五分钟，对话式——以了解你的执业实际如何工作。它询问你的执业领域组合、你的司法管辖区足迹、你的执法姿态、你的审批矩阵和你的升级触发器。然后它询问你的投资组合列表、品牌指南、C&D 模板、执法剧本和 OSS 政策——无论你有什么——因此它可以提取而不是让你重新输入。

它将学到的内容写入 `~/.prismclaw/plugins/legal-chineselaw/ip-legal/PRISMCLAW.md`——一份关于你执业的简单英文文档，每个其他 skill 在做任何事情之前都会阅读它。你编辑文档，而不是配置文件。

```
/ip-legal:cold-start-interview
```

**执业领域组合。** 在设置早期，你会被问及你实际执业的 IP 领域——商标、专利、版权、商业秘密、开源或全部。plugin 跳过你不执业的领域的问题。你的配置可以并行持有多个领域，并且每个 skill 在从你粘贴的内容不明显时询问哪个领域适用。

**执法姿态。** 你会被问及在发送声明函时你在激进 / 稳健 / 保守谱上的位置，以及谁批准发送每种函件类型。该姿态翻转停止侵权函、删除通知和侵权分类 skill 的默认值。

## Commands

| 命令 | 功能 |
|---|---|
| `/ip-legal:cold-start-interview` | 运行（或重新运行）冷启动访谈 |
| `/ip-legal:cease-desist [context]` | 停止侵权函——发送，或分类收到的停止侵权函，使用你的 PRISMCLAW.md 要求的审批路由 |
| `/ip-legal:takedown [context]` | DMCA 删除通知——发送，响应收到的通知，或起草反通知 |
| `/ip-legal:clearance [mark]` | 第一遍商标查询——剔除 + 混淆分析，律师仍需签署 |
| `/ip-legal:fto-triage [product / claim scope]` | 自由实施分类——为律师审查标记阻止参考 |
| `/ip-legal:invention-intake [disclosure]` | 发明披露第一遍筛选——新颖性、非显而易见性、§101、关键日期、可检测性、战略价值 |
| `/ip-legal:infringement-triage [context]` | 侵权分类——这值得追求吗，以及如何 |
| `/ip-legal:ip-clause-review [file]` | 审查协议中的 IP 条款——转让、许可授予、IP 赔偿、OSS 声明 |
| `/ip-legal:oss-review [repo / file list]` | 开源许可证合规检查——左版义务、归属、许可证兼容性 |
| `/ip-legal:portfolio` | 注册和续展跟踪器——什么到期了，什么已提交，什么需要行动 |
| `/ip-legal:matter-workspace` | 管理事项工作区（仅多客户私人执业）——新建、列表、切换、关闭、无 |

## Skills

| Skill | 目的 |
|---|---|
| **cold-start-interview** | 第一次运行的访谈，编写 `~/.prismclaw/plugins/legal-chineselaw/ip-legal/PRISMCLAW.md` |
| **cease-desist** | 起草或分类 C&D；在发送前通过审批矩阵路由 |
| **takedown** | DMCA 通知、收到的删除通知的响应或反通知 |
| **clearance** | 拟议商标的剔除搜索 + 混淆可能性第一遍 |
| **fto-triage** | FTO 分类——标记律师在发布前应该阅读的参考 |
| **invention-intake** | 发明披露的第一遍专利性筛选——新颖性、非显而易见性、§101、关键日期、可检测性、战略价值 |
| **infringement-triage** | 鉴于明显侵权，决定：忽略 / 软函 / C&D / 提交 |
| **ip-clause-review** | 审查 MSA、SOW、许可、承包商协议中的 IP 条款 |
| **oss-review** | 根据 OSS 政策检查 repo 中的开源许可证 |
| **portfolio** | 注册登记册、续展截止日期、状态仪表板 |
| **matter-workspace** | 为多客户执业创建、列出、切换和关闭事项工作区；隔离每个客户/事项，因此上下文不会在它们之间泄露 |

## Interactive commands vs. scheduled agents

上面的命令在你调用时运行——用于当你处理事项时。下面的 agent 按计划运行——用于当你不注意时发生变化的内容：

| Agent | 监视内容 | 默认节奏 |
|---|---|---|
| **ip-renewal-watcher** | 投资组合登记册——计算未来 90 天到期的内容（续展、宣誓书、维护）并发布排名的截止日期报告 | 每周 |

## Connectors and citation verification

**首先连接研究工具——引用护栏依赖它。** 没有它，每个引用都被标记为 `[verify]`，并且每个可交付成果上方的审查者备注记录来源未经验证。plugin 可以以两种方式工作；当连接研究工具时，它只是为你做更多的验证工作。

此 plugin 中的法律研究连接器不仅仅是数据源——它们是经验证引用和你必须检查的引用之间的区别。通过 **CourtListener**（美国法院意见、PACER 案件记录、引用验证）或 **Descrybe**（实体法搜索、引用处理、引用语言验证）检索的引用标记有其来源并且可以追溯回来。来自模型知识或网页搜索的引用标记为 `[verify]` 或 `[verify-pinpoint]`，并且在任何人依赖它之前应该对照主要来源检查。plugin 对其引用进行分层，因此你的验证时间流向重要的地方。

## Integrations

附带在 `.mcp.json` 中配置的连接器：

- **元典（YuanDian）** ——中国法律法规检索、案例文书、企业信息核查、法律引用核验
- **PatSnap 智慧芽 / incoPat** ——专利和非专利文献检索、现有技术分析、权利要求分析（需独立接入）
- **北大法宝（PKULaw）/ 威科先行** ——中国裁判案例、法律法规、引用验证（需独立接入）
- **企业协作 / 钉钉 / 企业微信** ——消息推送、频道通知、工作区协同
- **Google Drive / 企业协作云文档** ——搜索、阅读和获取文档

连接专利检索后：FTO 和现有技术 skill 自动提取参考，而不是依赖用户提供的列表。

连接案例法工具后：查询和侵权分类 skill 核验引用案例是否仍为有效裁判，检查是否被再审或指导性案例推翻。

连接企业协作/钉钉后：投资组合导出、停止侵权函模板和执法日志更新通过你指向的频道路由。

## Quick start

### 1. 接受访谈

```
/ip-legal:cold-start-interview
```

十到十五分钟。准备好分享你的投资组合列表、品牌指南（如果有）、C&D 模板（如果有）和你的 OSS 政策（如果有）。

你的配置存储在 `~/.prismclaw/plugins/legal-chineselaw/ip-legal/PRISMCLAW.md`，并在 plugin 更新后保留。

### 2. 查询商标

```
/ip-legal:clearance "APEXLEAF"
```

输出：剔除命中列表、混淆可能性因素分析、律师审查的标志。不是去/不去决定。

### 3. 查看到期的内容

```
/ip-legal:portfolio
```

输出：未来 90 天内有续展、宣誓书或维护截止日期的注册，按紧急情况分组。

## File structure

```
ip-legal/
├── .plugin/plugin.json
├── .mcp.json
├── PRISMCLAW.md                    # 你的执业档案——由冷启动编写，由你编辑
├── README.md
├── agents/
│   └── ip-renewal-watcher.md
├── skills/
│   ├── cold-start-interview/
│   ├── cease-desist/
│   ├── takedown/
│   ├── clearance/
│   ├── fto-triage/
│   ├── invention-intake/
│   ├── infringement-triage/
│   ├── ip-clause-review/
│   ├── oss-review/
│   ├── portfolio/
│   └── matter-workspace/
└── hooks/hooks.json
```

## Configuration

plugin 从以下位置读取用户特定配置：

```
~/.prismclaw/plugins/legal-chineselaw/ip-legal/PRISMCLAW.md
```

此路径在 plugin 更新后保留。随 plugin 提供的 `PRISMCLAW.md` 是模板——每次升级都会替换它。冷启动访谈将你填充的版本写入上面的配置路径；从那时起，当某些内容发生变化时，直接编辑该文件。

## How it learns

你在 `~/.prismclaw/plugins/legal-chineselaw/ip-legal/PRISMCLAW.md` 中的执业档案不是静态的——它会随着你使用 plugin 而改进。Skill 会告诉你何时输出使用了你应该调整的默认值。`ip-renewal-watcher` agent 跟踪投资组合登记册并按你的节奏显示即将到来的续展截止日期。你可以重新运行设置、直接编辑文件或告诉 skill 记录新立场。

## Notes

- 每个 skill 首先阅读执业档案。如果它找到占位符，它会停止并告诉你运行 `/ip-legal:cold-start-interview`。没有通用回退——通用 IP 姿态比没有姿态更糟糕。
- 发送 C&D 会引发斗争。`/ip-legal:cease-desist` skill 不会自己发送任何内容；它会起草、显示审批矩阵条目并等待审批人。
- `/ip-legal:clearance` 和 `/ip-legal:fto-triage` 是**第一遍**分类。输出是给律师的研究包，而不是查询意见。skill 每次运行时都会这样说。
- `/ip-legal:oss-review` 标记许可证义务和不兼容性。它不会祝福商业使用决定——工程和法律共同决定。
- 专利权利要求起草有意不在范围内。这个 plugin 与专利申请专家配合得很好；它不能取代一个。
