<!--
This file is a Chinese translation and adaptation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
改造为中国劳动法语境。
-->

# 劳动法律顾问 Plugin（中国劳动法）

内部劳动法律工作流：招聘合规审查、解除/终止审查、规章制度起草、员工手册更新、省市感知的工资工时问答。围绕在冷启动时学习的司法管辖区足迹构建——plugin 知道你的员工在哪些省市以及各地的差异。

**每个输出都是供律师审查的草稿——带引用、标记和把关——而不是法律结论。** Plugin 完成工作：阅读文档、应用你的规则、发现问题、起草备忘录。律师审查、验证并决定。引用按来源标记，以便你知道哪些来自研究工具，哪些需要检查。保密标记保守应用，因此不会意外放弃任何东西。重要行动——提交仲裁申请、发送解除通知、执行——在明确确认后进行。

## Who this is for

| 角色 | 主要工作流 |
|---|---|
| **劳动法律顾问** | 解除审查、规章制度起草、工资工时分析 |
| **HR 业务伙伴** | 招聘合规审查、员工手册问题、工资工时一线问答 |
| **合规总监/主任合伙人** | 高风险条款和裁员的升级接收方 |

## First run: cold-start

询问你在哪些省市和国家有员工，阅读你的员工手册和三份最近的解除备忘录，构建省市感知的升级表格。

```
/employment-legal:cold-start-interview
```

你的配置存储在 `~/.prismclaw/assets/plugins/legal-chineselaw/employment-legal/PRISMCLAW.md`，并在 plugin 更新后保留。

## Prerequisites

- **持久数据路径。** 休假登记、调查日志和扩展跟踪器写入 `~/.prismclaw/assets/plugins/legal-chineselaw/employment-legal/`，这是一个版本无关的路径，在 plugin 更新后保留。这些文件包含敏感的人员信息——确保该目录已备份并受到访问控制。
- **法律研究访问。** 此 plugin 中的 skill 有意不存储实体法律规则（最低工资标准、竞业限制补偿金比例、各地仲裁处理周期、省市高院指导意见等）。每个省市特定的规则在审查时进行研究和引用。确保会话可以访问你依赖的研究工具（元典 MCP、网页搜索、内部法律研究集成、团队参考材料）。
- **外部法律顾问。** 在没有外部法律顾问参与任何紧急情况或新省市扩张的情况下，不会产生任何省市特定或司法管辖区特定的法律建议。

## Skills

| Skill | 功能 |
|---|---|
| `/employment-legal:cold-start-interview` | 冷启动访谈——从员工手册+解除备忘录中学习省市足迹+升级规则 |
| `/employment-legal:hiring-review` | 录用函+竞业限制协议审查，省市合规检查 |
| `/employment-legal:termination-review` | 带有高风险标志检测的解除/终止审查 |
| `/employment-legal:policy-drafting [topic]` | 在需要时起草带有省市补充的规章制度 |
| `/employment-legal:wage-hour-qa [question]` | 工资工时或一般劳动法问答，省市感知 |
| `/employment-legal:worker-classification` | 对拟议的用工安排进行劳动关系认定，并标记错误分类差距 |
| `/employment-legal:expansion-kickoff [country]` | 启动新省市/国家的劳动法律合规规划 |
| `/employment-legal:expansion-update [country]` | 更新进行中的扩展跟踪器 |
| `/employment-legal:investigation-open` | 打开新的内部调查事项 |
| `/employment-legal:investigation-add` | 向正在进行的调查添加文档、访谈笔记或观察结果 |
| `/employment-legal:investigation-query` | 针对正在进行的调查日志提问 |
| `/employment-legal:investigation-memo` | 起草或更新保密调查备忘录 |
| `/employment-legal:investigation-summary` | 从调查备忘录中起草针对特定受众的摘要 |
| `/employment-legal:leave-tracker` | 检查未结休假的截止日期提醒和所需决定 |
| `/employment-legal:log-leave` | 将新休假添加到休假登记册 |
| `/employment-legal:matter-workspace` | 管理事项工作区（仅多客户私人执业）——新建、列表、切换、关闭、无 |
| **handbook-updates** | 将拟议变更与当前员工手册进行比较，标记省市补充影响 |

参考 skill `internal-investigation` 和 `international-expansion` 包含详细框架和模板——上面的按模式 skill 根据需要加载它们。

## Interactive skills vs. scheduled agents

上面的 skill 在你调用时运行——用于当你处理事项时。下面的 agent 按计划运行——用于当你不注意时发生变化的内容：

| Agent | 监视内容 | 默认节奏 |
|---|---|---|
| **leave-tracker** | 具有严格法律期限的未结休假——带薪年休假、医疗期、产假、陪产假、工伤停工留薪期等；在错过截止日期前触发决策点提醒 | 每周（周一） |

## How it learns

你在 `~/.prismclaw/assets/plugins/legal-chineselaw/employment-legal/PRISMCLAW.md` 的执业档案不是静态的——它会随着你使用 plugin 而改进。Skill 会告诉你何时输出使用了你应该调整的默认值。你可以重新运行设置、直接编辑文件，或者告诉 skill 记录新立场。

## Notes

- **省市感知是关键。** Plugin 知道上海和广东对竞业限制补偿金、经济补偿金封顶等有不同规定；各地高院对劳动争议的裁判尺度也存在差异。
- **解除审查不是与 HR 和经理对话的替代品。** 它是一个检查清单，可以捕捉每个人都忘记的事情——解除合法性三要素（事实依据+制度依据+程序依据）、工会通知程序、送达证据等。
- **工资工时问答引用规则但标记接近判断供人工审查。** 劳动关系认定有后果；派遣/外包/灵活用工的认定分歧需要人工判断。
- **仲裁前置。** 劳动争议必须先经劳动仲裁，仲裁裁决后15日内可向法院撤裁或起诉。仲裁时效一般为1年（劳动关系存续期间的工资争议除外）。
