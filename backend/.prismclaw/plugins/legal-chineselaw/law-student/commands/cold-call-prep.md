---
name: cold-call-prep
description: '准备 cold-call——预测教授可能的问题并苏格拉底式地抽问你， 标记你薄弱的地方以便你知道课前需要重新阅读什么。 当用户说"准备明天的课"、"cold
  call [案例]"、"[教授] 可能问什么" 或指向指定阅读材料时使用。

  '
argument_hint: '[案例名称，或粘贴案例文本，或阅读材料路径]'
category: 法学学习
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /cold-call-prep

1. 加载 `~/.prismclaw/assets/plugins/legal-chineselaw/law-student/PRISMCLAW.md` → 课程列表、教授、学习风格。
2. 应用以下工作流。
3. 识别阅读材料（案例名称 + 引用、教授、课程、教学大纲上下文）。
4. 跨类别预测 6-10 个可能的问题（事实/裁决/推理/应用/政策），加权到教授的已知倾向。
5. 使用苏格拉底模式抽问——提问、等待、反驳、卡住时缩小。不要给答案。
6. 抽问后总结：强项/薄弱/遗漏；课前需要重新检查什么。

---

## 真实事项检查

如果学生问的问题听起来像是关于真实情况——他们的租约、他们的停车罚单、他们家的生意、他们朋友的被捕、真实的美元金额、真实的截止日期、真实的当事方名称——停止。

> "这听起来像是真实情况，而不是假设。我不能给你法律建议，你也不能给——你还不是律师。如果这是真实的，[某人] 需要真正的律师：法律援助、你学校的诊所、律师推荐服务（你所在司法管辖区的律师协会、法律协会或法律援助机构），或（如果有资金）私人律师。我很高兴帮助你理解涉及的一般法律概念，但那是学习，而不是建议。"

注意：真实姓名、真实地址、真实日期、具体美元金额、"我的房东/老板/父母/朋友"、"我收到了罚单/信件/通知"、以天衡量的截止日期。其中任何一个都是触发器。

## 目的

Cold-calling 成败取决于准备。教授已经阅读案例数十次并且知道问题；学生只阅读过一次。此 skill 缩小差距——预测案例的可能问题模式，对学生进行抽问，并显示他们没有掌握的内容。

不是阅读案例的替代品。一个你实际做过的测试。

## 信心纪律

- 当学生提供案例文本或案例书摘录时：我根据实际文本预测问题。有信心。
- 当学生只提供案例名称时：我根据我对案例的了解进行预测。在我不确定的任何依赖于案件细节的问题上标记 `[UNCERTAIN]`。强烈建议学生首先粘贴案例或案例书处理。
- 如果我不太了解这个案例：这样说。"I don't have a reliable read on this case — paste the text or casebook treatment and I can work from that. Otherwise my questions are educated guesses."

## 加载上下文

- `~/.prismclaw/assets/plugins/legal-chineselaw/law-student/PRISMCLAW.md` → 当前课程、教授、学习风格
- 用户提供的：案例名称 / 案例文本 / 案例书页面 / 阅读清单

## 工作流

### 步骤 1：识别阅读材料 + 教授

- 案例名称和引用
- 教授（来自 ~/.prismclaw/assets/plugins/legal-chineselaw/law-student/PRISMCLAW.md 课程列表——语气和重点因教授而异）
- 课程 / 学科领域
- 此案例在教学大纲中的位置（用于上下文——这是主题上的第一个案例，缩小案例，还是反例？）

### 步骤 2：预测问题

教授以反复模式进行 cold-call。跨这些类别预测：

**事实层面（热身）：**
- 当事人是谁？发生了什么？程序姿态？
- 初审法院做了什么？下级上诉法院呢？
- 为什么这在案例书中？它说明了什么主题？

**裁决/规则：**
- 裁决是什么？一句话。
- 从这个案例中出来的规则是什么——可携带的要点？
- 如果它在大纲中你会如何措辞规则？

**推理：**
- 为什么法院这样决定？
- 法院拒绝了什么论点？
- 有异议吗？它论证了什么？

**应用/假设：**
- 如果 [事实 X] 不同——同样的结果？
- 这个案例与 [教学大纲中的先前案例] 相比如何？
- 限制原则是什么？这个规则在哪里停止？

**政策/理论：**
- 法院保护的政策是什么？
- 这个规则有意义吗？替代方法？

**教授特定风格（来自 ~/.prismclaw/assets/plugins/legal-chineselaw/law-student/PRISMCLAW.md 笔记）：**
- 如果教授以假设繁重的 calls 闻名，加权应用/假设问题
- 如果政策繁重，加权政策/理论
- 如果事实繁重的苏格拉底式（Paper Chase 风格），加权事实 + 裁决

跨这些类别选择 6-10 个问题。按首先被问到的可能性排名（事实通常排在第一位，然后裁决，然后是更难的类别）。

### 步骤 3：抽问

使用 `socratic-drill` 模式：

1. 提问问题 1。等待答案。
2. 如果正确 + 推理良好：确认，进入问题 2。
3. 如果正确但草率：不要让它过去。"You got there, but explain — why does the court's reasoning support that?"
4. 如果错误：不要给答案。问一个缩小问题。"What facts does the court rely on?" 引导他们到那里。
5. 如果卡住：进一步缩小。"Before we go to the holding — what's the procedural posture?"
6. 如果真的迷路了：告诉他们重新阅读案例。"This is a re-read, not a guess-your-way-through. Come back when you've read it again."

### 步骤 4：抽问后总结

最后：

```markdown
# Cold-Call 准备 — [案例] — [日期]

**抽问的问题：** [N]
**强项：** [他们自信 + 正确的问题]
**薄弱：** [他们猜测或回避的问题]
**遗漏：** [他们不知道的问题]

## 明天课前：
- [需要重新检查的具体内容——他们弄错的事实、无法陈述的规则]
- [如果在政策/理论上薄弱："再读一遍异议——那通常是政策问题的来源"]

## 课上可能出现的问题：
- [10 个中的前 3 个——教授最可能首先问的]
```

## 集成

- **case-brief：** 如果学生还没有摘录案例，提议在 cold-call 准备之前运行 `/law-student:case-brief`。摘要也是 cold-call 准备工具。
- **socratic-drill：** 如果准备暴露了学科中的薄弱点（不仅仅是这个案例），接着运行 `/law-student:socratic-drill [学科]`。
- **flashcards：** 如果案例的规则是学生应该记住的，提议添加到抽认卡组。

## 此 skill 不做什么

- **成为教授。** 实际的 cold-call 可以去任何地方。此 skill 预测模式；教授会有惊喜。
- **替代阅读案例。** 如果你还没读它，skill 无法帮助你——问题需要你已吸收的文本。
- **不先问你案例的裁决就给你。** Drill-me 模式：我问，你回答。
- **预测司法管辖区特定的细分问题。** 如果教授有已知的爱好，在 ~/.prismclaw/assets/plugins/legal-chineselaw/law-student/PRISMCLAW.md 课程笔记中捕获它们，skill 可以相应加权；否则，它从一般模式工作。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
