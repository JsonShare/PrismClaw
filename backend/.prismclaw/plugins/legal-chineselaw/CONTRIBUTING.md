<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# 为 PrismClaw for Legal 做贡献

给任何在此仓库中编写或编辑 plugin 的人的说明。保持简短——关乎输出质量最重要的设计原则，而非风格指南。

## 在您第一个 PR 之前

签署 CLA。第一次打开 pull request 时，CLA Assistant 机器人会评论一个 [CLA](CLA.md) 链接并要求您确认。回复 `I have read the CLA Document and I hereby sign the CLA`，检查就会通过。您只需做一次。

## 设计原则：SKILL.md 编码正确行为；PRISMCLAW.md 护栏是网

本仓库中的每个 plugin 都附带两层指令：

1. **`<plugin>/skills/<skill>/SKILL.md`**——这个特定 skill 做什么，一步步来。狭窄、任务特定的支架。
2. **`<plugin>/PRISMCLAW.md`**——共享护栏和执业档案。"支架，而非眼罩"、来源标记纪律、"核实用户陈述的法律事实"、前提验证、目的地检查、跨 skill 严重性底线、飞行前引用横幅。广阔的 plugin 级安全网。

**如果 skill 的正确输出依赖于 PRISMCLAW.md 护栏捕获 SKILL.md 本会犯的错误，那这是设计气味。** SKILL.md 应直接告诉模型做什么；护栏应捕获 SKILL.md 遗漏的。每次护栏不得不救援 skill 时，我们都在依赖护栏持续触发——而在糟糕的运行、较弱的模型、更简洁的提示，或只读取 skill 文本的未来编辑时，救援不会发生。

**经验法则：如果 QA 测试仅因护栏触发而通过，直接把行为添加到 SKILL.md 中。** 护栏保留（双保险），但 skill 现在自己携带所需知识。

此规则实际应用的示例：

- 外观设计专利问题不应仅因为"支架，而非眼罩"让模型覆盖实用新型专利工作流而通过侵权分级。skill 应在 D 前缀本身上分支并路由到普通观察者测试。
- 落在周日的续约取消截止日不应仅因为用户想到询问工作日而正确落在您日历上。登记册架构和模式 2 输出应自己携带营业日回滚。
- FLSA 欠薪计算不应仅因为模型刚好记得 §207(e) 而正确获得常规费率公式。skill 应有一个 §207(e) 检查单，将包含项、0.5× vs 1.5× 姿态、惩罚性赔偿翻倍和 SOL 回溯强加到每个答案中。

## 由此产生的一些具体事项

- **把学说放在 skill 中。** 如果 skill 的模式涵盖专利，涵盖外观设计专利。如果它涵盖加班，涵盖常规费率公式。不是指向"也想想"——而是实际检查单。
- **将来源标签附加到数字，而非段落。** `[model calculation — verify against the notice clause]` 紧邻日期；`[verify — consult wage-and-hour counsel before asserting or paying]` 在欠薪数字出现的那一行。周围散文上的标签会丢失；承重数字上的标签不会。
- **将拒绝路径做成支架，而非逃生舱。** 如果某些类别问题的正确答案是"我拒绝计算"，把它作为硬关卡 baked 到 skill 中。`legal-clinic` 的 `/deadlines` 不计算规则就是范例：直白陈述、不可覆盖、skill 拥有。
- **关卡标题写成使关卡默认开启。** 如果有豁免，将标题表述为关卡并在子项目符号中缩小豁免，而非反过来。承重括号是一个等待下一次编辑重新引入的 bug。

## 工作流说明

- **在编辑 plugin 中的任何 skill 之前，先阅读该 plugin 的 `PRISMCLAW.md`。** 执业档案、集成表、共享护栏和决策姿态说明都塑造 skill 应该说和省略的内容。
- **有实质性变更时提高 plugin 版本。** 行为添加做补丁版号提高；新 skill 或新必需输入做次版本号提高。
- **运行验证器。** `scripts/validate.py` 和 `scripts/lint-tool-scope.py` 检查 plugin 加载器依赖的结构不变量。
- **不要从 PRISMCLAW.md 中移除共享护栏。** 网保留。目标是不需要网的 skill，不是没有网的 plugin。
