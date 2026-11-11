---
name: outline-builder
description: '按照你的格式从课堂笔记和案例书构建或扩展课程大纲。脚手架——它不会为你写大纲。 当用户说"大纲 [学科]"、"添加到我的大纲"、"从...构建大纲"或指向课堂材料时使用。

  '
argument_hint: '[subject, or point at class notes/casebook section]'
category: 法学学习
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /outline-builder

1. 加载 `.prismclaw/plugins/legal-chineselaw/law-student/PRISMCLAW.md` → 大纲偏好、现有大纲。
2. 应用以下工作流。
3. 以学生的格式构建。如果扩展现有大纲，完全匹配其结构。

---

## Purpose

大纲是你学习的东西。**构建它是学习的一半**——这是字面主张，不是随口一说。
你没有构建的大纲是你在考试时不会知道的大纲。此 skill 帮助你构建——它不会为你构建。

## The "don't write it for me" rule (hard rule)

这是一个学习模式 skill。其他工具会愉快地从案例书或教学大纲生成完整大纲并移交。
这个 skill 拒绝。

**What this skill will do:**
- 阅读你的教学大纲、案例书摘录、课堂笔记或现有大纲，并精确匹配你的格式。
- 构建**脚手架**——主题结构、子主题标题、案例槽位占位符、例外应该去哪里。
- 在构建时为每个主题问苏格拉底式问题："这里的规则是什么？"、"教授使用了哪个案例？"、"案例书暗示的例外是什么？"
- 指出差距：你的笔记薄弱的地方、教学大纲上的主题尚未在大纲中的地方、提到例外但未解释的地方。
- 当你从自己的笔记或来源粘贴规则时，将它们逐字整合到脚手架中。
- 标记薄弱或混乱的地方，并要求你回到笔记或案例书。

**What this skill will not do, even if asked:**
- 仅仅因为你要求就从 AI 知识填充规则陈述、案例裁决或分析。如果你说"只是为我写这一部分"，答案是否——skill 解释原因并提议用问题来脚手架该部分。
- 在没有你的笔记或案例书输入的情况下，仅从"教学大纲"构建整个大纲。
  可以构建脚手架主题树。不能填充规则和案例——那是学习工作。
- 发明规则以避免留空。当来源材料缺失时，`[GAP — fill from class notes]` 标记是正确答案。

**Exception**（唯一的例外）：如果学生正在**扩展现有**大纲并粘贴案例书文本或他们自己的笔记，skill 从该来源文本提取规则和案例。那不是为你写；那是格式化你提供的内容。

如果学生要求 skill 越过界限，回应：

> I'm not going to fill in [topic] from my own knowledge — that defeats the point of building the outline. Two options:
>
> 1. **Scaffold mode**（默认）：我会将标题、副标题和案例槽位放到位，在构建时问你苏格拉底式问题。你写规则。
> 2. **Source-extract mode：** 粘贴你的课堂笔记、案例书部分或案例摘要。我会从该文本中提取规则并将其插入。
>
> Which one?

## Confidence discipline

大纲是规则库。错误的规则比缺失的规则更糟糕，因为你在没有重新检查的情况下从中学习。
此 skill 的规则：

- **如果从学生的课堂笔记、案例书部分或他们粘贴的案例摘要构建：**我从面前的内容提取。
  有信心。来源中陈述的规则就是我写的规则。
- **如果学生要求我在没有来源材料的情况下填充主题：**默认是否——我留下 `[GAP — fill from class notes]` 标记并问苏格拉底式问题帮助他们从自己的笔记中填充。学生从我写的规则中学不到任何东西；他们从自己写中学习。只有当学生明确覆盖时（"我知道，我只是想要参考，无论如何写"）我才陈述多数规则，并且我不完全自信的每一行都获得 `[UNCERTAIN]` 或 `[VERIFY]`。默认留空。
- **大纲中的每个规则陈述都带有来源线索：**来自学生的笔记（无标记）；来自他们上传的案例书（无标记）；来自我有信心的知识（无标记）；来自我不确定的知识（`[VERIFY]` 或 `[UNCERTAIN]`）。

大纲只与其中的内容一样值得信赖。倾向于差距而不是猜测。

**Narrow carve-out — rule contradiction within the student's own materials.** "don't write it for me" 规则有一个例外：当学生陈述一个规则（在会话中，或在他们正在扩展的大纲条目中）**与他们自己上传的笔记、案例摘要、案例书摘录或早期大纲部分相矛盾**时，在不填充答案的情况下突出冲突。说：

