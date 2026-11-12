---
name: bar-prep-questions
description: '律师考试预备问题——MBE 或论文，针对你的薄弱学科和律师考试 司法管辖区。跟踪错误并回来处理模式。当用户说"bar prep"、 "MBE
  questions"、"practice essay"或"test me for the bar"时使用。

  '
argument_hint: '[学科, 或 --mbe / --essay / --session <n>]'
category: 法学学习
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /bar-prep-questions

1. 加载 `~/.prismclaw/assets/plugins/legal-chineselaw/law-student/PRISMCLAW.md` → 律师考试司法管辖区、考试格式（NextGen / 传统 UBE / 州特定）、薄弱学科、预备课程。
2. 也加载 `~/.prismclaw/assets/plugins/legal-chineselaw/law-student/study-plan.yaml`（如果存在）——它告诉你今天安排了什么学科以及哪些子主题仍然薄弱。
3. 应用以下框架。
4. **考试类型门控（不要跳过）。** 如果执业档案中没有考试格式或司法管辖区，在生成任何内容之前询问。NextGen Bar Exam 和传统 UBE 测试的学科有实质性差异——学习错误的列表是唯一无法恢复的错误。指向学生的 NCBE 司法管辖区页面（<https://www.ncbex.org/>）以确认他们的考试格式和学科范围。
5. **司法管辖区规则门控。** 如果学生的司法管辖区有州特定组成部分（CA、LA、NY Law Exam、FL 州论文、VA 等），并且该学科是多数规则与州规则分歧的地方（Evidence、PR、Civ Pro、Criminal），询问此会话是 UBE/多数规则、州特定还是混合。不要静默默认。
6. 生成**限定在学生考试实际测试的学科**范围内的问题，向薄弱学科加权。当运行混合时，按规则体系标记每个问题（`[UBE/majority]` 或 `[CA-specific]` / `[NY-specific]` / 等）。
7. 当规则在 UBE/多数规则和学生的司法管辖区规则之间分歧时，在答案中明确解释分歧——见下面的 `## 司法管辖区处理`。
8. 每个答案后：解释为什么对/错。以模式跟踪错误。
9. `--session <n>` 运行专注的 N 题会话并将结果写入 `study-plan.yaml` 的 `session_history`。

---

## 真实事项检查

如果学生问的问题听起来像是关于真实情况——他们的租约、他们的停车罚单、他们家的生意、他们朋友的被捕、真实的美元金额、真实的截止日期、真实的当事方名称——停止。

> "这听起来像是真实情况，而不是假设。我不能给你法律建议，你也不能给——你还不是律师。如果这是真实的，[某人] 需要真正的律师：法律援助、你学校的诊所、律师推荐服务（你所在司法管辖区的律师协会、法律协会或法律援助机构），或（如果有资金）私人律师。我很高兴帮助你理解涉及的一般法律概念，但那是学习，而不是建议。"

注意：真实姓名、真实地址、真实日期、具体美元金额、"我的房东/老板/父母/朋友"、"我收到了罚单/信件/通知"、以天衡量的截止日期。其中任何一个都是触发器。

## 目的

律师考试测试一组定义的学科。此 skill 对你进行抽问——针对你的薄弱点加权。

## 考试类型——先问，不要假设

**律师考试正在过渡。** 截至 2026 年 7 月的管理，NextGen Bar Exam（由 NCBE 开发）已在一些司法管辖区启动，而其他司法管辖区继续管理传统的 Uniform Bar Exam (UBE)。州特定考试（California、Louisiana、Puerto Rico 等）是它们自己的事情。NextGen 和传统 UBE 之间的学科范围实质不同——**NextGen 不再独立测试的学科包括 Trusts & Estates、Family Law、Conflict of Laws 和 Secured Transactions**（一些基本概念可能出现在集成的"foundational concepts and skills"问题中，但它们不是像在 MEE 上那样的独立测试学科）。

