---
name: deadlines
description: '跟踪案件截止日期——添加、跨案件汇总报告、更新、完成、关闭。 在可配置的阈值（默认 14/7/3/1 天）发出警告；逾期事项保持标记直到解决。
  诊所工作量的操作记录。当学生或督导需要添加截止日期、询问本周到期什么、 获取截止日期报告或更新案件截止日期时使用。

  '
argument_hint: '[--add | --report（默认）| --update [id] | --complete [id] | --close [id]
  | --horizon=N]'
category: 法律诊所
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /deadlines

1. 加载 `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/PRISMCLAW.md` → 司法管辖区、执业领域、警告天数节奏。
2. 使用以下工作流。
3. 按标记路由：
   - `--add`：捕获案件、类型、描述、到期日期、来源、负责人。写入 `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/deadlines.yaml`。先检查重复。
   - `--report`（默认）：跨案件汇总——逾期、接下来 3 天、接下来 7 天、接下来 14 天；按负责人；按执业领域；未分配标记。
   - `--update [id]`：修改字段；附日期记录备注。
   - `--complete [id]`：标记完成；与学生确认工作实际已提交/归档。
   - `--close [id]`：关闭但不标记完成；要求在备注中说明理由。
4. 在提交前确认任何写入操作。

---

# 截止日期

## 目的

诊所最大的操作风险是错过截止日期。学生同时处理多个案件，兼职工作，每学期轮换。仅存在于个别学生脑中的截止日期在交接时丢失，在期末考试周被遗忘，在学生意外退出诊所时被错过。此 skill 是核心操作记录。

如果截止日期被错过，督导律师需要承担责任。此 skill 校准到那个风险水平——警告提前发出，逾期事项保持可见直到明确解决，交接（通过 `/semester-handoff`）将截止日期列表前向传递给下一个学生。

## 加载上下文

- `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/PRISMCLAW.md` → 司法管辖区、执业领域、截止日期警告天数（默认 14/7/3/1）、督导律师
- `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/deadlines.yaml` — 分类账

**司法管辖区假设。** 截止日期计算和警告阈值假设 PRISMCLAW.md 中设置的司法管辖区。截止日期、 tolling 规则、时间计算规则和本地法院实践因司法管辖区和特定法院而有实质性差异。如果事项涉及不同的州、特定法院的本地规则，或联邦 vs. 州法院问题，在依赖截止日期之前根据适用规则与您的督导确认。

## 模式

标记：`--add | --report | --update | --complete | --close`（默认：report）

### `--add` — 记录新截止日期

**输入：**
- 案件 ID + 名称（哪个案件）
- 执业领域
- 类型（提交 / 聆讯 / 诉讼时效 / 发现 / 补救期限 / 答复 / 通知 / 其他）
- 描述——到期内容的一行说明
- 到期日期（以及时间 + 时区，如果适用）
- 来源——截止日期来自哪里（2026-04-20 送达的法院命令、法规 8 USC § 1229a、合同 §7 中的补救期限）
- 负责学生——负责的学生

skill 自动生成 `id` slug：`[case]-[short-desc]-[YYYY-MM]`。

**从其他 skills 中提取：** 当 `/client-intake`、`/draft` 或 `/status` 在其输出中发现截止日期时，它们应该用预填充的字段交接给此 skill。学生确认并添加。

**添加前检查：** 如果具有相同 case_id + type + due_date 的截止日期已存在，标记为可能重复并在添加前询问。

**合理性检查区间。** 学生输入到期日期后，不要计算或验证——但对提交类型的典型范围进行粗略合理性检查，如果日期远远超出范围则标记学生。这是捕获学生自己数学中严重错误的脚手架，不是根据规则计算的替代方案。

**区间按司法管辖区索引。** 从 `${PRISMCLAW_PLUGIN_ROOT}/references/plausibility-bands/{state}.md` 加载此诊所司法管辖区的区间文件，其中 `{state}` 是 `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/PRISMCLAW.md` → 诊所司法管辖区的两位字母代码（联邦始终同时加载）。legal-clinic 插件附带 `${PRISMCLAW_PLUGIN_ROOT}/references/plausibility-bands/CA.md`（已完全填充）和 `${PRISMCLAW_PLUGIN_ROOT}/references/plausibility-bands/IL.md`（占位符结构）作为起点。

**如果区间文件缺失则在冷启动时硬停止。** 如果此诊所司法管辖区的 `${PRISMCLAW_PLUGIN_ROOT}/references/plausibility-bands/{state}.md` 不存在，不要在没有合理性检查的情况下静默运行。在冷启动时告诉督导：

> "我没有 [state] 的截止日期合理性检查——此诊所司法管辖区的合理性区间不在附带的参考文件中。我仍然可以跟踪截止日期（添加、报告、更新、完成、关闭），但无法对它们进行合理性检查。以下是构建区间文件的方法：复制 `${PRISMCLAW_PLUGIN_ROOT}/references/plausibility-bands/IL.md` 作为模板，为您的诊所最常见的每种截止日期类型填写一行（典型范围、触发事件处理、时间计算规则、简短引用），保存在 `${PRISMCLAW_PLUGIN_ROOT}/references/plausibility-bands/{state}.md`，然后重新运行 `/legal-clinic:deadlines`。在此之前，我接受的每个截止日期都会带有 `warnings: no-plausibility-band`，您的审查应将日期视为未检查的。"

不要为非 CA 诊所回退到 CA 表。静默降级的情况——将加利福尼亚合理性检查运送给伊利诺伊诊所——正是此修复要关闭的失败。

