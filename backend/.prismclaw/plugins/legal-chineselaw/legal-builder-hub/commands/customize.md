---
name: builder-customize
description: '引导式自定义您的 Legal Builder Hub 执业档案——更改一件事 而不重新运行整个冷启动访谈。调整执业档案、已安装的入门包、
  监控的注册表、更新偏好或质检严格度。当用户说"更改我的 [某事]"、 "添加注册表"、"更新我的档案"、"编辑我的配置"或"自定义"时使用。

  '
argument_hint: '[章节名称，或描述您想要更改的内容]'
category: 技能管理
customizes: legal-builder-hub
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# 自定义技能管理配置

## 读取当前配置

读取 `~/.prismclaw/plugins/legal-chineselaw/legal-builder-hub/PRISMCLAW.md`（以及上一级的 `~/.prismclaw/plugins/legal-chineselaw/company-profile.md`），获取当前自定义配置。如果插件配置不存在或仍包含 `[PLACEHOLDER]` 值，提示用户先运行冷启动访谈。

## 显示可自定义项

列出所有可自定义项及其当前值：

- **公司/您是谁** — 名称、行业、司法管辖区、阶段、执业设置 *（跨所有 12 个插件共享——更改通过 `company-profile.md` 流转）*
- **您的执业档案** — 范围内的执业领域，用于推荐社区 skills
- **已安装的入门包** — 通过中心安装了哪些插件和 skills，及其安装来源
- **监控的注册表** — 中心从中拉取社区 skills 的 GitHub 仓库/URL
- **更新偏好** — 检查频率（每天/每周/按需）、通知渠道（企业协作/钉钉/会话内）、自动更新 vs. 提示
- **质检严格度** — `/skills-qa` 在安装候选 skill 时标记问题的积极程度（宽松/中等/严格），以及哪些失败模式检查开启
- **Skill 安装默认值** — 安装范围（用户/项目）、安装前是否自动运行 `/skills-qa`
- **集成** — 企业协作/钉钉/文档存储状态、备用方案

## 修改配置

询问用户想修改什么，然后修改对应配置项。展示当前值，询问新值，说明下游影响，确认后写入配置。

示例：
- *添加新的监控注册表：* "`/registry-browser` 将在现有注册表旁搜索此注册表。`/auto-updater` 将在下次运行时检查它。"
- *质检严格度从严格 → 中等：* "`/skills-qa` 将报告相同的发现，但不会在中等层级阻止安装，除非您确认。"
- *自动更新从开 → 关：* "中心将在应用更新之前提示您，而不是自动应用。"

对于共享档案更改（公司名称、行业、司法管辖区、执业设置、阶段）：写入 `~/.prismclaw/plugins/legal-chineselaw/company-profile.md` 并注明影响范围。修改后保存。

## 关闭

确认修改完成，告知用户如何恢复默认。

## 护栏

- **永不删除章节。** 如果用户想要"移除"一个监控注册表，提议将其标记为 `[Paused]`，并说明暂停保留安装历史但停止更新检查。
- **标记内部不一致。** 如果更改会使档案不一致（例如，自动更新开 + 质检严格度关；或执业档案与任何已安装插件不匹配），标记紧张关系。
- **标记护栏降级。** 法律 Skill 设计框架检查（九个设计参数、三个法律失败模式、信任表面检查）是 `/skills-qa` 存在的原因——关闭它们违背了初衷。如果用户想要降低严格度，推荐中等层级而不是禁用检查。
- **一次更改一项。** 不要重新询问整个访谈。

> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
