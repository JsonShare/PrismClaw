---
name: flashcards
description: '生成或抽认黑字律记忆卡片——Leitner 风格存储桶、每学科 markdown 存储、带自我评估的抽认模式。 当用户说"抽认卡片"、"从...制作卡片"、"考考我卡片"或想要记忆规则时使用。

  '
argument_hint: '[subject] [--generate | --drill | --review | --stats | --session <n>]'
category: 法学学习
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /flashcards

1. 加载 `.prismclaw/plugins/legal-chineselaw/law-student/PRISMCLAW.md` → 当前课程、弱学科、大纲位置。
2. 应用以下框架。
3. 按标志路由：
   - `--generate`：按照卡片编写规则从来源（大纲路径、笔记、案例书）构建卡片。
     写入 `.prismclaw/plugins/legal-chineselaw/law-student/flashcards/[subject]/cards.md`。
   - `--drill`（默认）：优先到期卡片 + 新卡片；显示 Q，等待答案，显示 A，
     接受自我评估，更新存储桶 + 下次审查。
   - `--review`：按存储桶浏览卡组。
   - `--stats`：进度快照；标记困难卡片以供口头抽认。
   - `--session <n>`：专注的 N 卡会话，按先前错误 + 到期卡片优先；
     将结果附加到 `study-plan.yaml` → `session_history`。
4. 应用信心纪律：标记每个从无来源知识生成的卡片为 `[VERIFY]`。

---

## Real-matter check

如果学生问的问题听起来像是关于真实情况——他们的租约、他们的停车罚单、他们家的生意、
他们朋友的被捕、真实的美元金额、真实的截止日期、真实的当事方名称——停止。

> "这听起来像是真实情况，而不是假设。我不能给你法律建议，你也不能给——你还不是律师。
> 如果这是真实的，[某人] 需要真正的律师：法律援助、你学校的诊所、律师推荐服务
  （你所在司法管辖区的律师协会、法律协会或法律援助机构），或（如果有资金）私人律师。
  我很高兴帮助你理解涉及的一般法律概念，但那是学习，而不是建议。"

注意：真实姓名、真实地址、真实日期、具体美元金额、"我的房东/老板/父母/朋友"、
"我收到了罚单/信件/通知"、以天衡量的截止日期。其中任何一个都是触发器。

## Purpose

大纲用于综合；抽认卡用于记忆。律师考试和大多数法学院考试奖励快速规则回忆。
此 skill 从你的大纲（或笔记或案例书摘录）生成卡片，用轻量间隔练习它们，并跟踪什么困难，什么不困难。

**不是完整的 SRS 系统。** 简单的 Leitner 风格存储桶。足以学习，足够轻量以维护。
如果你想要 Anki，使用 Anki；这是当你聊天并想要快速抽认时的工具。

## Confidence discipline

与其他内容生成技能相同的规则：

- 如果从你提供的来源（大纲、笔记、案例书摘录）生成卡片，卡片的 Q 和 A 来自该来源。有信心。
- 如果在没有来源的情况下从我的知识生成卡片，我将每张不完全自信的规则卡片标记为
  `[VERIFY: rule — confirm against source]`。你应该在将卡片作为学习目标之前检查。
- 如果我不太了解某个领域，我会生成更少的卡片而不是发明。最好有 8 张好卡片而不是 20 张其中有 5 张错误的。

## Load context

- `.prismclaw/plugins/legal-chineselaw/law-student/PRISMCLAW.md` → 当前课程、弱学科、现有大纲
- `.prismclaw/plugins/legal-chineselaw/law-student/flashcards/[subject]/cards.md`（如果存在）（增量构建）
- 用户提供的来源（大纲路径、笔记、案例书摘录）（如果给出）

## Modes

标志：`--generate | --drill | --review | --stats | --session <n>`（默认：提示）

### `--session <n>` — focused N-card session

当学生说"让我们做 5 张合同卡"或运行 `/law-student:session Contracts 5 --flashcards` 时。

- 如果存在 `.prismclaw/plugins/legal-chineselaw/law-student/study-plan.yaml`，加载它并阅读此学科的 `session_history`。
- 优先级：先前标记为错误的卡片 > 到期卡片 > 新卡片。
- 按照 `--drill` 流程一次运行 N 张卡片。
- 会话结束时，将结果附加到 `study-plan.yaml` → `session_history`：

