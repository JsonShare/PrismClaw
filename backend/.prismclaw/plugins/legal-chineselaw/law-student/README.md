<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# Law Student Plugin

学习模式，而非答案模式。苏格拉底式提问训练——它问你问题，质疑不严谨的推理。案例摘要、大纲构建、抽认卡、IRAC/三段论评分、课堂提问准备、从不重写的写作反馈，以及根据教师过往考试进行的考试预测。根据你的情况校准——你的课程、你的法律职业资格考试（法考）备考阶段、你想要被提问还是被解释。

**每个输出都是学习支架，而非标准答案。Plugin 构建你的思维框架，以苏格拉底式训练你，标记你出错的地方。它不会替你写大纲、摘要或论文——这会违背目的。学习材料中的引用都标记了需要验证。**

## 适用对象

法学院在读学生及法考备考人员。从大一入门到法律职业资格考试（法考）客观题、主观题全程备考。

## 首次运行：冷启动

这是关于你的，而不是某个组织。你的课程、你的法考备考阶段和薄弱科目、你的学习风格——训练我 vs 解释给我。带上材料：过往大纲、评分论文、旧考题（特别是同一教师出的）、法考真题集、教学大纲、论文。目标是 10 到 20 项；低于这个数量，档案会标记为 `LIMITED DATA`，后续技能会较薄弱，直到添加更多内容。

```
/law-student:cold-start-interview
```

## Skills

每个 skill 都以 `/law-student:<skill-name>` 调用。

| Skill | 功能 |
|---|---|
| `/law-student:cold-start-interview` | 关于你的访谈 + 材料收集——课程、律师资格考试、学习风格、材料 |
| `/law-student:socratic-drill [subject]` | 苏格拉底式训练——它提问，你回答，它质疑。不给答案。 |
| `/law-student:case-brief [case]` | 你偏好格式的案例摘要 |
| `/law-student:outline-builder [subject]` | 根据课程材料构建或扩展你格式的大纲 |
| `/law-student:bar-prep-questions [subject]` | 法律职业资格考试（法考）备考题，客观题（卷一/卷二）或主观题——覆盖民法、刑法、民诉、刑诉、行政法、商法、经济法、三国法、理论法，标记高频考点与历年真题规律 |
| `/law-student:flashcards [subject]` | 生成或训练抽认卡；莱特纳式分组；每科目 markdown；`--session <n>` 模式 |
| `/law-student:study-plan` | 构建或更新长期学习计划——阶段、按薄弱科目划分、根据会话历史的自适应每日安排 |
| `/law-student:session <subject> <n>` | 针对某科目的 N 个问题聚焦会话；用结果更新计划 |
| `/law-student:irac-practice` | 评分你的 IRAC 论文——结构、争点、规则、分析。追踪跨会话模式。从不重写。 |
| `/law-student:cold-call-prep [case]` | 课堂提问准备——预测教授问题并训练它们 |
| `/law-student:legal-writing [path-or-paste]` | 对任何草稿的结构性反馈——从不重写，永远不 |
| `/law-student:exam-forecast [class]` | 分析同一教授的过往考试；预测即将到来的考试 |

## "学习模式"的含义

这里的几个 skill（socratic-drill、训练模式下的 case-brief、cold-call-prep、irac-practice、legal-writing）被刻意设计为*不*给你答案或替你写东西。关键是你通过做来学习。如果你想要答案或草稿，使用其他工具。这个 plugin 是为了让你挣扎。

**legal-writing 是最严格的。** 它读你的草稿，告诉你哪里薄弱，但不重写。要求它重写会得到礼貌的拒绝，并提供更具体的结构性反馈。这是一个特性。

**outline-builder 和 case-brief 以更柔和的形式遵循相同规则。** Outline builder 提供支架——主题树、子主题槽、案例占位符——并在你根据自己的笔记和案例书填充规则时提出苏格拉底式问题。它不会仅根据教学大纲生成完整的大纲。Case brief 在每个模式下（训练我和解释给我）都以相同方式工作：skill 给出模板并质疑你写的内容；它不会替你摘要案例。如果你粘贴案例文本，它可以将法院自己的语言提取到槽中——这是指向来源，而非替你写。

## 学术诚信

在任何评分作业中使用此 plugin 之前——带回家考试、评分写作作业、期刊笔记、论文——检查你学校的荣誉守则和你教授关于 AI 工具的教学大纲政策。许多学校禁止或限制在评分作业中使用 AI，规则因课程和教授而异。此 plugin 设计用于学习和练习；在你学校禁止的地方使用它是违反荣誉守则的，后果由你承担，而不是工具的。有疑问时，书面询问你的教授。

