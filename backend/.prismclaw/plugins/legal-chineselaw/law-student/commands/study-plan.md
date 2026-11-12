---
name: study-plan
description: '构建或更新长期律师考试（或期末考试）学习计划——阶段划分、 按薄弱程度加权的学科、每日学习会安排、根据 study-plan.yaml
  中的会话历史自适应。当用户说"build a study plan"、 "plan my bar prep"、"schedule my studying"或 "how
  should I study for [X]"时使用。

  '
argument_hint: '[--build | --update | --status | --cram]'
category: 法学学习
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /study-plan

1. 加载 `~/.prismclaw/assets/plugins/legal-chineselaw/law-student/PRISMCLAW.md` → 律师考试司法管辖区、考试格式、考试日期、薄弱学科、目标学习小时/天、预备课程。
2. 加载 `~/.prismclaw/assets/plugins/legal-chineselaw/law-student/study-plan.yaml`（如果存在）。
3. 应用以下框架。
4. 按标志路由：
   - `--build`（无计划存在时的默认值）：走一遍输入门控（考试、学科、小时/周、休息日、方法）。构建阶段结构 + 前两周的每日安排。写入 `study-plan.yaml`。
   - `--update`（有计划存在时的默认值）：重新阅读 `session_history`，调整学科优先级和每周小时，填写下一段每日安排。
   - `--status`：今天/本周安排了什么、分数趋势、正在滑落的学科、每个学科的下一次安排会话。
   - `--cram`：强制进入冲刺模式——80/20 高收益优先、每日 MBE 量、最后 2-3 天逐步减少。
5. 写入前：以散文形式总结计划并与学生确认。根据他们的答案调整。
6. 始终根据学生陈述的生活约束对小时/周进行合理性检查。过于雄心勃勃的计划会失败。

---

## 目的

坐下来学习却不知道学习什么就是几周消失的方式。此 skill 构建一个计划——到考试的周数、每天的学习会、每周的学科、会话类型——然后随着学生实际进行会话而适应。它是一个活的计划，而不是日历导出。

它还为下游 skills（bar-prep、flashcards、drill、irac）提供了一个共享的 schedule 来遵守，这样学生每次打开会话时都不会被问"你今天想学习什么"。

## 信心纪律

计划是意见，而不是教条。Skill 明确说明什么是估计：

- **每个主题的时间估计**是一般指导（基于典型的 Barbri/Themis/Kaplan 权重）。将它们标记为估计——学生的实际节奏会有所不同。
- **学科权重**源于学生自己报告的薄弱学科和会话历史。有信心。
- **冲刺模式中的高收益主题优先**基于多年的律师考试发布模式（MBE/MEE 学科频率）。将任何"这肯定在考试上"的主张标记为 `[UNCERTAIN — past frequency is not a prediction]`。

## 加载上下文

`~/.prismclaw/assets/plugins/legal-chineselaw/law-student/PRISMCLAW.md`：
- 律师考试司法管辖区、考试格式、考试日期
- 当前课程（用于非律师考试）
- 薄弱学科（MBE、论文）
- 预备课程
- 目标学习小时/天

`~/.prismclaw/assets/plugins/legal-chineselaw/law-student/study-plan.yaml`（如果存在）——扩展，不要覆盖。

## 工作流

### 步骤 1：我们在为什么做计划

> 我们在为什么做计划？
>
> 1. **律师考试**（你有一个考试日期）
> 2. **特定的法学院考试或一组期末考试**
> 3. **一般学期学习节奏**（大纲、阅读、抽问跨所有课程）

对于 (1) 律师考试：从执业档案阅读考试日期，确认。如果未捕获考试日期，询问。
对于 (2) 法学院考试：询问哪门课、什么日期、什么格式。
对于 (3) 学期：询问学期结束日期作为锚点。

### 步骤 2：输入——一次一个，等待每个

**询问并等待。** 不要将所有问题合并到一个提示中然后继续。

