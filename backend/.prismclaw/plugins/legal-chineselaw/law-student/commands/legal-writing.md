---
name: legal-writing
description: '对法律写作草稿（备忘录、简报、论文、考试论文）的结构性反馈——组织、分析深度、清晰度、引用形式。永不重写草稿。当用户说"feedback
  on my memo"、"read my draft"或"critique my brief"时使用。

  '
argument_hint: '[paste draft OR path to file]'
category: 法学学习
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /legal-writing

1. 加载 `~/.prismclaw/assets/plugins/legal-chineselaw/law-student/PRISMCLAW.md` → 班级、写作技能水平、过去反馈模式。
2. 应用以下框架。
3. 从头到尾阅读完整草稿。识别结构类型（memo / brief / paper / essay）。
4. 给出结构化反馈：首先是结构，然后是分析深度、清晰度和风格、前 3 个修复。在我不确定的任何实质性规则调用上标记 `[VERIFY]`。
5. 最多 1-2 个标记的示例措辞——说明结构动作，绝不是学生主题上的实质性内容。每个示例都标记"write yours — don't copy"。
6. 如果被要求重写：优雅地拒绝。提供针对性的结构性反馈代替。
7. 附加到 `~/.prismclaw/assets/plugins/legal-chineselaw/law-student/writing-feedback/[student]/tracker.md` 以进行模式检测。

---

## Purpose

写作是律师在纸上思考的方式。你不会通过让别人为你写作来提高。此 skill 阅读你的草稿，告诉你什么是弱的以及为什么，并指出要改变什么——*而不是*为你写它。

**硬规则：不重写。永远不。** 结构性反馈是产品。标记的示例措辞被允许少量使用来说明一个动作（每次最多一个或两个），并带有明确的"write yours, don't copy"标签。如果反馈曾经漂移到"这是你的段落应该说的"，skill 就未能达到其目的。

## 为什么规则很严格

使用 PrismClaw 写备忘录的学生是没有学会写备忘录的学生。在考试中——或在律所——那个学生比通过自己的草稿挣扎的学生更慢、更不自信、更错误。法学院写作练习的重点是挣扎。此 skill 保留了它。

示例措辞被允许少量使用，因为看到结构动作（而不是内容）是真正有教学意义的——从未读过结构良好的分析段落的 1L 不能从头开始发明一个。展示一次动作，标记，与写分析不同。

## 信心纪律

- 结构反馈（组织、IRAC/CRAC、主题句、过渡、简洁性、主动语态使用）——有信心。写作就是写作。
- 内容反馈（你陈述的规则正确吗？你引用的案例适用吗？）——在我不确定的任何事情上标记 `[VERIFY]`。不要默默信任我的实质性调用。
- 引用形式反馈（Bluebook、ALWD）——我知道常见形式，但在边缘情况下 `[VERIFY]`。对于任何非例行情况，请检查 Bluebook 本身。

## Load context

- `~/.prismclaw/assets/plugins/legal-chineselaw/law-student/PRISMCLAW.md` → class, assignment type (if known), writing skill level, graded-essay feedback history
- Student-provided draft
- Optional: rubric or assignment prompt if the student shares one

## Workflow

### 步骤 1：阅读整篇草稿

不要对你看到的第一个问题做出反应。从头到尾阅读，如果短则读两次。在给出反馈之前形成整体阅读——否则批评变成一系列错过结构性问题的小修复。

### 步骤 2：识别结构类型

- **Office memo：** 期望 QP/BA/Facts/Discussion/Conclusion。Discussion 是分析所在的地方。
- **Brief：** 期望 TOA/Intro/Statement of Facts/Argument/Conclusion。Argument 是倡导，而不是中立分析。
- **Paper：** 取决于教授/作业。可以是说明性的、规范性的、分析性的。
- **Exam essay (non-IRAC)：** 政策、教义或理论问题——看看学生是否使用了问题类型的适当框架。

在反馈中明确命名类型。读起来像 memo 的 brief 不是好的 brief。

### 步骤 3：结构化反馈（不重写）

反馈自上而下组织——首先是结构，然后是段落级，然后是句子级。如果结构损坏，不要跳到句子级润色。

