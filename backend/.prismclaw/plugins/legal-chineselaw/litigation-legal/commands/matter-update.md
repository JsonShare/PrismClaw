---
name: matter-update
description: 向事项的历史文件附加日期事件并刷新日志行——捕获新发展、状态变更、风险重新评估、截止日期变更和和解授权变更。当用户想要记录事项更新、记录发展或记录投资组合的状态变更时使用。
argument_hint: '[slug] [简要事件描述]'
category: 争议诉讼
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /matter-update

1. 遵循以下工作流和参考。
2. 确认 slug 存在于 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/` 和 `_log.yaml` 中。
3. 提示事件类型、日期（默认今天）、摘要以及任何日志字段更新（风险变更、状态变更、下一个截止日期变更、重大性重新分类）。
4. 附加日期条目到 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/[slug]/history.md`。
5. 更新 `_log.yaml`——将 `last_updated` 设为今天，应用任何字段更新。
6. 确认。

---

# 事项更新

## 目的

投资组合只有保持最新才有用。此 skill 让记录更新的成本很低——两分钟的结构化捕获，没有自由形式的漂移。

## 加载上下文

- `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/_log.yaml` — 查找行
- `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/[slug]/history.md` — 附加目标
- `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/[slug]/matter.md` — 参考（不要重写）
- `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` — 风险校准（如重新评估风险）

**冲突门槛——不可绕过。** 在记录更新之前，检查 `_log.yaml` 中的事项 slug。如果事项不在 `_log.yaml` 中，拒绝并路由：

> "我在事项日志中看不到 [matter slug]。首先运行 `/litigation-legal:matter-intake`，以便冲突检查运行并设置事项工作区。我不会向未管理的事项附加历史——冲突检查是门槛，在事项被接收之前没有 `history.md` 可附加。"

## 输入

Slug（必需）。如果未提供，询问——附上最近更新事项的短列表供选择。

## 更新

### 1. 事件类型

提供类别：

- **程序性** — 提交/收到动议、发出命令、举行听证、设定截止日期
- **发现** — 制作/收到、取证、送达传票
- **实质性** — 新事实、关键文档浮出、实体裁决
- **策略性** — 姿态转变、提出/收到和解要约、授权更新
- **风险重新评估** — 严重性或可能性变更
- **利益相关者** — 新人员加入、外部律师变更
- **行政性** — 聘书签署、预算调整、保留刷新

或如果没有匹配的自由形式。

### 2. 日期

默认今天。接受覆盖（例如，捕获上周的事件）。

### 3. 摘要

一段叙述。发生了什么，意味着什么，任何即时影响。

### 4. 日志字段变更

逐步检查可能受影响的字段：

- `status:` — 阶段是否转变（例如，诉答 → 事实发现）？
- `stage:` — 子阶段更新
- `risk:` — 需要重新评估？
- `materiality:` — 有变更吗（新事实可能触发准备金或披露）？
- `exposure_range:` — 如有新信息则修订
- `next_deadline:` — 新的即将到来的日期（如有）
- `outside_counsel:` — 变更？
- `internal_owners:` — 新增或移除任何人？
- `legal_hold:` — 刷新、扩大、释放？

仅提示事件类型可能影响的字段。程序性更新通常只涉及 `stage` 和 `next_deadline`；和解要约可能涉及 `materiality`、`exposure_range`、`status`。

### 4前. 和解接受门槛

如果策略更新是**和解接受**（公司正在接受和解要约、签署和解协议或授权原则上接受——而不仅仅是记录提出或收到的要约）：阅读 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` 中的 `## 谁在使用这个`。如果角色是非律师：

> 接受和解有法律后果——它解决主张、通常需要豁免、可能影响保险、税务和相关事项。你是否与律师审查了此内容？如果是，继续。如果否，这是带给他们的简报：
>
> [生成一页摘要：事项、拟议和解条款（金额、结构性、豁免范围、保密、不贬损）、涉及的敞口、授权阶梯状态（见 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` 和解授权）、可能出什么问题、在接受前要问律师什么。]
>
> 如果你需要在你所在的司法管辖区找到持牌律师、事务律师、大律师或其他授权法律专业人士：你所在专业监管机构的转介服务是最快的起点（美国的州律师协会、英格兰和威尔士的 SRA/律师标准委员会、苏格兰/北爱尔兰/爱尔兰/加拿大/澳大利亚的律师协会，或你所在司法管辖区的同等机构）。

没有明确的是，不要记录接受或翻转基于接受的重大性。记录要约或还价不需要门槛——接受需要。

### 4a. 重大性触发——明确提示

某些事件类型强制进行重大性复查。当事件类型在此列表中时，**始终提示**——不要让用户在没有明确回答的情况下继续：

| 事件类型 | 重大性触发提示 |
|---|---|
| 实质性（新事实、关键文档、实体裁决） | "此事件是实质性的。它是否推动 `materiality`？当前：`[current]`。选项：`reserved / disclosed / monitored / none`。变更？" |
| 策略性（姿态转变、提出或收到和解要约） | "和解活动通常触发重大性重新分类。当前：`[current]`。如果要约、还价或接受推动敞口变化或从争议转为可能且可估计，请重新分类。" |
| 风险重新评估（严重性或可能性变更） | "风险已变。重大性应跟踪。当前：`[current]`。重新分类？" |
| 监管 / 执法发展 | "监管机构行动（传票、CID、执法通知）通常触发披露分析。当前：`[current]`。变更？" |

可接受的回答包括 `no change`——但 `no change` 必须是明确的，不能通过沉默暗示。在历史条目中捕获：

```markdown
**重大性检查：** [无变化 / 从 X 变更为 Y]
**理由：** [一句话]
```

如果重大性变为 `reserved` 或 `disclosed`，且事项之前没有准备金或披露，将事件标记为需要根据 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` 重大性阈值通知财务 / 审计委员会。

### 5. 种子文档提示（可选）

如果更新引用了文档（命令、提交、通信），询问是否有路径可链接。不强求。

## 写入

### 附加到 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/[slug]/history.md`

最新的在顶部，直接在标题后面的 `---` 下方。

```markdown
## [YYYY-MM-DD] — [事件类型]：[简短标题]

[一段摘要。]

**字段变更：**
- [字段]：[旧 → 新]
- [字段]：[旧 → 新]

**相关文档：** [路径，如提供]
```

如果没有字段变更，省略"字段变更"块。

### 更新 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/_log.yaml`

- 应用任何字段变更。
- 设置 `last_updated: [today]`（或用户覆盖的事件日期——日志跟踪记录最后被触碰的时间）。

## 确认

在写入前向用户展示历史条目和 yaml 差异：

> 这是我将要附加和更新的内容。可以提交吗？

## 此 skill 不做什么

- 编辑过去的历史条目。更正是引用并纠正先前条目的新条目。
- 静默更改日志。每个字段变更在写入前都展示给用户。
- 决定新发展是否需要准备金/披露。它浮出问题（"这可能推动重大性——要重新分类吗？"），用户回答。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