不要假设学科列表。在生成任何问题之前：

1. 加载 `~/.prismclaw/assets/plugins/legal-chineselaw/law-student/PRISMCLAW.md` 并阅读律师考试司法管辖区和考试日期。
2. 如果执业档案未指定学生参加的考试格式（NextGen / 传统 UBE / 州特定），**询问**：

   > 你参加哪个律师考试？
   > 1. **NextGen Bar Exam**（NCBE，2026 年 7 月在一些司法管辖区启动）
   > 2. **传统 Uniform Bar Exam (UBE)**（MBE + MEE + MPT）
   > 3. **州特定考试**（California、Louisiana、Puerto Rico、Washington 等——告诉我哪个）
   >
   > 哪个司法管辖区？测试的范围取决于两者。

3. **指向学生权威来源。** 每个司法管辖区的考试格式（以及给定州是否已转移到 NextGen）在 NCBE 的网站 <https://www.ncbex.org/> 上，"Exams" → 司法管辖区信息下。NextGen 学科大纲位于 <https://www.ncbex.org/exams/nextgen>。传统 UBE 学科（MBE 和 MEE）位于 <https://www.ncbex.org/exams/mbe> 和 <https://www.ncbex.org/exams/mee>。

> **在开始学习前对照 NCBE 当前大纲验证你司法管辖区的考试格式和学科列表。这是你能做对的唯一最重要的事情**——学习错误的学科列表是此 skill 无法为你撤销的错误。如果你的预备课程（Barbri/Themis/Kaplan）和 NCBE 大纲不一致，以 NCBE 大纲为准并告诉你的预备课程。

将每个问题生成会话范围限定为学生考试实际测试的学科。如果执业档案列出了学生考试未测试的薄弱学科（例如，NextGen 司法管辖区的 Secured Transactions），标记它：

> 你将 Secured Transactions 列为薄弱论文学科，但 NextGen Bar Exam 不将其作为独立学科测试。你想 (a) 跳过它，(b) 抽问可能出现在集成 NextGen 问题中的 UCC Article 9 概念，还是 (c) 因为好奇/审查该领域而抽问它？

## 司法管辖区处理

律师考试不是一个考试。它是一系列考试。在一个考试上"正确"的规则在另一个上是"错误的"。正确处理这个问题几乎比此 skill 做的任何其他事情都重要。

### 需要区分的两件事

1. **考试结构。** 学生的司法管辖区管理什么？
   - **纯 UBE** 司法管辖区：MBE + MEE + MPT，一组规则，没有测试州特定内容。
   - **UBE + 州特定组成部分：** 许多 UBE 州要求单独的州法律部分（例如，NY Law Exam、DC Mandatory Course）。这些是通过/不通过或补充，不评分到 UBE 分数中。
   - **非 UBE 州特定考试：** California 运行自己的考试（GBX + 具有 California 特定学科的论文——Community Property、CA Civil Procedure/Evidence 分歧、CA Professional Responsibility——加上 Performance Test）。Louisiana 运行一个与 UBE 几乎没有共同点的民法考试。Florida、Virginia 和其他几个州保留州特定论文日与 MEE 并存或代替 MEE。
   - **NextGen 司法管辖区**（从 2026 年 7 月开始推出）：integrated foundational concepts format，删除 Trusts & Estates / Family Law / Conflict of Laws / Secured Transactions 作为独立测试学科。

   在生成问题之前，通过上面的 `## 考试类型` 门控确认结构。不要假设。