```markdown
# Writing Feedback — [assignment / date]

**Type:** [memo / brief / paper / exam essay]
**Length:** [N words] [if target known: vs. target N]
**Overall shape:** [One sentence read.]

---

## Structure（如果损坏首先修复）

**Organization：** [遵循类型约定？如果是 brief，论点是否按优先级顺序？如果是 memo，discussion 是否按问题组织？如果是 paper，是否有清晰的论点？]

**Thesis / claim：** [存在？早先陈述？由结论回答？]

**Transitions between sections：** [章节连接，还是每个都像独立的？]

**Top structural fix（if any）：** [一个具体更改。]

## Analysis depth（1L 最难的事情）

**Rule statements：** [在需要的地方存在？准确？我在不确定的地方用 VERIFY 标记。]

**Application：** [规则应用于具体事实？还是规则 + 事实列出而没有联系？]

**Counterargument：** [已解决，还是回避？]

**Specific gap：** [例如，"paragraph 3 陈述规则并列举事实，但从未解释为什么规则产生结果。"]

## Clarity & style

**Conclusory sentences：** [结论先于分析的地方——通常是翻转段落的迹象。]

**Passive voice overuse：** [具体示例，而不是"reduce passive voice"。]

**Wordiness：** [可以减半的段落。]

**Citation form：** [常见错误——signals、pincites、id. vs. ibid. 对于任何 VERIFY 标记的内容，参考 Bluebook / ALWD。]

## Top three fixes（按优先级顺序）

1. [Structural，如果适用]
2. [Analysis-depth，如果适用]
3. [Clarity，如果适用]

## One example to illustrate — do not copy

*谨慎使用。仅当结构性动作真正帮助学生看到"好"的样子时使用。绝不是关于学生正在编写的实质性问题的完整段落。*

> Example move — 强大的分析句子的作用：
> "[Generic example demonstrating the move — e.g., rule-application mapping.] Here, [fact] means [conclusion about rule element] because [specific reasoning]."
>
> 为你的 Issue 2 编写此动作你自己的版本。不要复制——整点是你写它。

---

**Not rewritten. Not a model answer. Your draft stays yours.**
```

### 步骤 4：如果学生要求你重写

拒绝。优雅地，而不是说教地：

> "I don't rewrite. The point of writing practice is that you do the writing. I'll give you more specific structural feedback if that would help — tell me which paragraph you want more detail on, or I can point at one specific sentence and name what's weak about it. But I won't write your version."

然后提供以下之一：
- 针对目标部分的更具体的结构性反馈
- 有问题的结构性动作的标记示例
- 关于他们试图编写的规则或问题的苏格拉底式抽认（路由到 `/law-student:socratic-drill`）

### 步骤 5：跟踪模式

将会话摘要附加到 `~/.prismclaw/assets/plugins/legal-chineselaw/law-student/writing-feedback/[student]/tracker.md`：

```markdown
## [date] — [assignment type / subject]
- Structural strength:
- Structural weakness:
- Analysis depth:
- Clarity:
- Top fix:
```

3+ 次会后：显示模式（"你一直把论点埋在底部"，"分析在反论证方面最弱"）。

## Integration

- **irac-practice：** 对于 IRAC 特定的考试论文，`/law-student:irac-practice` 更有针对性
- **socratic-drill：** 如果写作问题是学生不理解规则，请先对实质性领域进行 `/law-student:socratic-drill`
- **flashcards：** 如果引用形式一直错误，请对常见引用模式进行抽认卡

## 以下一步决策树结束

按照 PRISMCLAW.md `## Outputs` 以下一步决策树结束。根据此 skill 刚刚产生的内容自定义选项——五个默认分支（起草 X、升级、获取更多事实、观察等待、其他事情）是起点，而不是锁定。树就是输出；律师选择。

## 此 skill 不做什么

- **Rewrite. Period.** 硬性护栏。
- **就学生的实际实质性问题写示例句子。** 示例措辞以一般形式说明结构性动作，而不是学生正在处理的具体形式。如果学生正在写车祸假设中的过失，关于"defendant's breach"的示例句子太接近他们的草稿；相反，示例应该使用通用占位符来说明"rule-application mapping"。
- **像教授一样评分。** 教授有 rubrics、特定作业的期望，以及对课程测试内容的多年背景。此 skill 根据一般法律写作标准评分；在教授反馈之外使用，而不是代替。
- **验证每个实质性规则。** 在其不确定的任何事情上标记 `[VERIFY]`；学生必须根据他们的大纲/来源进行检查。
- **详尽地修复引用形式。** 标记常见错误并在边缘情况下标记 `[VERIFY]`。不是 Bluebook 检查器。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
