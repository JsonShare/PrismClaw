<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# PrismClaw for 法学院法律诊所（中国法律援助场景）

*通过 AI 赋能的法律临床教育，强化法律援助能力与诉诸司法机会。*

中国法学院法律诊所的 plugin——在督导律师指导下，法学生在高校法律援助机构为符合条件的受援人提供免费法律服务。劳动争议（农民工欠薪/工伤/劳动合同纠纷）、刑事辩护（《法律援助法》指定辩护）、婚姻家庭（离婚/抚养权/继承）、消费者维权、土地房屋（征地拆迁）、行政诉讼。

**每个输出都是供学生分析和律师审查的草稿——已标记、已把关、已记录。Plugin 为工作搭建支架；学生对其进行推理；监督律师审查。没有教授在设置时建立的监督模型，任何东西都不会离开诊所。**

## 这解决的问题

诊所在结构上受到容量限制。一名监督教授管理 5-10 名学生。每个学生在处理课程的同时处理少数案件。学生每学期轮换。行政任务——intake 撰写、初稿、研究起点、状态更新——消耗了本可以用于为客户提供建议的时间。结果：长长的等待名单、有限的案件量、放弃等待的人。

这个 plugin 削减了*围绕*法律工作的一切的时间成本，因此相同的学生和教授可以服务有意义更多的客户——并且学生将更多时间花在使临床教育有价值的分析和策略上。

**它加速了非教育部分。它保留了分析工作。** 这是设计原则。

## 谁使用它

| 角色 | 运行 | 获取 |
|---|---|---|
| **监督教授** | `/clinic-cold-start-interview`（一次）、`/supervisor-review-queue`（如果启用正式审查） | 诊所上下文已配置、学生工作已审查 |
| **学生** | `/ramp`（学期开始），然后是 `/client-intake`、`/draft`、`/memo`、`/research-start`、`/status`、`/client-letter` | 起点——永远不是最终工作产品 |

## Commands

| Command | 做什么 | 不做什么 |
|---|---|---|
| `/clinic-cold-start-interview` | **教授。** 一次性诊所配置：执业领域、司法管辖区、监督风格、手册/规则上传 | — |
| `/build-guide` | **教授。** 编写每个执业领域的指南：intake 问题、教学姿态（辅助/引导/教导）、审查把关、跨 plugin 检查 | 不替代 `/clinic-cold-start-interview`——这为一个执业领域调整技能 |
| `/ramp` | **学生。** 学期入职：诊所程序、工具演练、练习 | 不替代教授的 orientation |
| `/client-intake` | 结构化 intake：执业领域模板、跨领域问题发现、冲突标记、分类 | 不决定是否接受案件 |
| `/draft [doc]` | 初稿：庇护申请、驱逐答辩、保护令、要求信——司法管辖区感知 | 不产生最终工作产品 |
| `/memo` | IRAC 支架式案件分析，研究缺口已标记 | 不撰写分析——为其搭建支架 |
| `/research-start [issue]` | 研究路线图：法律法规、裁判规则领域、元典/北大法宝检索词 | **线索，不是权威引用**——学生核验所有内容 |
| `/status [audience]` | 案件状态摘要：面向客户、内部或法院就绪 | 不提交任何内容 |
| `/client-letter [type]` | 例行通信：预约确认、文档请求、简短更新 | 不做实质性建议——那是 `/status client` 或对话 |
| `/deadlines` | 追踪案件截止日期——添加、跨案件汇总并在 14/7/3/1 天发出警告、逾期标记 | 不从触发事件计算截止日期；学生根据当地规则计算 |
| `/client-comms-log [case]` | 仅追加的每个案件通信日志——电话、电子邮件、信件、亲自 | 不存储实质性法律分析；仅通信记录 |
| `/semester-handoff` | 学期结束离职——下一批的每个案件交接备忘录 | 不结案；学期结束时结案的案件获得最终 `/status internal` 备忘录并在交接文档中标记为已关闭 |
| `/supervisor-review-queue` | **教授，如果启用正式审查。** 什么在等待，批准/编辑/返回 | 可选——三种监督模型之一 |

## 伦理和保密前提

在将此 plugin 用于真实客户案件之前，请与你诊所的监督律师和学校的 IT/伦理办公室确认：

1. **你的 PrismClaw 帐户层级及其数据保留和训练策略。** Team、Enterprise、Work、Education 和个人帐户对保留、训练使用和子处理器处理有不同的保证。确认什么适用于诊所的帐户。
2. **你对 AI 辅助工作的受援人告知和披露做法**，根据《律师法》第三十八条（保密义务）、《律师职业道德基本准则》（全国律师协会）及诊所所在高校/法律援助机构的 AI 使用政策。决定诊所是否以及如何向受援人告知 AI 工具的使用；记录下来。
3. **保密材料将如何处理**——什么被粘贴到会话中、输出存储在哪里、谁有权访问、材料保留多长时间、学生轮换如何影响访问控制。
4. **你诊所的任何执业领域是否涉及更高的保密性**（刑事辩护、家庭暴力受害者保护、未成年人案件、移民/居留证件）需要额外的保障——并决定该 plugin 对于这些案件类型是否完全合适。

