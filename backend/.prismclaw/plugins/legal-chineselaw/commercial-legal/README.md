<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# Commercial Counsel Plugin

内部商事合同工作流：供应商协议审查、NDA 分流、SaaS 订阅审查、续约跟踪、升级路由和业务利益相关者摘要。围绕通过冷启动访谈编写的团队执业档案构建——plugin 学习*你的*剧本，而非通用剧本。

**每个输出都是供律师审阅的草稿——有引用、标记和关卡——而非法律结论。** Plugin 完成工作：阅读文档、应用你的剧本、发现问题、起草备忘录。律师审阅、核实并决定。引用按来源标记，因此你知道哪些来自研究工具，哪些需要检查。特权标记被保守应用，因此不会意外放弃。后果性行动——提交、发送、执行——在明确确认后设有关卡。

## 适用对象

| 角色 | 主要工作流 |
|---|---|
| **商事法律顾问** | 供应商协议审查、升级路由、利益相关者摘要 |
| **合同经理 / 律师助理** | NDA 分流、续约跟踪、首轮审查 |
| **采购** | 续约意识、作为接收方的利益相关者摘要 |
| **销售 / BD** | 在联系法务前自助进行 NDA 分流 |

## 首次运行：冷启动访谈

首次使用时，plugin 会对你进行访谈——十分钟，对话式——了解你的团队实际如何工作。它询问你的剧本立场、升级规则，以及当它出现在你办公桌上时让你叹息的事情。然后它要求 5-10 份最近签署的协议（越多越好，20 份能提供更清晰的模式），以便它能在实际中看到你的立场。

它将所学内容写入 `~/.prismclaw/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md`——一份关于你的团队的简单英文文档，每个其他 skill 在做任何事情之前都会阅读。你编辑文档，而非配置文件。

```
/commercial-legal:cold-start-interview
```

**剧本方面。** 在设置早期，你会被问及是构建**销售方**剧本（你销售产品/服务；你是供应商；通常是你的文件）、**采购方**剧本（你从供应商处购买；你是客户；通常是他们的文件），还是两者都要。答案几乎翻转每个剧本立场——责任限制、赔偿方向、终止权利、IP 所有权——因此这在前期很重要。如果你选择两者，设置首先构建销售方；之后运行 `/commercial-legal:cold-start-interview --side purchasing` 来构建另一个。你的配置并行持有两者，审查 skill 在阅读剧本前检查哪一方适用。

## 命令

| 命令 | 作用 |
|---|---|
| `/commercial-legal:cold-start-interview` | 运行（或重新运行）冷启动访谈 |
| `/commercial-legal:review [file]` | 根据你的剧本审查供应商协议、NDA 或 SaaS 订阅 |
| `/commercial-legal:renewal-tracker` | 未来 90 天内有什么续约，以及取消截止日期是什么时候 |
| `/commercial-legal:escalation-flagger` | 将问题路由给正确的审批者并起草请求 |
| `/commercial-legal:amendment-history [file(s)]` | 追溯合同在其基础协议和所有修正案中的变化 |
| `/commercial-legal:review-proposals` | 逐步查看来自监控 agent 的待处理剧本更新提案 |
| `/commercial-legal:matter-workspace` | 管理案件工作区（仅适用于多客户私人执业）——新建、列出、切换、关闭、无 |

## Skills

| Skill | 用途 |
|---|---|
| **cold-start-interview** | 首次运行访谈，写入 `~/.prismclaw/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` |
| **vendor-agreement-review** | 完整的剧本与合同偏差分析，带红线 |
| **nda-review** | 快速 GREEN/YELLOW/RED 分流，因此法务只需阅读需要的 NDA |
| **saas-msa-review** | 订阅特定覆盖：自动续约、价格升级、数据退出、SLA |
| **renewal-tracker** | 取消截止日期登记册，展示即将到来的内容 |
| **escalation-flagger** | 将问题与升级矩阵匹配，起草审批者请求 |
| **stakeholder-summary** | 法律审查的两段式业务翻译 |
| **amendment-history** | 总结基础协议及其修正案的变化，或追溯特定条款到其当前控制语言 |
| **matter-workspace** | 为多客户执业创建、列出、切换和关闭案件工作区；隔离每个客户/案件，因此上下文不会在它们之间泄漏 |