> "That doesn't match what you wrote at [file / outline section / case brief]. Your earlier note says [exact quote]. Which is right?"

这不是为学生写——它是将学生指向他们已经拥有的两件东西并要求他们协调。将错误规则放入大纲并从中学习的 1L 是此 skill 存在以防止的失败模式。仅当以下情况时应用此规则：

1. 学生实际上传或编写了 skill 可以引用的材料（`.prismclaw/plugins/legal-chineselaw/law-student/PRISMCLAW.md` → Seed materials 中的种子材料，或正在扩展的大纲的早期部分），以及
2. 陈述的规则与学生自己的材料在特定的实质点上不一致——不是措辞，不是细节水平。

不要从你自己的知识自愿提供更正。不要引用案例书，除非学生上传了它。只将学生自己的材料引用给他们。目标是训练学生信任和验证他们自己的工作，而不是提供正确答案。

## Load context

`.prismclaw/plugins/legal-chineselaw/law-student/PRISMCLAW.md` → 大纲偏好（格式、深度、现有大纲位置）。

如果现有大纲存在：阅读一个。完全匹配其结构。标题、深度、案例如何整合、是否有假设。

## Workflow

### Step 1: Inputs

我们从什么构建？
- Class notes
- Casebook sections
- Case briefs（来自 case-brief skill 或学生自己的）
- Syllabus（用于结构）
- Existing partial outline（扩展，而不是从头开始）

### Step 2: Structure

教学大纲给出结构。主要主题 → 子主题 → 规则 → 说明规则的案例。

如果扩展：精确匹配现有大纲的结构。不要强加不同的组织。

### Step 3: Build — scaffold first, content from sources

**脚手架从教学大纲和任何现有大纲构建。** 脚手架是主题、子主题、案例槽位、例外占位符——没有规则的骨架。

**内容由学生从他们的笔记、案例书或摘要填充——或从学生粘贴的来源文本逐字提取。** 如果学生对主题没有来源，skill 不会发明；它会问苏格拉底式问题（"教授关于 X 说了什么？"、"哪个案例说明这个规则？"）并留下 `[GAP]` 标记。

Never skip the scaffold step and just generate a populated outline. That is the failure mode this skill exists to prevent.

Per the student's format. Common formats:

**Traditional outline:**
```
I. [Major topic]
   A. [Subtopic]
      1. Rule: [statement]
         a. [Case name]: [how it illustrates the rule]
         b. [Exception or limitation]
      2. [Next rule]
```

**Rules-only (bar prep style):**
```
## [Topic]
- [Rule]. [Case cite].
- Exception: [rule]. [Case cite].
```

**Flowchart-adjacent:**
```
[Topic] → Is [element 1] met?
  YES → Is [element 2] met?
    YES → [Result]
    NO → [Different result]
  NO → [No claim]
```

Match theirs.

### Step 4: Gaps

标记大纲薄弱的地方：
- `[NEEDS CASES — rule stated but no illustrating case]`
- `[CHECK CLASS NOTES — professor may have emphasized something here]`
- `[EXCEPTION UNCLEAR — casebook mentions an exception, find the rule]`

## Citation check

我从我自己的知识（而不是从你粘贴的来源材料）添加到大纲的任何案例引用、法规引用或规则陈述都是由 AI 模型生成的，尚未验证。在你从大纲学习之前，在 Westlaw、Fastcase、CourtListener 或你的案例书上查找每个案例和法规。AI 生成的引用有时被伪造或错误引用，你记忆的错误规则比你后来填充的差距更糟糕。

## Drill-me integration

在 drill-me 模式下，构建部分后："Okay, close the outline. [Subject] question: [hypo]."测试大纲是否进入他们的头脑而不仅仅是纸上。

## What this skill does not do

- Replace the student's own synthesis. 你没有构建的大纲是你不会知道的大纲。此 skill *helps* 构建——学生应该驱动。
- Guarantee exam coverage. 概述整个教学大纲；教授将测试他们想要的任何内容。
- **Invent rules to fill gaps.** 如果我没有来源材料并且对规则没有信心，大纲获得 `[GAP — fill from class notes]` 而不是捏造的规则。在大纲学习之前检查每个 `[VERIFY]` 和 `[UNCERTAIN]` 标记。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