不要跳过这一步。冷启动访谈（`/legal-clinic:cold-start-interview`）在任何其他配置之前将这些决定捕获为第 0 部分。

## 置信度标记

此 plugin 中的技能内联标记置信度，因此学生和监督律师可以看到支架不确定的地方与它断言的地方。每个标记都是验证的提示——没有标记的内容是可信的。

- `[AI-ASSISTED DRAFT — requires student analysis and attorney review]`——应用于每个输出的基线标签。审查标签，不是面向客户内容的一部分；在任何内容出去之前剥离。
- `[UNCERTAIN: specific reason]`——skill 对此调用确实不确定（少数规则、有争议的问题、skill 不熟悉的司法管辖区）。用于 memo、intake、status、draft。
- `[VERIFY: claim — check source]`——陈述为可能但未验证的主张。学生在依赖之前必须确认——引用、当地规则格式、规则陈述。大量用于 research-start、draft、status、memo。
- `[RESEARCH NEEDED: ...]`——memo 支架标记，规则陈述是研究缺口，不是结论。学生运行 `/research-start` 并填充它。
- `[STUDENT ANALYSIS: ...]`——memo 支架标记，应用按设计为空。学生的推理填充它。
- `[STUDENT CONCLUSION: ...]`——memo 支架标记，结论按设计为空。
- `[FACT NEEDED: ...]`——draft 支架标记，案件笔记中缺少所需事实。学生获取事实；不猜测。
- `CHECK WITH [PROFESSOR] BEFORE SENDING` / `BEFORE FILING`——在"可配置标记"监督模式下应用于标记主题输出的监督标记标签。

信任标记多于没有标记。没有标记的陈述意味着 skill 是置信的——这并不意味着学生或律师跳过核验。《律师法》第三十八条和职业诚信要求无论如何都需要核验。

## 内置保障

每个 skill 的每个输出都包括：

- **AI 辅助标签**——需要学生分析和律师审查
- **置信度指标**——`[UNCERTAIN: ...]` 在确实不确定的地方，而不是猜测
- **验证提示**——在依赖输出之前要事实检查的具体事情
- **根据任务调整的伦理提醒**

这些旨在强化临床教育模型：学生进行思考，plugin 做围绕它的繁重工作。

**特别是研究输出：** `/research-start` 为学生提供线索和框架以验证和开发。它明确**不**提供法律引用作为权威。这既是伦理保障也是教学功能——学生仍然学习研究和使用判断；他们只是从更好的地方开始。

## 监督工作流（可配置）

plugin 是否包括正式审查工作流——学生 draft → 教授审查 → 已批准——是一个真正的开放问题。一些诊所想要硬把关；其他诊所发现它对他们的监督结构过于规范。

冷启动访谈要求教授选择：

1. **正式审查队列**——面向客户/法院的输出排队，教授批准，全部记录
2. **可配置标记，非正式审查**——某些触发条件将输出标记为"CHECK WITH PROFESSOR"，无队列机制
3. **轻触**——所有内容上的标准保障标签，教授通过现有诊所结构监督（案件 rounds，一对一会面）

稍后可通过编辑 `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/PRISMCLAW.md` 更改。你的配置存储在该版本无关的路径中，并在 plugin 更新后保留。

## 学期轮换：`/ramp` 解决方案

每个学期，诊所从头重建。新生需要数周来学习程序、工具、执业领域基础。`/ramp` 是交互式入职——它读取教授在设置时上传的诊所手册并教授它，在学生接触真实案件之前进行低风险练习（假 intake、练习 draft、研究路线图）。

`/ramp --card` 生成一页学生参考卡：命令、PrismClaw 可以和不可以帮助什么、验证习惯。第一天分发它。

## 伦理框架：《律师法》第三十八条 + 《律师职业道德基本准则》

此 plugin 运行所在的伦理框架。督导律师在使用生成式 AI 时，须确保：对所用技术有充分的认知能力、对受援人信息的保密义务、对学生输出的监督责任、在适当情况下告知受援人 AI 使用情况，以及在依赖任何输出前进行核验。上面的保障——标签、置信度指标、核验提示、研究输出的明确非权威性——都是为此监督模型构建的。

法律援助诊所的督导律师和临床教授是将法律服务延伸至弱势群体的重要力量。该 plugin 旨在按照他们希望的方式支持这一工作。

## Skills

