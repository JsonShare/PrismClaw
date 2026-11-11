---
name: semester-handoff
description: '学期末案件交接备忘录——/ramp 的镜像。生成每个案件的过渡备忘录和队列摘要， 以便离开队列将工作干净地交给进入队列。当教授或离开的学生需要结束学期、
  构建过渡备忘录或让毕业/退学的学生离开时使用。

  '
argument_hint: '[--semester=YYYY-term（默认：当前）] [--case=[case_id]（针对单个案件）]'
category: 法律诊所
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /semester-handoff

1. 加载 `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/PRISMCLAW.md` → 诊所档案、学期日期、监督风格。
2. 加载 `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/deadlines.yaml` 和每个案件的 `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/client-comms/[case-id]/log.md`。
3. 使用以下工作流。
4. 将活跃案件列表作为输入（如果诊所没有中央列表则询问）。映射离开 → 进入的所有者。
5. 生成每个案件的交接备忘录 → `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/handoffs/[semester]/[case_id].md`。
6. 生成队列摘要 → `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/handoffs/[semester]/_summary.md`。
7. 根据监督模型路由——正式队列 / 可配置标记 / 更轻触。

---

# 学期交接

## 目的

每个学期，诊所都会失去整个劳动力并重建。`/ramp` 解决了一半问题——它让新队列入职。此 skill 解决另一半：通过生成交接备忘录让离开队列离开，这些备忘录捕获下个学生需要知道的每个活跃案件的信息。

没有这个，案件知识会随学生一起离开。新学生从案件文件和摄取摘要开始，这永远不够。两周时间浪费在重新学习案件上，然后新学生才能做任何有用的事情。客户将重新学习视为倒退——电话在新学生赶上来时无人接听，已经回答的问题再次被问。

## 受众

教授或离开的学生。教授运行它以协调整个队列离开；个别学生可以在学期中过渡（毕业、退学）时在自己的案件上运行它。

## 加载上下文

- `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/PRISMCLAW.md` → 诊所档案、学期、执业领域、监督风格
- `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/deadlines.yaml` → 所有活跃截止期限，按案件分组
- `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/client-comms/[case-id]/log.md`（每个案件）→ 沟通历史
- 诊所维护的案件文件 / 摘要摘要
- 学生名册——进入交接时谁拥有什么

## 工作流

### 步骤 1：识别案件和所有者

- 拉取所有活跃案件（来自摄取记录 + `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/deadlines.yaml` case_ids + client-comms 文件夹）
- 对于每个案件：谁是当前的学生所有者？他们留下还是离开？
- 映射：离开所有者 → 进入所有者（如果已知；否则标记"TBD——教授分配"）

如果诊所不维护中央活跃案件列表，skill 需要一个输入：活跃案件列表。询问。不要猜测。

### 步骤 2：每个案件的交接备忘录

对于每个案件：

```markdown
# 案件交接——[案件名称]——[学期结束]

**案件 ID：** [case_id]
**执业领域：** [area]
**离开学生：** [name]
**进入学生：** [name 或 "TBD"]
**主管律师：** [professor]
**客户：** [name 或客户 ID]

---

## 我们在哪里

[一段话：当前状态。已做了什么，什么待处理，案件去向。如果案件处于自然暂停点或提交之间，请说明。]

## 待处理的截止期限

*从 `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/deadlines.yaml` 拉取。进入学生的第一个工作是确认这些是准确的并已拥有。*

| 到期 | 类型 | 描述 | 备注 |
|---|---|---|---|
| [日期] | [类型] | [一行] | [如果紧张："URGENT——学期开始后 [N] 天内到期"] |

## 已完成的工作

- [本学期的关键行动：摄取、提交、听证会、主要通信]
- [生成的文档——带有指向它们所在位置的指针]

## 未完成的事项

- [待处理的决定：例如，"客户尚未决定是否接受和解提议"]
- [研究差距：例如，"需要确认 [司法管辖区] 是否允许 [补救]"]
- [未解决的通信：例如，"等待对方律师办公室的回复"]

## 客户关系

- [学生联系的频率如何？电话、电子邮件、面对面？]
- [下个学生应该知道的任何关系背景：语言偏好、建立信任的笔记、影响安排的情况]
- [即将进行的计划联系或预约]

## 起草/提交的文档

*指针，不是内容。*

- [日期] [文档类型] —— [路径或文件引用] —— [状态：已提交 / 已起草 / 在审查队列中]

## 沟通历史摘要

*来自 `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/client-comms/[case-id]/log.md`。这里是三行摘要；进入学生阅读完整日志。*

[最近联系模式的简短摘要——例如，"自摄取以来 3 次电话，全部用西班牙语，客户更喜欢晚上。最后联系：2026-04-15，确认听证通知的地址。"]

## 教授给进入学生的标记

*由教授审查添加，在交接备忘录发给进入学生之前。可能包括："这个案件有敏感的家庭动态——在给客户打电话之前仔细阅读摄取"；"客户要求所有邮件发送到 PO 信箱而不是家庭地址"；"这里有一个范围问题我们尚未解决——在第 1 周与我检查。*]

[标记，或"无"]

## 进入学生的第一周优先事项

1. [具体——例如，"在接手案件后 48 小时内给 [客户] 打电话。介绍你自己。确认你已收到案件文件。"]
2. [截止期限驱动——例如，"驱逐投诉的答复于 [日期] 到期。审查离开学生的草稿，修改，提交。"]
3. [知识差距——例如，"在 4/28 状态会议之前阅读离开学生关于可居住性辩护的备忘录。"]

---

**交接准备人：** [离开学生]
**日期：** [YYYY-MM-DD]
**审查人：** [主管律师，如果监督模型适用]
```