- **考试日期：** 已确认？（如果是律师考试：如果不在执业档案中则询问司法管辖区——学习内容取决于它。）
- **要覆盖的学科：** 对于律师考试，从考试格式的 NCBE 学科大纲阅读（NextGen / 传统 UBE / 州特定）。对于课程，教学大纲。与学生确认——"有什么学科我应该添加或删除吗？"
- **最强的学科：** 最低优先级。仍然复习，不大量抽问。
- **最薄弱的学科：** 最高优先级。获得更多会话。
- **每周可用小时：** 现实的，不是理想的。"我可以做 20 小时"与"我将在 8 周内做 20 小时"不同。询问他们实际能维持什么。
- **生活情境合理性检查——强制执行。** 在学生给出一个数字后，询问（一次一个问题——不要跳过）：

  > 你说每周 [N] 小时。在我构建之前，告诉我你一周中还有什么——工作（小时/周）、家庭（孩子、照护）、通勤、锻炼、治疗、诊所、任何有意义的事情。计划应该适合你的生活，而不是反过来。你无法遵循的计划比更轻的你 能遵循的计划更糟糕。

  等待答案。然后根据他们报告的负荷对陈述的小时进行合理性检查：

  > 那是跨 [N] 个学习日每天约 [X] 小时，加上 [工作 + 家庭 + 通勤 + 其他]。按我的经验，这是 [现实的 / 紧张的 / 不可持续的]。想在构建前调整小时/周目标，还是保持它们看看第一周怎么样？

  不要跳过此步骤，即使执业档案的目标小时数已在冷启动时捕获。档案捕获学生说了什么；生活情境检查捕获它是否可持续。如果检查产生更低的数字，使用更低的数字并在 `confidence_flags` 块中注明调整。

  如果学生拒绝分享生活情境（"just build it"），尊重——但添加 `confidence_flags` 条目："Life-context check declined; plan assumes [N] hours/week is sustainable. Revisit at end of week 2 if adherence is below [X]%."
- **偏好的学习方法：** 多选。MBE 练习 / 论文 / 抽认卡 / 大纲 / 抽问 / 重新阅读。将安排加权到他们说他们会实际做的方法。
- **每周休息天数：** 休息日很重要。安排 7/7 天的计划在第 3 周失败。

### 步骤 2.5：补充 vs. 替代（预备课程用户）

如果 `~/.prismclaw/assets/plugins/legal-chineselaw/law-student/PRISMCLAW.md` → `Prep course` 是 **Barbri**、**Themis**、**Kaplan** 或任何其他结构化预备课程（即不是 `self` 或 `N/A`），学生已经有预备课程日历。此 skill 的计划必须选择两个角色之一——它不能在预备课程旁边运行完整的平行课程而不让学生筋疲力尽。

询问，一个问题，等待：

> 你的档案说你在使用 [Barbri / Themis / Kaplan]。他们发布每日日历，安排了每个学科和任务。此计划可以有两种工作方式——选择一种：
>
> 1. **补充。** 预备课程是你的主要课程。此计划填补缺口：针对薄弱学科的额外 MBE 抽问、有针对性的论文练习、你遗漏的主题的抽认卡循环。我不会重建预备课程日历；我会叠加在上面。
> 2. **替代。** 你不遵循预备课程日历（可能因为它的节奏不适合你的生活）。我将构建整个计划——学科、小时、阶段、安排——你放弃预备课程日历。
>
> 不要选两者。同时运行两个完整课程是学生在第 4 周爆炸的方式。

等待答案。在 yaml 中记录为 `prep_course_mode: supplement | replace`。

如果 **补充**：计划的每日安排更轻——它只添加薄弱学科抽问和有针对性的练习，不重复预备课程覆盖。在 `confidence_flags` 中标记："Supplement mode — this plan assumes you're on track with [prep course] for primary coverage. If you fall behind on the prep course, tell me and we'll re-plan."

如果 **替代**：按下面指定的方式构建完整计划。

如果学生的预备课程是 `self` 或 `N/A`，跳过此步骤——没有什么要补充的。

### 步骤 3：构建安排

从今天日期计算到考试的周数。然后：

**正常模式（距离 4+ 周）：**
- 将周数划分为阶段：
  - **学习阶段**（前约 60% 时间）：每约 3-5 天一个学科，混合大纲/阅读与抽认卡和少量 MBE/论文问题。
  - **抽问阶段**（接下来约 30%）：更多 MBE 量、更多论文练习、模拟条件、所有学科轮换。
  - **复习阶段**（最后约 10%）：专注于 session_history 中最薄弱的子主题、完整模拟考试、强领域的轻量复习。
- 按薄弱程度加权学科：薄弱学科获得约 2 倍于强学科的小时。
- 逐日安排：哪个学科、哪个方法、多长时间。为学生实际生活留出余量。

**冲刺模式（距离不到 4 周）：**
- 标记："你距离不到四周。这是冲刺模式——计划优先高收益主题而非完整覆盖。你将留下缺口。这就是此时此地的权衡。"
- 80/20 优先化：历史上出现最多的 MBE 学科（Civ Pro、Evidence、Con Law、Contracts）获得最大份额。更窄的学科获得最低可行覆盖。
- 每日安排：每天 MBE 块（现在量很重要）、隔日论文练习、每周一次模拟考试。
- 最后 2-3 天睡眠和逐步减少。不要在考试前一天安排硬抽问。这是真实的——通宵冲刺到前一天晚上的人得分更差。

### 步骤 4：写入

写入 `~/.prismclaw/assets/plugins/legal-chineselaw/law-student/study-plan.yaml`：