| Skill | 目的 |
|---|---|
| **cold-start-interview** | 教授的一次性设置——执业领域、司法管辖区、监督风格、种子文档 |
| **build-guide** | 教授的每个执业领域指南——intake、教学姿态（辅助/引导/教导）、审查把关、跨 plugin 检查 |
| **ramp** | 学生学期入职——程序、工具、练习 |
| **client-intake** | 具有跨领域问题发现、冲突标记、分类的特定执业领域 intake |
| **draft** | 初稿生成——执业领域模板、司法管辖区感知、明确起点 |
| **memo** | IRAC 支架，研究缺口已标记——分析是学生的 |
| **research-start** | 研究路线图——线索不是权威，学生验证和开发 |
| **status** | 受众感知的案件摘要——客户 / 内部 / 法院 |
| **client-letter** | 来自模板的例行通信 |
| **supervisor-review-queue** | 可选的正式审查工作流——仅在教授选择时激活 |
| **deadlines** | 每个案件截止日期追踪、跨案件汇总、警告节奏、逾期标记 |
| **client-comms-log** | 仅追加的每个案件通信记录——电话、电子邮件、信件、亲自 |
| **semester-handoff** | 学期结束离职备忘录；`/ramp` 的镜像 |

*(两个已弃用的 skill——`form-generation`、`plain-language-letters`——分别重定向到 `/draft` 和 `/client-letter` + `/status client`。)*

## 连接器和引用验证

**首先连接研究工具——引用护栏依赖它。** 没有它，每个引用都被标记为 `[verify]`，每个交付物上方的审稿人注释记录来源未验证。该 plugin 两种方式都工作；当研究工具连接时，它只是为你做更多验证。

此 plugin 中的法律研究连接器不仅仅是数据源——它们是经验证引用和你必须检查的引用之间的区别。通过 **CourtListener**（自由法律项目的美国法院意见和 PACER 案件记录）或 **Descrybe**（基本法律搜索、引用查找、引述语言验证）检索的引用标记有其来源，可以追溯。来自模型知识或网络搜索的引用标记为 `[verify]` 或 `[verify-pinpoint]`，应在任何人依赖之前对照主要来源检查。该 plugin 对引用进行分层，因此你的验证时间用于重要的地方。

## 集成（开放问题）

随附 `.mcp.json` 中的通用连接器桶：

- **Slack**——搜索消息、读取频道、查找讨论
- **Google Drive**——搜索、读取和获取文档

Clio 被标记为可选的未来集成——120 多所法学院使用 Clio 进行案件管理。从文件上传开始；Clio 连接器将让 `/client-intake` 和 `/status` 直接提取案件数据。

用于客户保密的帐户层级（Team 与 Enterprise）是每个诊所 IT 和伦理审查的开放问题。Cowork 的桌面架构在本地处理数据。

## 它如何学习

你在 `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/PRISMCLAW.md` 的执业档案不是静态的——它会随着你使用 plugin 而改进。Skill 会告诉你何时输出使用了你应该调整的默认值。你可以重新运行设置，直接编辑文件，或告诉 skill 记录新立场。

## 文件结构

```
legal-clinic/
├── .plugin/plugin.json
├── .mcp.json                          # Clio 标记为可选
├── PRISMCLAW.md                          # 教授的诊所配置——由 cold-start 编写
├── README.md
├── deadlines.yaml                     # 操作截止日期分类账
├── skills/                            # 每个 skill 也是斜杠命令 /legal-clinic:<skill>
│   ├── cold-start-interview/          # 教授——一次性设置
│   ├── build-guide/                   # 教授——每个执业领域指南
│   ├── ramp/                          # 学生——学期入职
│   ├── client-intake/
│   │   └── references/intake-templates/
│   ├── draft/
│   ├── memo/
│   ├── research-start/
│   ├── status/
│   ├── client-letter/
│   ├── supervisor-review-queue/       # 教授，如果启用正式审查
│   │   └── references/review-queue.yaml
│   ├── deadlines/
│   ├── client-comms-log/
│   ├── semester-handoff/
│   ├── form-generation/               # 已弃用→/draft（仅参考）
│   └── plain-language-letters/        # 已弃用→/client-letter、/status client（仅参考）
├── handoffs/                          # 新——每学期交接备忘录
│   └── [YYYY-term]/
│       ├── _summary.md
│       └── [case-id].md
├── client-comms/                      # 新——每个案件通信日志
│   └── [case-id]/
│       └── log.md
└── hooks/hooks.json
```

## 测试和 QA

## 先决条件

一些功能引用外部集成（文档管理、发布追踪器、电子发现、案件管理、监管提要）。这些不被捆绑——如果你在你的环境中为其中之一拥有 MCP 服务器，相关功能将使用它。没有它，plugin 回退到文件上传和手动工作流。运行 `/legal-clinic:integrations` 以查看你环境中有什么可用。
