---
name: privacy-customize
description: '引导式自定义你的隐私执业档案——只需更改一项内容而无需重新运行整个冷启动访谈。调整风险姿态、 升级联系人、DPA 剧本、隐私政策承诺、PIA
  内部风格、DSAR 流程或事项工作空间路径。当用户说 "更改我的[某事]"、"更新我的档案"、"编辑我的剧本"或"自定义"时使用。

  '
argument_hint: '[section name, or describe what you want to change]'
category: 数据隐私
customizes: privacy-legal
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# 自定义数据隐私配置

## 读取当前配置

读取 `~/.prismclaw/assets/plugins/legal-chineselaw/privacy-legal/PRISMCLAW.md`（以及上一层级的 `~/.prismclaw/assets/plugins/legal-chineselaw/company-profile.md`），获取当前自定义配置。如果插件配置不存在或仍包含 `[PLACEHOLDER]` 值，提示用户先运行冷启动访谈。

## 显示可自定义项

列出所有可自定义项及其当前值：

- **公司 / 你是谁** — 名称、行业、司法管辖区、阶段、执业设置、控制者 vs. 处理者方向 *(在所有 12 个插件之间共享——更改通过 `company-profile.md` 流动)*
- **风险姿态** — 保守 / 中间 / 激进，每个对于处理者义务、跨境传输和保留意味着什么
- **人员** — DPO、隐私团队、工程联络人、外部律师、升级链
- **DPA 剧本** — 关于子处理者通知、删除、审计、责任、国际传输、SCC 的立场——作为处理者和作为控制者
- **隐私政策承诺** — 你的隐私声明中 `/privacy-policy-monitor` 监视实践与之一致的承诺
- **PIA 内部风格** — 章节顺序、风险评分、利益相关者框架、DPIA 触发器何时适用
- **DSAR 流程** — 验证、每个制度的法定时间线、豁免申请、模板响应结构
- **工作流** — 接收路径、事项工作空间、policy-monitor 扫描节奏
- **集成** — 文档存储 / 隐私工具 / Slack 状态、备用方案

## 修改配置

询问用户想修改什么，然后修改对应配置项。展示当前值，询问新值，说明下游影响，确认后写入配置。

示例：
- *子处理者通知 30 天 → 14 天：* "`/dpa-review` 现在会将任何短于 14 天的标记为偏差。现有 DPA 保持已记录状态。"
- *剧本中的新 DSAR 豁免：* "`/dsar-response` 将在事实匹配的评估步骤中呈现此豁免。"
- *风险姿态中间 → 保守：* "我会标记更多活动以进行 PIA 升级，建议更严格的 SCC 条款，并在保留方面更加保守。"

对于共享档案更改（公司名称、行业、司法管辖区、执业设置、阶段）：写入 `~/.prismclaw/assets/plugins/legal-chineselaw/company-profile.md` 并注意影响范围。修改后保存。

## 关闭

确认修改完成，告知用户如何恢复默认。

## 护栏

- **永不删除部分。** 如果用户想要从范围中"移除"一个制度，提议将其标记为 `[Not currently in scope]` 并解释标记会丢弃什么。
- **标记内部不一致。** 如果更改会使档案不一致（例如，"仅处理者" + 控制者剧本立场激活；或"无欧盟联系" + 默认模板中的 SCC），标记紧张关系。
- **标记护栏退化。** `[review]` 标记、来源归因标签、引用法规上的 `[verify]` 标签以及 `/privacy-use-case-triage` 上的 DPIA 触发器强制检查是承重的——不要删除。如果法定 DSAR 时间线被调整到监管最低以下，拒绝并解释原因。
- **一次一个更改。** 不要重新询问整个访谈。

> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
