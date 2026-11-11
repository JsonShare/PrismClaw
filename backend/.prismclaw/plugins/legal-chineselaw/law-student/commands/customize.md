---
name: student-customize
description: 引导式调整法学生档案——只改一项，不必重跑整套冷启动访谈。可修改当前课程、学习方式、提纲偏好、律考科目、种子材料或学习节奏。当用户说「改一下我的某项设置」「加一门课」「更新我的档案」「新学期」「自定义」时使用。
argument_hint: '[section name, or describe what you want to change]'
category: 法学学习
customizes: law-student
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# 自定义法学学习配置

## 读取当前配置

读取 `~/.prismclaw/plugins/legal-chineselaw/law-student/PRISMCLAW.md`，获取当前自定义配置。如果插件配置不存在或仍包含 `[PLACEHOLDER]` 值，提示用户先运行冷启动访谈。

## 显示可自定义项

列出所有可自定义项及其当前值：

- **学生档案** —— 姓名、学校、年级（1L/2L/3L/LLM）、bar 司法管辖区、注册的诊所或期刊
- **当前课程** —— 课程名称、教授、教学大纲路径、考试格式（闭卷/开卷、论文/MBE/混合）、cold-call 风格
- **学习风格** —— Socratic 与摘要、你想要多少反击、插件是重写你的工作还是仅结构性批评
- **大纲偏好** —— 大纲格式（IRAC/CREAC/案例摘要风格）、规则详细程度、是否包括政策讨论、保存的大纲模板
- **Bar prep** —— 哪个考试（UBE/州）、轮换科目、薄弱科目标记、MBE 与论文节奏
- **种子材料** —— 案例书路径、先前大纲、评分论文、旧考试、MBE 套题、教学大纲、论文
- **学习工作流** —— 会话长度、flashcard Leitner 分桶时间表、考试预测节奏、cold-call prep 时机
- **集成** —— 文档存储 / flashcard 应用（如有）状态、备用方案

## 修改配置

询问用户想修改什么，然后修改对应配置项。展示当前值，询问新值，说明下游影响，确认后写入配置。

示例：
- *添加新课程：* "`/outline-builder` 将为此课程搭建新大纲。`/flashcards` 将添加新的主题存储桶。当你为此课程调用 `/cold-call-prep` 时，它会询问座位和主题。"
- *学习风格 Socratic → summary-first：* "`/socratic-drill` 不会要求你先回答——它会展示规则和示例，然后测试你的应用。"
- *添加 bar 科目：* "`/bar-prep-questions` 将在轮换中包括此科目，如果你将其标记为薄弱，则权重更高。"

修改后保存。

## 关闭

确认修改完成，告知用户如何恢复默认。

## 护栏

- **永不删除部分。** 如果用户想要"删除"课程，提供将其标记为 `[Archived — retain seed materials]` 并解释 flashcard 和大纲行为的变化。
- **标记内部不一致。** 如果更改会使档案不一致（例如，"summary-first"学习风格 + "maximum pushback"Socratic 设置），标记紧张关系。
- **标记护栏降级。** `/legal-writing` 和 `/irac-practice` 上的"不重写你的写作"规则是承重的——skill 的价值是结构性反馈，而不是代写。如果用户要求关闭此功能，确认他们理解插件不会为他们撰写作品。
- **一次更改一个。** 不要重新询问整个访谈。

> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