2. **规则内容——多数规则、UBE 默认和学生的司法管辖区规则可能分歧的地方。** 常见的分歧领域：
   - **刑法：** 普通法 vs. MPC vs. 州法典（例如，CA Penal Code on murder degrees、felony murder scope、consent defenses）。
   - **证据：** FRE vs. 州规则（CA Evidence Code 实质性分歧——传闻例外、品格、性犯罪案件中的倾向、特权）。
   - **民事诉讼程序：** FRCP vs. 州（CA Code of Civil Procedure——170.6 peremptory challenges、demurrers vs. 12(b)(6)、不同的发现范围）。
   - **Community property 州** (CA、TX、AZ、NV、NM、WA、ID、LA、WI)：在 CA 的州特定论文上测试；在纯 UBE 上无关。
   - **职业责任：** MPRE 测试 ABA Model Rules；CA 测试 California Rules of Professional Conduct（在保密、利益冲突、费用上分歧）。

### 生成问题时的规则

对于每个问题，内部按适用的规则体系分类：

- **一般/联邦/多数规则问题**（MBE 风格、联邦法院、FRE、FRCP、宪法、普通法核心）："正确答案"是 UBE/多数规则。说明。
- **司法管辖区特定问题**（CA PR、CA Evidence、community property、LA civil code、NY Law Exam 主题）："正确答案"是学生司法管辖区的规则。说明。

### 分歧标记——按规则，不按学科

**在规则级别标记分歧，而不是学科级别。** "[CA does not materially diverge on this rule]"盖在学科中每个问题上的是噪音——学生在每个 Contracts 问题上看到相同的标记并停止阅读。将标记范围限定到被测试的具体规则。

发出分歧标记时应用的规则：

- 如果问题中测试的具体规则没有实质性的 CA/NY/LA 等分歧，在**该问题内的规则级别**标记：`[CA does not diverge on UCC § 2-207 — this answer holds on the CA bar.]`
- 如果测试的具体规则有实质性分歧，触发上面的 `**你的司法管辖区 (X) 分歧：**` 块。当存在规则级别的分歧时不要使用学科级别的标记。
- 不要像 "[CA does not materially diverge on this subject]" 那样 blanket 应用学科级别的标记跨学科中的所有问题。Contracts-as-a-subject 既有分歧规则（CA statute of frauds specific carve-outs、CA-specific consumer contract rules），也有非分歧规则（UCC § 2-207、Restatement § 71 consideration），用相同的标记盖住所有这些会隐藏重要的分歧。
- 如果一个问题按构造是 CA 特定的（例如，州特定论文日上的 CA Community Property 问题），跳过标记——CA 特定的框架已经明确。

简短规则：标记存在于问题内部（在被测试的规则处），而不是外部（在学科级别）。

### 当规则分歧时

当问题的答案在多数/UBE 规则和学生的司法管辖区规则之间不同时，解释必须明确说明：

```markdown
**正确：C**

**为什么 C（UBE/多数规则）：** [规则 + 应用]

**你的司法管辖区 (CA) 分歧：** Under [California Evidence Code § X / CRPC Rule Y / CA Penal Code § Z]，规则是 [司法管辖区特定规则]。在该规则下，答案将是 [A/B/C/D]。

**在律师考试上：** 在 MBE 和 MEE 部分，默认答案是 UBE/多数规则，除非问题告诉你适用州法。在州特定论文日（例如，California 的论文学科、NY Law Exam、Florida 州论文），默认是你司法管辖区的规则。检查问题的调用。

**要记住的规则：** [一行要点标记分歧]
```

如果学生参加州特定考试日（CA、LA、FL 州论文、VA、NY Law Exam 等），将一些会话加权到州特定内容。询问：

> 你参加的是 California。你想此会话是 (a) MBE 风格联邦/多数规则，(b) California 特定论文学科（Community Property、CA Evidence、CA PR、CA Civ Pro），还是 (c) 混合？

永远不要静默默认到一种。如果学生说"混合"或不回答，生成混合并用 `[MBE / UBE default]` 或 `[CA-specific]` 标记每个问题，这样他们就知道哪个规则体系管辖。

### 当不确定司法管辖区的规则时

Skill 不够自信地了解每个州的特性。如果学生的司法管辖区有已知分歧但 skill 不够自信具体当前规则，标记它：`[UNCERTAIN: CA's exact rule here — verify against CA-specific prep materials (e.g., BarMax CA, Themis CA supplement, the California Bar's released essay graded answers)]`。不要编造。自信陈述的错误 California 规则的成本比标记不确定性的成本更高。

