---
name: exam-forecast
description: '分析同一教授的过去考试以揭示模式——学科权重、反复出现的问题识别陷阱、 偏好的假设类型、政策与教义的混合——并预测即将到来的考试的可能重点。
  当用户说"考试有什么"、"分析过去考试"、"预测考试"或分享过去考试时使用。

  '
argument_hint: '[class name, with past exams shared or paths to them]'
category: 法学学习
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /exam-forecast

1. 加载 `.prismclaw/plugins/legal-chineselaw/law-student/PRISMCLAW.md` → class、professor、exam format、syllabus。
2. 应用以下工作流。
3. 接收过去考试（PDF、粘贴或路径）。确认样本量。
4. 分析每次过去考试：格式、学科覆盖、问题风格、事实模式密度、反复出现的陷阱。
5. 跨考试模式分析——什么是稳定的，什么是变化的。
6. 结合当前教学大纲生成预测：学科权重、格式、爱好、学习重点。
7. 编写 `.prismclaw/plugins/legal-chineselaw/law-student/exam-forecasts/[class]/forecast-[YYYY-MM-DD].md`。构建为加权启发式，而不是预测。

---

## Purpose

每个教授的考试都有指纹。相同的假设结构反复出现。相同的陷阱回来。相同的学科比例重复。拥有过去考试的学生更聪明地学习；没有的学生更努力地学习。此 skill 分析你拥有的过去考试并揭示模式。

不是魔法。预测，不是预言。Skill 不能告诉你考试有什么——它可以告诉你过去考试有什么，以及根据教学大纲覆盖可能反复出现什么。

## Confidence discipline

- 模式分析（出现了哪些学科、每个主题多少问题、政策与规则应用的频率）——在考试清楚在我面前时有信心。
- 关于即将到来的考试的可能重点的推断——`[UNCERTAIN]` 是默认；这些是预测，不是确定性。明确构建为"基于你分享的 [N] 次过去考试，[主题] 在 [M] 中出现。你即将到来的考试可能强调它，或者教授可能会轮换——将此作为复习时间的权重，而不是预测。"
- 如果只有 1-2 次过去考试可用，明确说明——从 1 次考试推断的任何模式都是噪音。
- 如果教授是新（没有过去考试可用），skill 无法预测。这样说；回退到仅基于教学大纲的"这些是涵盖的学科"。

## Load context

- `.prismclaw/plugins/legal-chineselaw/law-student/PRISMCLAW.md` → 当前课程、考试格式、教学大纲（如果捕获）
- 用户提供的过去考试（PDF、粘贴文本、路径）
- Optional：当前课程的教学大纲（用于"迄今为止涵盖的内容"）

**如果上传的过去考试有教授姓名，使用它来匹配模式**（同一教授的考试是最高信号输入）。**如果没有，按学科和结构匹配。**不要要求用户输入教授姓名——使用材料中的内容。如果用户在对话中自愿提供，那很好；不要提示。

## Workflow

### Step 1: Intake

- 我们为哪个课程预测？
- 这位教授有多少次过去考试可用？
- 它们是来自同一课程，还是同一教授的不同课程？
- 它们中是否有任何是带回家/开卷/不同格式的变体，相对于你即将到来的考试的典型格式？
- 你当前课程的教学大纲？

如果少于 3 次过去考试：标记为样本薄弱。模式推断较弱。
如果考试跨越不同课程：一些模式转移（问题风格、政策与教义比率）；特定学科的模式不转移。

### Step 2: Read each past exam

对于每次过去考试：

- Format（问题数量、长度、时间限制、开卷/闭卷）
- Subject coverage（测试了哪些主题，以什么比例）
- Question style（问题识别、单一问题深入、政策论文、简答 MBE 风格、混合）
- Fact pattern density（事实密集的假设、事实稀疏的教义重点，或没有事实的政策提示）
- Recurring traps（例如，教授总是将管辖权问题隐藏在否则干净的事实模式中；教授总是问例外而不是规则）
- Policy vs. doctrine ratio
- Unusual structures（论文 + MBE 混合、模拟法庭情景等）

### Step 3: Cross-exam pattern analysis

汇总考试中一致的内容：

**Stable patterns（出现在大多数/所有过去考试中）：**
- Subject weights（例如，"consideration and modification consistently account for 30% of exam points"）
- Question style（例如，"always one long issue-spotter + two short-answer hypos"）
- Professor hobby horses（例如，"always tests third-party beneficiaries even when it's a minor topic in class"）