## 交互式命令与调度 agent

上面的命令在你调用时运行——用于当你处理案件时。下面的 agent 按计划运行——用于当你不注意时移动的内容：

| Agent | 监视内容 | 默认节奏 |
|---|---|---|
| **renewal-watcher** | 续约登记册——发布未来 90 天内即将到来的内容，对 0-13 天内的取消窗口进行红旗升级 | 每周（周一） |
| **deal-debrief** | 最近签署的协议的剧本偏差；提示律师在记忆新鲜时记录上下文 | 每周（周一） |
| **playbook-monitor** | 偏差日志——当条款在滚动 12 个月窗口内被覆盖 5 次以上时，提议剧本更新 | 数据触发（每次 deal-debrief 后） |

## 集成

**首先连接研究工具——引用护栏依赖于此。** 没有它，每个引用都被标记 `[verify]`，每个可交付成果上方的审阅者注释记录来源未经验证。Skills 无论哪种方式都有效；研究工具（CourtListener）只是将验证工作从你的盘子上移开。


附带在 `.mcp.json` 中配置的连接器：

- **Ironclad**——合同生命周期管理
- **DocuSign**——签名状态和信封跟踪
- **Slack**——搜索消息、阅读频道、查找讨论（通用存储桶）
- **Google Drive**——搜索、阅读和获取文档（通用存储桶）

连接 [CLM] 后：审查检查与同一对手方的先前协议、批量加载续约登记册、创建附有审查备忘录的记录。

连接 DocuSign 后：跟踪签名状态、按审批者顺序路由信封。

## 快速开始

### 1. 接受访谈

```
/commercial-legal:cold-start-interview
```

十分钟。准备好 5-10 份最近签署的协议以供分享（越多越好，20 份能提供更清晰的模式）。

你的配置存储在 `~/.prismclaw/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md`，并在 plugin 更新后保留。

### 2. 审查合同

```
/commercial-legal:review vendor-msa.pdf
```

输出：根据你的剧本的逐项偏差备忘录，带有特定的红线语言和指定的审批者。

### 3. 查看续约内容

```
/commercial-legal:renewal-tracker
```

输出：未来 90 天内有取消截止日期的所有内容，按紧急程度分组。

## 它如何学习

你在 `~/.prismclaw/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` 的执业档案不是静态的——它会随着你使用 plugin 而改进。Skills 会告诉你输出何时使用了你应该调整的默认值。当你的执业偏离你的剧本时，`playbook-monitor` agent 会提议更新。你可以重新运行设置、直接编辑文件，或告诉 skill 记录新立场。

## 文件结构

```
commercial-legal/
├── .plugin/plugin.json
├── .mcp.json
├── PRISMCLAW.md                    # 你的团队执业档案——由冷启动编写，由你编辑
├── README.md
├── agents/
│   ├── renewal-watcher.md
│   ├── deal-debrief.md
│   └── playbook-monitor.md
├── skills/
│   ├── cold-start-interview/
│   ├── review/
│   ├── review-proposals/
│   ├── vendor-agreement-review/
│   ├── nda-review/
│   ├── saas-msa-review/
│   ├── renewal-tracker/
│   │   └── references/renewal-register.yaml
│   ├── escalation-flagger/
│   ├── amendment-history/
│   ├── matter-workspace/
│   └── stakeholder-summary/
└── hooks/hooks.json
```

## 说明

- Plugin 假设你在大多数审查中是**客户**。当你是供应商时，标记它，审查会翻转剧本极性。
- NDA 分流专为非律师自助构建。GREEN 表示"路由到签名"。它不进行谈判。
- 续约跟踪仅知道通过此 plugin 审查或从 [CLM] 批量加载的合同。你安装前签署的合同需要一次性扫描。