**合理性检查逻辑：**

1. 从 `${PRISMCLAW_PLUGIN_ROOT}/references/plausibility-bands/{state}.md`（加上联邦始终）加载此诊所司法管辖区的区间表。
2. 学生输入 `due:` 后，与触发事件日期 + 该 `type:` 的典型范围比较（如果加载的区间文件中存在该提交类型的典型范围）。
3. 如果在范围内，写入条目。什么都不说——区间存在的目的是捕获错误，不是祝贺正确的数学。
4. 如果超出范围实质性幅度，在写入前停止并说：
   > 您输入的日期超出了 [司法管辖区] 中 [type] 的典型范围。[Type] 截止日期对于 [提交类型] 通常在 [触发事件] 后约 [范围]。您的输入：[日期]，距离 [触发事件] [N] 天。根据 [区间文件中的引用规则] 和该司法管辖区的时间计算规则重新检查您的计算。如果您的计算是正确的（本地规则例外、非典型触发事件、tolling、豁免），确认，我将按原样添加条目。否则，重新计算并重新运行 `/deadlines --add`。
5. 如果此 `type:` 没有已知区间（不寻常的提交、非标准截止日期），不做合理性检查——写入条目并在 `warnings:` 字段中注明无适用合理性区间。
6. 如果此司法管辖区完全没有区间文件，上述冷启动时的硬停止适用；在稳态中（督导确认了差距并继续），每个条目都带有 `warnings: no-plausibility-band` 写入。

**此 skill 不计算。** 如果学生在 `due:` 字段中输入 `[VERIFY]` 因为他们还没有做数学，用 `due: [VERIFY]` 写入条目——合理性区间仅在学生提供具体日期时运行。计算由学生和督导完成。

### `--report`（默认）— 跨案件汇总

读取 `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/deadlines.yaml`。产生：

```markdown
# 截止日期报告 — [今天]

**活跃截止日期：** [N]
**逾期：** [N] ⚠️
**本周到期（接下来 7 天）：** [N]

---

## ⚠️ 逾期（标记为需要立即关注）

| ID | 案件 | 类型 | 到期 | 负责人 | 逾期天数 |
|---|---|---|---|---|---|

## 🔴 今天 / 接下来 3 天到期

| ID | 案件 | 类型 | 到期 | 负责人 |
|---|---|---|---|---|

## 🟡 4-7 天内到期

| ID | 案件 | 类型 | 到期 | 负责人 |
|---|---|---|---|---|

## 🟢 8-14 天内到期

[列表]

## 14 天以后

[仅计数——用 `/deadlines --report --horizon=30` 展开详情]

---

## 按负责学生（工作负载分布）

| 学生 | 逾期 | 接下来 7 天 | 接下来 14 天 | 活跃总数 |
|---|---|---|---|---|

## 按执业领域

[相同表格，按领域分组]

## 未分配的截止日期

[列表——如果任何活跃截止日期没有 owner_student 则标记]
```

### `--update` — 修改现有截止日期

常见更新：到期日期变更（法院延期）、负责人变更（重新分配）、添加备注。

每次更新写入一个带日期的内联回注；条目中可见历史。

### `--complete` — 标记完成

- 设置 `status: completed`、`completed_date: [今天]`。
- 与学生确认实际工作已完成并已提交/归档。
- 从活跃报告中移除但保留在 yaml 中。

### `--close` — 关闭但不标记完成

用于不再适用的截止日期——案件和解、动议撤回、客户放弃事项。要求在 `notes:` 条目中解释原因。

## 警告节奏

按照 `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/PRISMCLAW.md` 截止日期警告天数。默认 14、7、3、1。

警告不会自动展示——此插件没有计划/代理行为。但每当 `/deadlines` 被调用（或 `/status`，它会路由到此 skill 进行截止日期检查）时，报告会拉取触及警告阈值的任何内容。

如果截止日期过了到期日但未标记完成，它移到 `status: overdue` 并在每份报告中保持该状态直到明确解决。逾期截止日期不会自动关闭。

## 集成

- **`/client-intake`：** 当接案发现时间线紧急性（驱逐通知日期、庇护申请截止日期、聆讯日期）时，提供带预填充字段的 `/deadlines --add`。
- **`/draft`：** 当提交草案引用截止日期（答复到期、异议窗口）时，提供添加。
- **`/status`：** status skill 读取 `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/deadlines.yaml` 中相关案件的信息，并在输出中包含即将到来的截止日期。
- **`/semester-handoff`：** 读取 deadlines.yaml 以识别离开学生案件中的所有活跃截止日期；每份交接备忘录将截止日期前向传递。
- **`/supervisor-review-queue`（如果启用了正式审查）：** 接近截止的截止日期在审查队列中获得优先级。

## 此 skill 不做什么

- **从触发事件计算截止日期。** 如果今天送达了诉状，答复按本地规则在 21 天后到期，skill 不做那个数学——学生使用规则来做，并记录结果日期。（自主计算会创建 skill 不应拥有的责任；规则因司法管辖区和法院而异。）
- **提交或送达任何内容。** skill 跟踪日期；提交发生在插件之外。
- **自动通知。** 没有计划通知。报告在调用时展示警告；它不推送。可以稍后添加计划的 cron，但需要教授明确按诊所选择加入。
- **覆盖本地规则。** 如果学生记录的到期日期与本地规则矛盾，skill 不会捕获它。这是用 `[VERIFY: confirm against local rule]` 标记任何非常规截止日期的另一个原因。

> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