**Variable patterns（出现在一些但不是所有考试中）：**
- Policy essays（例如，"appeared in 2 of 4 past exams — usually when the semester covered a policy-heavy topic late"）
- Open-book vs. closed-book differences
- Take-home vs. in-class differences

**Absent patterns worth noting:**
- Topics covered in class that have NEVER been tested in past exams — 不要跳过这些，但也不要过度加权
- Topics tested in past exams that aren't in your current syllabus — 可能不会再出现

### Step 4: Forecast for the upcoming exam

**Header — required, first line of the forecast, both in-chat and in the saved file.** 根据 plugin config `## Outputs`，每个学习输出都携带逐字学习笔记标题。预测是学习输出。不要省略、改写或重新定位标题。标题不是学生可以要求删除的免责声明；它是输出的身份，防止预测被误认为预测的考试或法律建议：

```
STUDY NOTES — NOT LEGAL ADVICE
```

结合模式分析与当前教学大纲：

```markdown
STUDY NOTES — NOT LEGAL ADVICE

# Exam Forecast — [class / professor] — [date]

**Past exams analyzed:** [N]
**Sample confidence:** [thin (<3) / moderate (3-5) / strong (6+)]
**Caveats:** [e.g., "one of the past exams was an open-book final; your upcoming is closed-book. Pattern transfer is partial."]

---

## Subject weighting (historical)

| Topic | Past exam weight (avg) | In current syllabus? | Forecast weight |
|---|---|---|---|
| [topic 1] | [%] | [yes/partial/no] | [heavier / stable / lighter] |

## Question-style forecast

- **Format likely:** [X issue-spotters + Y short answers + Z policy, or similar]
- **Fact-pattern density:** [fact-heavy / sparse / mixed]
- **Call style:** [one broad call / multiple specific calls / bullet sub-parts]

## Professor hobby horses to watch

- [topic A] — appeared in [M of N] past exams. Weighted 3-5x its syllabus share.
- [topic B] — [pattern]
- [trap pattern] — e.g., "hides jurisdictional issue in otherwise-clean facts"

## Topics covered this semester but rarely tested

[list — don't skip, but don't over-weight]

## Study emphasis recommendation

基于过去考试模式和当前教学大纲覆盖：

**Heavy：** [topics likely to anchor the exam — 40-50% of study time]
**Moderate：** [supporting topics — 30-40%]
**Sanity check：** [topics covered but historically under-represented — 10-20%, just in case]

## [UNCERTAIN — framing]

此预测来自 [N] 次过去考试。教授变化。教授轮换。过去几年强调的主题可以在教学大纲转移时被淡化。将此视为学习时间的加权启发式，而不是预测。考试将包括惊喜。
```

### Step 5: Output location

写入 `.prismclaw/plugins/legal-chineselaw/law-student/exam-forecasts/[class]/forecast-[YYYY-MM-DD].md`。版本控制——如果学生在学期中获得另一次过去考试，重新运行并附加。

## Integration

- **outline-builder：** 预测权重输入到大纲深度决策——在重要主题上加权深度
- **flashcards：** 预测重要主题生成更多卡片
- **bar-prep-questions：** 与律师考试准备无关（有自己的预测模型）；exam-forecast 用于课程特定的期末考试
- **irac-practice：** 使用预测主题作为 IRAC 练习假设的学科领域

## Close with the next-steps decision tree

按照 PRISMCLAW.md `## Outputs` 以下一步决策树结束。根据此 skill 刚刚生成的内容自定义选项——五个默认分支（起草 X、升级、获取更多事实、观察等待、其他事情）是起点，而不是锁定。树就是输出；律师选择。

## What this skill does not do

- **Predict specific questions.** 过去考试显示模式；它们不显示你明天的提示。
- **Work without past exams.** 如果你没有来自此教授的过去考试，skill 无法预测——它回退到"这里是教学大纲涵盖的内容，学习那个。"
- **Replace studying everything on the syllabus.** 预测是加权，而不是消除。因为主题历史上代表不足而跳过它是学生被烧伤的方式。
- **Account for changes you don't know about.** 如果教授今年转移了重点（例如，在课堂讲座中强调新案例），skill 不会看到，除非你告诉它。
- **Work reliably with 1-2 past exams.** 样本薄弱。标记为这样。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