```yaml
plan_type: bar  # or law-school-exam or semester
exam_date: 2026-07-28
jurisdiction: CA
exam_format: state-specific  # or NextGen / UBE
created: 2026-05-08
last_updated: 2026-05-08
weeks_to_exam: 12
hours_per_week: 25
days_per_week: 6
mode: normal  # or cram
phases:
  - name: learning
    start: 2026-05-08
    end: 2026-06-20
    focus: 大纲、抽认卡、入门 MBE
  - name: drilling
    start: 2026-06-21
    end: 2026-07-18
    focus: MBE 量、论文练习、模拟条件
  - name: review
    start: 2026-07-19
    end: 2026-07-27
    focus: 薄弱子主题复习、完整模拟考试
subjects:
  evidence:
    priority: high  # weak
    weekly_hours: 5
    methods: [mbe, flashcards, essay]
  con-law:
    priority: medium
    weekly_hours: 3
    methods: [mbe, outline-review]
  # etc.
schedule:
  - date: 2026-05-08
    day: Thursday
    sessions:
      - subject: Evidence
        method: outline-review
        duration_min: 90
      - subject: Evidence
        method: mbe
        duration_min: 60
        n_questions: 25
  - date: 2026-05-09
    day: Friday
    sessions:
      - subject: Contracts
        method: flashcards
        duration_min: 45
      - subject: Contracts
        method: essay
        duration_min: 60
  # etc.
session_history: []  # 由 bar-prep、flashcards、drill、irac 在会话完成时附加
```

### 步骤 5：与学生确认

**标题——每次聊天展示和任何与 YAML 一起编写的独立散文格式计划文档上的必需项。** 摘要的第一行（以及任何 `study-plan.md` 伴侣文件的第一行）必须是 plugin 配置 `## Outputs` 中的逐字标题：

```
STUDY NOTES — NOT LEGAL ADVICE
```

标题不进入 YAML 本身（它是数据文件），但它属于你展示给学生的散文摘要和保存的任何人类可读计划文档上。这不是免责声明后记——它是输出的身份。不要省略、改写或重新定位它。

在保存前以散文形式（不是原始 YAML）总结计划，顶部有标题：

> STUDY NOTES — NOT LEGAL ADVICE
>
> 这是我构建的内容。到 [exam] 还有 [X] 周。跨 [Z] 天每周 [Y] 小时。薄弱学科（Evidence、Contracts）获得 2 倍小时。三个阶段：学习到 [date]，抽问到 [date]，最后 [N] 天复习。我已安排了前两周的逐日计划。之后按周分配——我会随着你完成会话填写每日安排，所以计划适应你实际在哪里。
>
> 这感觉对吗？太雄心勃勃了？太轻了？遗漏了学科？

根据答案调整。然后写入。

## 适应计划

每次会话后（通过 bar-prep-questions、flashcards、drill、irac），相应的 skill 附加到 `session_history`：

```yaml
session_history:
  - date: 2026-05-08
    subject: Evidence
    type: bar-prep-mbe
    n_questions: 10
    score: 6
    weak_subtopics: [hearsay-exceptions, character-evidence]
```

在下次 `/law-student:study-plan --update` 运行时（或当任何 skill 检测到计划过时时）：
- 持续低分的学科在 `priority` 和 `weekly_hours` 中提升。
- 学科内的薄弱子主题为该学科的下一次安排会话标记。
- 如果学生落后（安排的会话未出现在历史中），调整：要么压缩覆盖，要么标记缺口并询问。
- 如果学生超前，开放更多时间进行更深入的薄弱学科抽问。

## 模式

`--build`（默认）——新计划
`--update`——重新阅读 session_history 并调整权重，填写即将到来的每日安排
`--status`——今天/本周安排了什么、分数趋势、什么在滑落
`--cram`——即使距离超过 4 周也强制冲刺模式（用户覆盖）

## 集成

- `/law-student:session <subject> <n>` 将结果写入此计划的 `session_history`。
- `/law-student:bar-prep-questions` 阅读计划以了解今天安排了哪个学科。
- `/law-student:flashcards` 可以 `--session <n>`，结果进入计划。
- `/law-student:socratic-drill` 和 `/law-student:irac-practice` 会话完成也附加。

## 此 skill 不做什么

- **保证你通过。** 计划是脚手架。工作在你身上。
- **预测考试。** 冲刺模式使用历史学科频率；高收益 ≠ 保证测试。
- **替代你的预备课程安排。** 如果你在 Barbri/Themis/Kaplan，此计划可以补充——不要同时运行两个完整课程。使用一个作为主要。
- **安排你的生活。** 可用小时是你告诉我的。如果你高估了，计划会在第 2 周断裂。诚实一点。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
