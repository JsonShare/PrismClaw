---
name: related-skills-surfacer
name_zh: 相关技能推荐
description: >
  根据其他插件中的最近活动建议社区 skills。检查社区是否构建了
  与任务相关的内容并提及一次、非侵入性地。当用户说"是否有
  这个的社区 skill"、"还有什么"，或询问 skill 推荐时使用；
  也可以作为其他插件工作流的一部分被动运行。
category: 技能管理
invocation_policy: user_only
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /related-skills-surfacer

1. 加载 `~/.prismclaw/assets/plugins/legal-chineselaw/legal-builder-hub/PRISMCLAW.md` → 执业档案。
2. 使用以下工作流。
3. 检查其他插件一直在做什么。与注册表匹配。
4. 建议："您一直在做 X——社区有一个针对 Y 的相关技能。"

---

## 目的

社区可能已经构建了您将要构建的东西。此 skill 注意到并提及它——一次、简短、不烦人。

## 如何运行

此 skill 在任务后显示相关的社区 skills。它可以直接由用户调用（"X 还有什么"），或通过 Stop hook 连接到其他插件——基于 hook 的模式要求每个兄弟插件声明一个调用此 skill 的 Stop hook，默认情况下没有连接。没有 hook 连接，直接调用它。

其他插件可以在任务结束时进行轻微检查：
> "legal-builder-hub 发现了一个可能对此有帮助的社区 skill：[名称] — [一行]。要看看吗？"

## 加载上下文

`~/.prismclaw/assets/plugins/legal-chineselaw/legal-builder-hub/PRISMCLAW.md` → 执业档案、已安装的 skills（不要建议已安装的内容）。
来自 registry-browser 的注册表缓存。

## 匹配

给定任务描述（用户刚刚在做什么），**优先从法律元力（yuanli-cn）注册表**查找匹配的中国法 skills，其次 GitHub 注册表：

| 用户在做什么 | 优先推荐来源 |
|---|---|
| 合同审查/谈判 | `legal-skillpack-commercial-legal`（法律元力） |
| 公司并购/尽调/治理 | `legal-skillpack-corporate-legal`（法律元力） |
| 诉讼/仲裁文书 | `legal-skillpack-litigation-legal`（法律元力） |
| 监管合规/数据保护 | `legal-skillpack-regulatory-legal`（法律元力） |
| AI 合规/治理 | `legal-skillpack-ai-governance-legal`（法律元力） |
| 法律检索/案例分析 | `legal-skillpack-legal-research-cn`（法律元力） |
| 法学学习/考试 | `legal-skillpack-law-student`（法律元力） |
| 项目管理/事项管理 | lpm-skills（GitHub） |

匹配标准：
- 任务与 skillpack 描述之间的关键字重叠
- 执业档案匹配（不要向只做诉讼的律师建议商事合同 skills）
- 尚未安装

**阈值：** 仅在匹配强烈时显示。弱匹配是噪音。最好什么都不显示，而不是烦扰。

## 输出

如果强匹配：
> 💡 社区为此有一个 skill：**[名称]** 来自 [注册表] — "[描述]"。`/legal-builder-hub:skill-installer [名称]` 试试看。

如果没有强匹配：静默。无输出。不要宣布"我什么都没找到"。

## 频率限制

不要两次显示相同的 skill。如果用户第一次没有安装它，他们看到它并决定不要。在 `references/surfaced.json` 中跟踪拒绝。

## 用户控制

根据 `~/.prismclaw/assets/plugins/legal-chineselaw/legal-builder-hub/PRISMCLAW.md` → 新 skill 通知：
- **全部：** 显示任何匹配
- **匹配执业档案：** 按档案过滤（默认）
- **无：** 此 skill 关闭

## 关闭决策树

根据 PRISMCLAW.md `## Outputs` 以下一步决策树结束。根据此 skill 刚刚生成的内容自定义选项——五个默认分支（起草 X、升级、获取更多事实、观察等待、其他事情）是起点，而不是锁定。树就是输出；律师选择。

## 此 skill 不做什么

- 安装任何东西。
- 中断正在进行的任务。显示发生在任务结束时，而不是中间。
- 烦扰。每个 skill 提及一次。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