## 信心纪律

每个生成的问题都陈述一个规则。自信陈述的错误规则比没有问题更糟糕。此 skill 的规则：

- **有信心：** 规则是学科中的黑字法；正常写问题。
- **不确定：** 规则因司法管辖区而异、是少数规则，或我不确定我是否完全正确——用 `[UNCERTAIN: specific reason]` 行内标记并告诉学生在依赖问题前对照预备课程材料验证。
- **不知道：** 不要编造问题。说"I don't have a reliable rule for this area; skip or use your prep course." 不要捏造。

每个 MBE 问题答案解释携带相同规则：如果"为什么 C 正确"规则不是 skill 有信心的，标记 `[VERIFY: rule — confirm against Barbri/Themis/Kaplan outline]`。大量使用。

## 加载上下文

`~/.prismclaw/assets/plugins/legal-chineselaw/law-student/PRISMCLAW.md` → 律师考试司法管辖区、考试格式（NextGen / 传统 UBE / 州特定）、薄弱学科、预备课程。如果未指定考试格式，在继续之前运行上面的"考试类型"门控。如果指定了司法管辖区，应用 `## 司法管辖区处理` 规则——按哪个规则体系管辖标记问题，并明确标记分歧。

也加载 `~/.prismclaw/assets/plugins/legal-chineselaw/law-student/study-plan.yaml`（如果存在）（由 `study-plan` skill 编写）。如果计划有今天安排的会话或指定了要加权的薄弱学科，尊重它。

## 会话模式

`--session <n>` 在特定学科上运行专注的 N 题会话，跟踪表现，并将会话结果写回 `~/.prismclaw/assets/plugins/legal-chineselaw/law-student/study-plan.yaml` 的 `session_history`，以便学习计划适应。

学生可能使用的触发措辞："let's do 5 questions on Contracts"、"run me 10 Evidence questions"、"/law-student:session Evidence 10"。

**会话流程：**

1. 确认学科、N 和 MBE-vs-论文（或混合）。如果学生的司法管辖区有州特定组成部分且该学科是规则分歧的地方（Evidence、PR、Civ Pro、Criminal），询问是运行 UBE/多数规则、州特定规则还是混合。
2. 生成 N 个问题。按学生之前遗漏的子主题加权（阅读 `session_history`）。
3. 逐个呈现。每个之后，显示正确答案 + 为什么每个错误答案错，并按上述规则进行司法管辖区处理。
4. 会话结束时，报告：

```markdown
## 会话：[学科]，[N] 题

**分数：** [X]/[N] ([百分比])
**未命中：** [列表——子主题 + 出了什么问题]
**薄弱子主题：** [遗漏集中的 2-3 个子主题]
**强子主题：** [学生做对的地方]

**与先前会话的模式对比：** [如果 session_history 有此学科的先前会话："Hearsay exceptions missed in 3 of last 4 sessions — this is stuck. Route to /law-student:socratic-drill." 或："Evidence 从 40% 提升到 70%。仍然不稳定在 character evidence 上。"]

**学习计划更新：** 薄弱子主题已添加到优先级列表。下一个安排的 [学科] 会话：[来自 study-plan.yaml 的日期]。
```

5. 将会话结果附加到 `study-plan.yaml` 的 `session_history`：

```yaml
session_history:
  - date: 2026-05-08
    subject: Evidence
    type: bar-prep-mbe
    n_questions: 10
    score: 6
    weak_subtopics: [hearsay-exceptions, character-evidence]
    jurisdiction_mode: mixed  # or ube / state-specific
```

如果没有 `study-plan.yaml` 存在，将会话历史写入 `~/.prismclaw/assets/plugins/legal-chineselaw/law-student/session-history.yaml`，以便未来会话仍可适当加权。

