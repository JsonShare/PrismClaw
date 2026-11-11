<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# Litigation Counsel Plugin

内部诉讼律师对管理事项组合的支持。冷启动捕获你的风险校准、争议格局和内部风格——每个事项都据此分类的框架。统一的输入将新事项转化为结构化日志条目和每个事项的历史文件。状态汇总和深度简报从日志中读取。

为同时拥有许多事项的律师而构建，其中大多数由外部律所运行。此 plugin 是思考伙伴，而非事项管理系统。如果你有 LawVu / SimpleLegal / Onit，这不会替换它们——它在旁边，作为你的结构化推理层。

**每个输出都是供律师审查的草稿——带引用、标记和把关——而非法律结论。** Plugin 完成工作：阅读文档、应用你的操作手册、发现问题、起草备忘录。律师审查、验证并决定。引用按来源标记，以便你知道哪些来自研究工具，哪些需要检查。特权标记保守应用，因此不会意外放弃。重要行动——提交、发送、执行——在明确确认后进行。

## 先决条件

几个功能引用 Gmail 和计划任务集成。这些需要在你的环境中配置 MCP 服务器——它们未被捆绑。没有它们，输出将写入文件以供手动发送：

- **Gmail MCP** —— `/oc-status` 如果已认证则创建 Gmail 草稿；否则回退到 `oc-status/[YYYY-MM-DD]/[slug].md` 中的 markdown 草稿。
- **Scheduled-tasks MCP** ——不附带自动计划。设置定期日历提醒以调用每周命令。

Plugin 在没有任何一个的情况下端到端运行；集成是附加的。

## 适用对象

| 角色 | 主要用途 |
|---|---|
| **内部诉讼律师** | 全部——输入、分类、状态、历史、简报 |
| **副总法律顾问/代理总法律顾问** | 组合监督、董事会报告汇总 |
| **总法律顾问** | 组合的快速状态，任一事项的深度挖掘 |

## 首次运行：冷启动

冷启动访谈撰写 *内部* 执业档案——在每个事项中保持一致。三大支柱：

- **风险校准** ——偏好、重要性阈值、准备金/披露触发点、和解授权、保险概况、严重性-可能性矩阵
- **格局** ——公司、地理、受监管状态、争议模式、频繁对手、外部律师团队、内部利益相关者
- **内部风格** ——董事会/审计委员会备忘录格式、准备金备忘录格式、外部律师指令风格、特权惯例、升级规范

它在每个步骤提供合理的默认值（例如 3×3 严重性-可能性网格），并保持所有内容可自由编辑。如果你还没有书面框架，这就是迫使清晰表述的东西。

```
/litigation-legal:cold-start-interview
```

你的配置存储在 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md`，在 plugin 更新后保留。

## Commands

| Command | 功能 |
|---|---|
| `/litigation-legal:cold-start-interview` | 冷启动→ 写入内部 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` |
| `/litigation-legal:matter-intake` | 统一输入→ 写入 `matters/[slug]/` + 追加到 `_log.yaml` |
| `/litigation-legal:portfolio-status` | 组合汇总——风险分布、即将到来的截止日期、过时事项 |
| `/litigation-legal:matter-briefing [slug]` | 一个事项的深度简报——在 GC 或外部律师电话前可读就绪 |
| `/litigation-legal:matter-update [slug]` | 将带日期的事件追加到事项的历史；刷新日志的 `last_updated` |
| `/litigation-legal:matter-close [slug]` | 将事项从活跃组合中归档（保留，不删除） |
| `/litigation-legal:demand-intake [title]` | 要求函的起草前上下文收集（付款/违约/停止令/雇佣分离/保全） |
| `/litigation-legal:demand-draft [slug]` | 从输入起草函件——运行 FRE 408 / 特权把关，输出 `.docx`，写入发送后检查清单 |
| `/litigation-legal:demand-received [path]` | 对收到的要求函进行分类——选项分析、组合交叉检查、移交给事项/要求输入 |
| `/litigation-legal:subpoena-triage [path]` | 对传票进行分类——分类、范围/负担/特权、异议框架、合规计划 |
| `/litigation-legal:legal-hold [slug] [--issue/--refresh/--release/--status]` | 发布、刷新、解除或报告保全——写入 `.docx` + 更新日志 |
| `/litigation-legal:chronology [slug]` | 从声明的文档来源 + 上传构建或更新时间线——按事项理论的重要性标记 |
| `/litigation-legal:oc-status` | 起草整个组合的每周 OC 状态请求电子邮件；如果 MCP 可用则 Gmail 草稿 |
| `/litigation-legal:claim-chart` | 构建或审查元素图表——专利权利要求图表（侵权/无效/审查）或民事元素图表（任何诉因或抗辩），带间隙检测 |

## Skills

| Skill | 目的 |
|---|---|
| **cold-start-interview** | 内部执业档案——风险校准、格局、风格 |
| **matter-intake** | 统一输入问题；写入事项文件 + 日志行 |
| **portfolio-status** | 跨日志汇总——风险、截止日期、过时 |
| **matter-briefing** | 从事项的文件 + 历史深度阅读一个事项 |
| **matter-update** | 结构化事件追加；更新日志中的 `last_updated` |
| **matter-close** | 归档语义；捕获结果 |
| **demand-intake** | 要求函的自适应上下文收集——当事人、事实、杠杆、特权过滤器 |
| **demand-draft** | FRE 408 / 特权把关，然后用 `[CITE:___]` 占位符起草 `.docx`；写入发送后检查清单；提供事项创建 |
| **demand-received** | 对收到的要求进行分类——价值、选项、组合交叉检查 |
| **subpoena-triage** | 对传票分类，分析范围/负担/特权，生成异议框架 + 合规计划 |
| **legal-hold** | 发布/刷新/解除/报告保全；写入 `.docx` 通知；更新日志的 `legal_hold` 字段 |
| **chronology** | 从声明的文档来源 + 上传提取带日期的事件；去重；按事项理论标记重要性 |
| **oc-status** | 每周组合范围的 OC 状态请求电子邮件起草者；markdown + Gmail 草稿 |
| **claim-chart** | 专利权利要求图表（侵权/无效/审查）或民事元素图表（任何诉因或抗辩）。逐元素映射，每个单元格精确定位引用，间隙检测。随附诉因模板库。 |