### 步骤 3：队列摘要

在所有每个案件的备忘录之后，生成 `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/handoffs/[semester]/_summary.md`：

```markdown
# 队列交接摘要——[学期结束]

**离开学生：** [N]
**进入学生：** [N]
**过渡的活跃案件：** [N]
**学期结束时关闭的案件（无过渡）：** [N]

---

## 过渡

| 案件 | 离开 | 进入 | 执业领域 | 紧急性 |
|---|---|---|---|---|
| [case_id] | [name] | [name 或 TBD] | [area] | [标准 / 学期开始后 2 周内截止期限 / 紧急] |

## 未分配

[进入学生为"TBD"的案件——教授在下学期之前分配]

## 学期开始后 30 天内的截止期限

[从 deadlines.yaml 拉取——这些是新队列立即碰到的案件]

## 给教授的笔记

- [任何引起对学生绩效担忧的案件，标记为更密切监督]
- [离开学生愿意继续咨询的任何案件——例如，想要指导接手的 2L 的毕业 3L]
- [交接中的模式——例如，"六个案件中有三个在前 14 天内有活跃截止期限；考虑在这些执业领域前置 ramp 练习"]
```

### 步骤 4：教授审查（如果监督模型要求）

关闭案件或将其过渡给新学生是一个有后果的行动。门槛是 `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/PRISMCLAW.md` 中 `## Supervision style` 中的监督工作流，由确认许可的主管律师拥有设置的第 0 部分角色检查加强。案件关闭备忘录始终获得教授签字，然后案件在交接文档中标记为关闭，无论监督风格选择如何。

根据 `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/PRISMCLAW.md` 监督风格：

- **正式审查队列：** 每个交接备忘录在发给进入学生之前进入审查队列。教授批准、编辑或返回。
- **可配置标记：** 备忘录带有"CHECK WITH [教授] BEFORE RELYING"——教授非正式审查，学生负责检查。
- **更轻触：** 备忘录带有标准 AI 辅助标签；教授通过现有结构审查。案件关闭备忘录仍会在关闭前路由给教授。

### 步骤 5：交接

审查后，交接备忘录位于 `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/handoffs/[semester]/[case_id].md`。进入学生在下学期开始 `/ramp` 运行时阅读它们——`/ramp` 应该为新学生分配的案件显示备忘录。

## 集成

- **`/ramp`:** 在下学期开始时，读取 `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/handoffs/[most-recent-semester]/` 并为每个新学生接手的案件显示每个案件的备忘录。
- **`/deadlines`:** 为每个备忘录的待处理截止期限部分提供内容。
- **`/client-comms-log`:** 为沟通历史摘要提供内容。
- **`/supervisor-review-queue`（如果启用正式审查）：** 交接备忘录路由到这里供教授批准。

## 此 skill 不做什么

- **关闭案件。** 交接是针对过渡到下个队列的案件。学期结束时关闭的案件应该为文件获得最终内部状态备忘录（`/legal-clinic:status internal`），并在交接文档中标记为关闭；status skill 支持 `client | internal | court` 受众。
- **分配进入学生。** 教授分配。Skill 记录分配是什么；不选择。
- **从零开始生成交接而没有诊所数据。** 需要活跃案件列表作为输入。如果诊所不维护一个，skill 会将差距显示为阻止器，而不是发明。
- **替代对话。** 书面备忘录是记录。离开学生也应该在可行时与进入学生对话——备忘录捕获事实；对话捕获备忘录无法捕获的判断和关系背景。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