```yaml
session_history:
  - date: 2026-05-08
    subject: Contracts
    type: flashcards
    n_cards: 5
    right: 3
    partial: 1
    wrong: 1
    stuck_topics: [parol-evidence-rule]
```

- 如果没有 `study-plan.yaml`，改为写入 `.prismclaw/plugins/legal-chineselaw/law-student/session-history.yaml`。

### `--generate` — create cards

**Inputs:**
- Subject（课程名称或主题）
- Source（大纲路径、笔记，或"use my existing outline from .prismclaw/plugins/legal-chineselaw/law-student/PRISMCLAW.md"）
- Optional：card count target（默认每次会话 10-20）

**Card structure:**

```markdown
### Card [N]
**Q:** [question — one concept, one card]
**A:** [answer — the rule, one or two sentences]
**Source:** [outline section, casebook page, class note date]
**Bucket:** new
**Last reviewed:** —
**Next review:** [today's date]
**Notes:** [optional — distinctions, exceptions, traps]
```

**Card-writing rules:**
1. **One concept per card.** "Elements of negligence"变为 4 张卡片，而不是 1 张。
2. **Front is a question, not a topic.** "Negligence duty"不好。"What are the four elements of negligence?"好。
3. **Back is a rule, not a paragraph.** 如果答案需要一段，拆分成多张卡片。
4. **Cite the source**以便你可以在抽认时重新检查。

**Citation check.** 当卡片是从我的知识而不是你粘贴的来源生成时，背面的规则和任何案例/法规引用
都是由 AI 模型生成的，尚未验证。在你记忆卡片之前，根据你的大纲、案例书或研究工具（Westlaw、Fastcase、CourtListener）确认它。
一张错误卡片练习到精通比没有卡片更糟糕。

### `--drill` — study session

**Prioritization:**
1. `next_review <= today` AND bucket != mastered 的卡片
2. 尚未尝试的新卡片
3. 如果没有到期卡片和新卡片：询问用户是否想要审查精通卡片（用于防止遗忘）

**Drill flow per card:**
1. Show Q. Wait for answer.
2. User answers（或输入 "skip" / "don't know"）
3. Show A.
4. User self-assesses：`right` / `partial` / `wrong` / `don't know`
5. 按照下表更新存储桶 + 下次审查：

| Self-assessment | Bucket change | Next review |
|---|---|---|
| right | up one (new → learning → review → mastered) | +1d new, +3d learning, +7d review, +21d mastered |
| partial | same bucket | +1d |
| wrong | down one (review → learning; learning → new; new stays new) | today +4h |
| don't know | down one | today +4h |

### `--review` — browse deck

显示学科中的所有卡片。按存储桶分组。用于扫描卡组中的内容并手动调整卡片内容。

### `--stats` — progress snapshot

每个学科：总卡片数、存储桶分布、今天到期、本周已审查。突出显示任何已经两次以上弹回到 `new` 的卡片——这些是值得通过 `/law-student:socratic-drill` 进行口头抽认的困难概念。

## Integration with other skills

- **outline-builder：**构建或扩展大纲后，提议从新材料生成抽认卡
- **socratic-drill：**如果卡片错误 2 次以上，将其路由到 `/law-student:socratic-drill` 进行口头练习——抽认卡对于你不真正理解的概念是不够的
- **bar-prep-questions：**抽认卡统计数据差的律师考试预备学科在 MBE 抽认中权重更高

## Storage

```
flashcards/
└── [subject]/
    └── cards.md
```

每个学科一个文件。卡片是 markdown。存储桶/审查元数据每张卡片内联。对于非常大的卡组（>500）不是最优的，但对于典型的法学院卡组大小来说没问题。

## What this skill does not do

- **Replace Anki.** 如果你已经有抽认卡习惯，保持它。这是当你在聊天中想要抽认而不切换应用时的工具。
- **Invent cards to hit a count target.** 如果我只能从你的来源生成 8 张有信心的卡片，你就得到 8 张。
  用大量 `[VERIFY]` 猜测填充比更小的卡组更糟糕。
- **Enforce study discipline.** 错过的审查天数会复合；skill 只显示到期的内容。你决定是否抽认。
- **Teach you the rule.** 卡片用于抽认你已经学过的内容。如果卡片一直错误，问题在上游——
  使用 `/law-student:socratic-drill` 或重新阅读来源。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
