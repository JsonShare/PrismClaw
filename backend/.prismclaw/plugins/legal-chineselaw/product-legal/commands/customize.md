---
name: product-customize
description: '引导式自定义你的产品法律顾问执业档案——只需更改一项内容而无需重新运行整个冷启动访谈。调整风险校准、 升级联系人、发布审查框架、营销声明姿态或事项工作空间路径。当用户说"更改我的[某事]"、
  "更新我的档案"、"编辑我的框架"、"重新校准"或"自定义"时使用。

  '
argument_hint: '[section name, or describe what you want to change]'
category: 产品合规
customizes: product-legal
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# 自定义产品合规配置

## 读取当前配置

读取 `~/.prismclaw/plugins/legal-chineselaw/product-legal/PRISMCLAW.md`（以及上一层级的 `~/.prismclaw/plugins/legal-chineselaw/company-profile.md`），获取当前自定义配置。如果插件配置不存在或仍包含 `[PLACEHOLDER]` 值，提示用户先运行冷启动访谈。

## 显示可自定义项

列出所有可自定义项及其当前值：

- **公司 / 你是谁** — 名称、行业、司法管辖区、阶段、执业设置、产品覆盖面 *(在所有 12 个插件之间共享——更改通过 `company-profile.md` 流动)*
- **发布审查流程** — 接收（Jira / Linear / Asana / 文档）、审查 SLA、发布分级、PRD 位置
- **审查框架** — 你针对发布进行审查的类别（隐私、IP、安全、声明、监管、可访问性等）以及每个的深度
- **风险校准** — 什么在此公司是 P0 阻塞 / 需要真正审查 / 可以通过，附有锚定标签的示例
- **营销声明** — 吹嘘 vs. 须证实的姿态、比较性声明框架、最高级、AI 功能声明的内部规则
- **人员** — 按覆盖面的产品合伙人、升级链（你的经理、GC、风险委员会）、营销对接人
- **工作流** — 事项工作空间、发布雷达观察器节奏、发布审查模板
- **集成** — Jira / Linear / Asana / Slack / 文档存储状态、备用方案

## 修改配置

询问用户想修改什么，然后修改对应配置项。展示当前值，询问新值，说明下游影响，确认后写入配置。

示例：
- *风险校准将"可以通过"收紧为"需要真正审查"对于某个模式：* "`/is-this-a-problem` 和 `/launch-review` 将开始标记此模式。现有审查保持不变；如果你想要新姿态应用，重新运行。"
- *新的发布审查类别：* "`/launch-review` 将为此类别添加一个部分。`/is-this-a-problem` 将在分流中模式匹配它。"
- *营销声明姿态收紧：* "`/marketing-claims-review` 将标记更多需要证实或重写的语言。"

对于共享档案更改（公司名称、行业、司法管辖区、执业设置、阶段）：写入 `~/.prismclaw/plugins/legal-chineselaw/company-profile.md` 并注意影响范围。修改后保存。

## 关闭

确认修改完成，告知用户如何恢复默认。

## 护栏

- **永不删除部分。** 如果用户想要"删除"审查类别，提议将其标记为 `[Not in scope — route elsewhere]` 并命名接管它的插件/团队。
- **标记内部不一致。** 如果更改会使档案不一致（例如，AI 功能声明审查开启 + `/ai-governance-legal` 中未设置 AI 政策承诺；或"快速 SLA" + "每次发布需要 GC 签署"），标记紧张关系。
- **标记护栏退化。** `[review]` 标记、来源归因标签和引用法规上的 `[verify]` 标记是承重的——不要删除。声明的证实要求是 `/marketing-claims-review` 存在的理由；削弱它会破坏 skill。
- **一次一个更改。** 不要重新询问整个访谈。

> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