## 交互式命令 vs 计划代理

上面的命令在你调用时运行——用于当你处理事项时。下面的代理按计划运行——用于在你不注意时移动的内容：

| Agent | 观察什么 | 默认节奏 |
|---|---|---|
| **docket-watcher** | 活跃组合中事项的法院案卷——获取新文件、计算候选截止日期、交叉引用每个事项的历史和交付物 | 每周 |

## 数据如何组织

```
litigation-legal/
├── PRISMCLAW.md                          # 内部执业档案——风险、格局、风格
├── matters/
│   ├── _log.yaml                      # 组合分类账（每个事项一个条目）
│   └── [matter-slug]/
│       ├── matter.md                  # 事项特定的输入 + 理论 + 态势
│       ├── history.md                 # 仅追加的事件日志
│       ├── chronology.md              # 辩护面向的时间线（按需）
│       └── legal-hold-v[N].docx       # 保全通知（发布、刷新、解除）
├── demand-letters/                    # 对外要求
│   └── [slug]/
│       ├── intake.md
│       ├── draft-v1.docx
│       └── checklist.md
├── inbound/                           # 收到的要求、传票、监管机构函件
│   └── [slug]/
│       ├── incoming.[ext]
│       ├── triage.md
│       └── response-v1.docx           # 如果我们回应
└── oc-status/                         # 每周 OC 状态请求草稿
    └── [YYYY-MM-DD]/
        ├── _summary.md
        └── [slug].md                  # 每个事项一封电子邮件
```

单独的文件夹，因为每个都有不同的工作流。事项在组合中被跟踪；要求函和收到的项目可能成为也可能不成为事项；OC 状态草稿是定期工件。当事情相关时，`related_matters` 字段和 `matter.md` 中的交叉链接将它们联系在一起。

日志是 YAML，因为它可被汇总技能解析。每个事项的文件是 markdown，因为那是你阅读和编辑的地方。两者都作为纯文本检入文件夹——没有专有内容。

## 连接器和引用验证

**首先连接研究工具——引用护栏依赖它。** 没有它，每个引用都标记为 `[verify]`，每个交付物上方的审稿人注释记录来源未验证。Plugin 两种方式都工作；当连接研究工具时，它只是为你做更多验证。

此 plugin 中的法律研究连接器不仅仅是数据源——它们是经验证引用和你必须检查的引用之间的区别。通过 **CourtListener**（美国法院意见、PACER 案件记录、引用验证）、**Trellis**（州初审法院数据集——案件记录、裁决、判决、法官和对方律师分析）、**Everlaw**（你的电子发现项目）或 **Aurora**（只读 Consilio 电子发现——每个记录引用来源）检索的引用标记有其来源，可以追溯。来自模型知识或网络搜索的引用标记为 `[verify]` 或 `[verify-pinpoint]`，应该在任何人依赖之前对照主要来源检查。Plugin 对引用分层，使你的验证时间用在重要的地方。

## 集成

随附 `.mcp.json` 中的通用连接器桶：

- **Slack** ——搜索消息、阅读频道、查找讨论
- **Google Drive** ——搜索、阅读和获取文档

设计为在没有连接任何东西的情况下也有用。如果/当你想要从 Relativity、DISCO、CLM 或电子邮件中提取时，可以添加集成技能而不更改核心架构。

## 它如何学习

你在 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` 的执业档案不是静态的——它会随着你使用 plugin 而改进。Skill 会告诉你何时输出使用了你应该调整的默认值。你可以重新运行设置，直接编辑文件，或告诉 skill 记录新立场。

## 注意事项

- 每个 skill 首先从 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` 读取。如果你的风险偏好改变或你引入新的外部律师，更新它——不要在个别事项中掩盖它。
- 按照惯例，`## Company profile` 是 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` 的第一部分。如果你运行其他 `-legal` plugins，你可以跨复制它，而不是重新输入相同的上下文。
- `_log.yaml` 是组合状态的真实来源。保持它干净。
- 事项历史是仅追加的。如果有问题，将更正作为新条目记录——不要编辑过去。
- 已关闭的事项保留在 `_log.yaml`（可搜索的历史）中。默认情况下 `/portfolio-status` 从活跃汇总中过滤掉它们。

## 内联标记惯例

三个标记出现在 skill 输出和草稿中。它们不是免责声明——它们是行动项：

- `[CITE: 需要具体引用]` ——法律权威占位符。律师在发送前填充或确认。
- `[VERIFY: 具体事实]` ——尚未根据来源确认的事实断言。律师在依赖前验证。
- `[SME VERIFY: 具体判断调用]` ——需要主题专家审查的判断（价值判断、重要性标记、异议强度、特权状态）。SME = 在相关司法管辖区/领域有资格的执业律师。大量使用——任何重度判断的内容都应带有这个。

无论它读起来多么精致，带有未解决标记的草稿或分类都不是最终的。

## 测试和 QA