这里的学习模式技能（socratic-drill、irac-practice、legal-writing、cold-call-prep）被刻意设计为不给你答案或替你写东西——这是教学法。这也是将某些允许的使用（看起来无辅助的练习训练）与禁止的使用（代写评分备忘录）区别对待的设计假设。不要绕过护栏。

## 置信度标记

生成内容的 skill 内联标记它们的置信度。没有标记的规则陈述或卡片是 skill 置信的内容（但仍不能替代你在考试前自己的来源检查）。整个 plugin 使用的标记：

- `[VERIFY: 主张——检查来源]` ——陈述为可能正确，但你应该在依赖之前对照你的大纲、案例书、预备课程或主要来源确认。在 bar-prep-questions、case-brief、flashcards、legal-writing、irac-practice 中大量使用。
- `[UNCERTAIN: 具体原因]` ——skill 对这个具体判断不置信（少数规则、有争议的争点识别、skill 不熟悉的司法管辖区）。自己判断；检查来源。
- `[GAP ——从课堂笔记填充]` ——outline-builder 标记，skill 对某个主题没有可靠来源且不会发明规则。你从笔记填充它。
- `[NEEDS CASES ——陈述规则但没有说明性案例]` ——outline-builder 标记，规则存在但案例说明缺失。
- `[CHECK CLASS NOTES ——教授可能在这里强调了某些内容]` ——outline-builder 标记，教授特定强调很重要而 skill 无法知道的领域。
- `[EXCEPTION UNCLEAR ——案例书提到了例外，找到规则]` ——outline-builder 标记，已知例外但细节未解决。
- `[UNCERTAIN ——框架]` ——exam-forecast 标记，注意预测是学习时间的权重分配，而非预言。

信任标记多于没有标记——没有标记的规则是 skill 置信的内容，但考试准备仍需要来源检查。

## 连接器和引用验证

**首先连接研究工具——引用护栏依赖它。** 没有它，每个引用都标记为 `[verify]`，每个交付物上方的审稿人注释记录来源未验证。Plugin 两种方式都工作；当连接研究工具时，它只是为你做更多验证。

此 plugin 中的法律研究连接器不仅仅是数据源——它们是经核验引用和你必须自行检查的引用之间的区别。通过**元典（yuandian）** 检索的法条、案例和企业信息标记为 `[元典]` 可追溯；通过**北大法宝（PKULaw）** 或**威科先行** 检索的裁判案例标记其来源。来自模型知识或网络搜索的引用标记为 `[模型知识—请核验]`，应在依赖前对照主要来源核查。Plugin 对引用分层，使你的核验时间用在重要的地方。

## 存储

你的练习档案存储在 `~/.prismclaw/plugins/legal-chineselaw/law-student/PRISMCLAW.md`，在 plugin 更新后保留。其他所有内容都在你的工作目录中：

```
law-student/
├── flashcards/
│   └── [subject]/cards.md             # 每科目抽认卡组
├── irac-sessions/
│   └── [student]/
│       ├── [date]-[topic].md          # 单个会话反馈
│       └── tracker.md                 # 跨会话模式追踪
├── writing-feedback/
│   └── [student]/
│       ├── [date]-[assignment].md     # 单个会话反馈
│       └── tracker.md                 # 跨会话模式追踪
└── exam-forecasts/
    └── [class]/
        └── forecast-[YYYY-MM-DD].md   # 版本化预测
```

## 测试和 QA

## 它如何学习

你在 `~/.prismclaw/plugins/legal-chineselaw/law-student/PRISMCLAW.md` 的学习档案不是静态的——它会随着你使用 plugin 而改进。Skill 会告诉你何时输出使用了你应该调整的默认值。你可以重新运行设置，直接编辑文件，或告诉 skill 记录新立场。

## 注意事项

- 训练我 vs 解释给我在冷启动时设置；可每会话切换。
- 案例摘要和大纲使用 *你的* 格式。如果你有现有大纲，让冷启动指向它们。
- 律师资格考试准备针对你在 `~/.prismclaw/plugins/legal-chineselaw/law-student/PRISMCLAW.md` 中的薄弱科目。它会持续回到这些科目。
- 每个生成内容的 skill 在不确定时标记。信任标记多于没有标记——没有标记的规则是我置信的内容；考试前仍要检查你的来源。