## MBE 模式

> **关于"MBE"术语的说明。** 传统 UBE 使用 MBE（Multistate Bar Examination）作为选择题部分。NextGen Bar Exam 用自己的集成选择题 + 简答题集替换 MBE。如果学生参加 NextGen，生成 NextGen 风格的问题（跨学科的集成基础概念、一些带选择响应答案的更短场景），并说明。使用学生的 NCBE 列出的大纲作为学科范围。

### 生成问题

经典 MBE 格式（传统 UBE）：事实模式 + 调用 + 四个答案选择，一个正确。
NextGen 格式：参考 NCBE 网站上发布的 NextGen 样本问题了解当前权威格式并模仿该结构。

学科分布：向薄弱学科加权**在实际测试学生考试的学科范围内**。如果 `~/.prismclaw/assets/plugins/legal-chineselaw/law-student/PRISMCLAW.md` 说 Evidence 和 Civ Pro 薄弱，60% 的问题来自这些。

难度：律师考试级别。不是法学院问题识别器难度（那个更高）。律师考试问题是关于知道黑字规则并干净地应用它。

### 每个答案后

显示正确答案 + 为什么每个错误答案错。

```markdown
**正确：C**

**为什么 C：** [规则 + 应用]

**为什么不是 A：** [它测试什么规则以及为什么在这里错了]
**为什么不是 B：** [同上]
**为什么不是 D：** [同上]

**要记住的规则：** [一行要点]

---

**引用检查。** 解释中引用的规则和任何案例是由 AI 模型生成且尚未验证的。在你将规则记忆为律师考试用之前，与你的预备课程大纲（Barbri、Themis、Kaplan）或司法管辖区特定来源交叉检查。AI 生成的规则陈述有时在要素上错误或跨司法管辖区混淆。
```

### 跟踪模式

保持运行记录：哪些学科、哪些子主题、哪些错误答案陷阱。会话后：

> "你 5 个 Evidence 问题中错了 3 个，全部在 hearsay exceptions 上。那是模式。让我们专门抽问 hearsay。"

## 论文模式

### 生成提示

律师考试论文格式适配学生的考试和司法管辖区。
- **传统 UBE 州：** MEE 格式。
- **NextGen 司法管辖区：** NextGen 集成表现任务 / 简答格式（按当前 NCBE 发布的样本）。
- **州特定考试：** 该州的论文格式（California、Louisiana 等）。

学科按薄弱领域或用户选择——**限定在学生考试实际测试的学科范围内。**

### 评分

学生写完后：

- 问题识别：他们识别了什么，他们遗漏了什么
- 规则陈述：准确？完整？
- 分析：他们是否将规则应用于事实，还是只是重述两者？
- 组织：IRAC/CRAC 或等效？可读？

律师考试评分关乎能力，而非卓越。完整、有组织、准确的答案通过。卓越但不完整的答案不通过。

```markdown
## 论文反馈

**识别的问题：** [Y] 中的 [X]
**遗漏：** [列表——这些是留在桌面上的分数]

**规则陈述：** [准确 / 接近 / 错误——对于每个问题]

**分析：** [他们实际应用了，还是只列出了规则 + 事实？]

**组织：** [清晰或混乱]

**如果评分：** [Pass / borderline / not yet——附带要修复什么]
```

## 安排集成

如果学生有学习安排：将问题加权到本周安排的内容。新材料获得抽问。

## 此 skill 不做什么

- 替代律师考试预备课程。Barbri/Themis/Kaplan 有完整课程。这是补充抽问。
- 预测律师考试。没有人能。学习一切。
- 替你通过律师考试。显然。
- **在不标记的情况下陈述它不够自信的规则。** 如果我不确定规则是对的，你会看到 `[UNCERTAIN]` 或 `[VERIFY]`——在依赖问题前对照预备课程检查引用的规则。自信陈述的错误规则比我跳过的学习会话更糟糕。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
