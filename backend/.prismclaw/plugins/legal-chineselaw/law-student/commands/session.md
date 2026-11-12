---
name: session
description: 围绕某一科目做一次限定题量的集中练习——MBE、论述题或记忆卡，记录表现并更新学习计划。当用户说「给我来 10 道某科目的题」「做一次某科目的练习」「刷 5 张卡」，或需要按固定题量训练并让计划自适应时使用。
argument_hint: <subject> <n> [--mbe | --essay | --flashcards]
category: 法学学习
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /session

1. 解析 `$ARGUMENTS` —— 主题和 N。如果缺失，询问：
   > 什么主题，多少问题？（例如，`Evidence 10` 或 `Contracts 5 --essay`。）
2. 加载 `~/.prismclaw/assets/plugins/legal-chineselaw/law-student/PRISMCLAW.md` → 司法管辖区、考试格式、薄弱学科。
3. 加载 `~/.prismclaw/assets/plugins/legal-chineselaw/law-student/study-plan.yaml`（如存在）。阅读此主题的 `session_history` 以权倾向于学生薄弱的子主题。
4. 按方法标志路由：
   - `--mbe`（bar prep 科目的默认值）：加载 `bar-prep-questions` skill，运行 N 个 MBE 风格问题。应用司法管辖区处理（请参阅该 skill 的 `## Jurisdiction handling`）。将每个标记为 `[UBE/majority]` 或 `[state-specific]`。
   - `--essay`：加载 `bar-prep-questions`，运行 N 个论文提示。按论文模式 rubric 评分。
   - `--flashcards`：加载 `flashcards` skill，在 `--drill` 模式下运行 N 张卡片。
5. 每次运行 N 个问题。每次之后，解释对/错并在司法管辖区分歧时标记规则主体。
6. 会话结束时，写入会话结果：
   - 如果 `study-plan.yaml` 存在：根据 `study-plan` skill 的架构追加到 `session_history`。
   - 如果不存在：写入 `~/.prismclaw/assets/plugins/legal-chineselaw/law-student/session-history.yaml`。
7. 报告：
   - 分数：X/N（百分比）
   - 未命中：附带子主题标签的列表
   - 本次会话的薄弱子主题
   - 与此主题上先前会话的模式（如历史记录有 2 次以上）
   - 计划现在推荐的下一步


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
