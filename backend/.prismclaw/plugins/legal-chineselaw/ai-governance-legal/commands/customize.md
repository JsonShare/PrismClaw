---
name: aig-customize
description: '引导式定制你的 AI 治理执业档案——在不重新运行完整冷启动访谈的情况下更改某一项。调整风险立场、升级联系人、用例登记册条目、供应商 AI
  立场、AI 政策承诺、影响评估内部风格或事项工作区路径。当用户说"change my [thing]"、"update my profile"、"edit my
  config"、"tune my playbook"或"customize"时使用。

  '
argument_hint: '[section name, or describe what you want to change]'
category: AI治理
customizes: ai-governance-legal
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# 自定义 AI 治理配置

## 读取当前配置

读取 `~/.prismclaw/assets/plugins/legal-chineselaw/ai-governance-legal/PRISMCLAW.md`（以及上一级的 `~/.prismclaw/assets/plugins/legal-chineselaw/company-profile.md`），获取当前自定义配置。如果插件配置不存在或仍包含 `[PLACEHOLDER]` 值，提示用户先运行冷启动访谈。

## 显示可自定义项

列出所有可自定义项及其当前值：

- **公司/你是谁** — 名称、行业、司法管辖区、阶段、执业设置 *（在所有 12 个 plugin 间共享——更改通过 `company-profile.md` 流转）*
- **监管足迹** — 欧盟 AI 法案、州 AI 法律、范围内的行业监管机构
- **风险立场** — 保守/中等/激进，每种对分类和 AIA 输出意味着什么
- **人员** — 治理团队、AI 风险负责人、升级链、审批人
- **用例登记册** — 已批准/有条件/从不条目，以及每条附加的条件
- **AI 系统清单** — 每个系统在欧盟 AI 法案下的角色（提供者/部署者等）和层级。运行 `/ai-governance-legal:ai-inventory` 使用专用编辑器。
- **供应商 AI 治理** — 你的供应商 AI 剧本中的数据训练、责任、模型变更通知和其他立场
- **AI 政策承诺** — 你的 AI 政策做出的公开或内部承诺，plugin 会交叉核对
- **影响评估内部风格** — AIA 章节顺序、风险评分格式、利益相关者框架
- **工作流** — 接收路径、输出格式、事项工作区路径、政策监控审查节奏
- **集成** — 已连接的内容（Slack、文档存储、定时任务），什么会退回

## 修改配置

询问用户想修改什么，然后修改对应配置项。展示当前值，询问新值，说明下游影响，确认后写入配置。

下游说明示例：
- *风险立场从中等 → 保守：* "我会将更多用例标记为有条件而非已批准，浮现更多 AIA 后续，并建议更保守的供应商 AI 红线。"
- *添加升级联系人：* "每个路由升级的 skill（`/aig-use-case-triage`、`/vendor-ai-review`、`/aig-reg-gap-analysis`）现在会在相关风险层级上包含此联系人。"
- *新用例登记册条目：* "`/aig-use-case-triage` 下次运行时将与此条目匹配。现有 AIA 不会重写——如果你想让新立场反映出来，重新运行它们。"

对于共享档案更改（公司名称、行业、司法管辖区、执业设置、阶段）：写入 `~/.prismclaw/assets/plugins/legal-chineselaw/company-profile.md` 并注明影响范围。修改后保存。

## 关闭

确认修改完成，告知用户如何恢复默认。

## 护栏

- **永远不要删除章节。** 如果用户想"删除"某些内容，将其设置为 `[Not configured]` 并解释这对 plugin 行为意味着什么。（"删除你的升级链意味着 `/aig-use-case-triage` 会标记需要升级的项目，但不会将其路由到特定人员。"）
- **标记内部不一致。** 如果更改会使档案不一致（例如，风险立场激进 + 升级"所有事情都发给合伙人"；或"生成式AI在范围内" + "没有系统标记为生成式AI"），标记张力并询问他们想要哪个。
- **标记护栏降级。** 如果用户要求关闭某个护栏（"停止添加 `[review]` 标记"、"删除引用警告"、"跳过特权标题"），解释该护栏防护的内容并确认他们理解权衡。大多数护栏是可调整的——少数是结构性的：
  - `[review]` 标记机制（告诉用户何时需要法律判断而不是给出自信的错误答案）——承重，不要删除。
  - 检索内容的来源归因标记——承重，不要删除。
  - 引用法规/法规上的 `[verify]` 标记——承重，不要删除。
- **一次一个更改。** 不要重新询问整个访谈。如果用户想要多个更改，依次处理并在继续前确认每个。

> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
