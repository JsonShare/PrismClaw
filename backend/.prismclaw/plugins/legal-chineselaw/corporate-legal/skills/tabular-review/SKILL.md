---
name: tabular-review
name_zh: 表格化审查
description: >
  表格化审查——每份文件一行，每个数据点一列，每个单元格引用来源。专为 M&A 尽调而构建（"审查这 200 份目标合同的控制权变更、转让和重大不利变化条款"），但适用于任何需要最终输出为电子表格的批量审查。当用户说"tabular review"、"review grid"、"build a grid"、"extract these fields from these contracts"、"review these documents for X, Y, Z"、"give me a spreadsheet of"、"batch review"，或指向一个文件夹并要求比较时使用。
category: 公司法务
invocation_policy: user_only
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /tabular-review

1. 加载 `~/.prismclaw/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` → 尽调结构、阈值、内部格式。
2. 确认：哪些文件、哪些列、输出去哪里。
3. 构建类型化模式。写入 `.review-schema.yaml`。与用户确认。
4. 样本运行（3-5 份文件）。调整模式。确认。
5. 扇出——每个文件一个子 agent，并行。每个单元格：值 + 状态 + 逐字引用 + 位置。
6. 规范化通过。标记异常值和不一致性。
7. 输出：`.xlsx` 或 Google Sheets（询问哪个），加上 `.csv` + `_sources.csv` + markdown 始终生成。工作产品标题。
8. 摘要：验证工作量（每列的 not_present/unclear/needs_review 计数）、标记的列、文件位置、提醒每个单元格是线索而非发现。

```
/corporate-legal:tabular-review
/corporate-legal:tabular-review --schema .review-schema.yaml --docs ./vdr/02-Contracts/
/corporate-legal:tabular-review --template ma-diligence
```

**`--schema <path>`：** 使用现有的模式文件而不是构建一个。用于重新运行和增量添加。

**`--template <name>`：** 从 `${PRISMCLAW_PLUGIN_ROOT}/references/tabular-review/` 中的模板开始。当前：`ma-diligence`。

**`--docs <path>`：** 文档来源。本地文件夹、Drive 文件夹 ID 或 VDR 路径。如果省略，则询问。

**`--output <xlsx|gsheets|csv>`：** 输出格式。如果省略，则询问。

**`--sample <n>`：** 模式检查的样本大小。默认 5。

---

## 事项上下文

**事项上下文。** 检查执业级 PRISMCLAW.md 中的 `## Matter workspaces`。如果 `Enabled` 为 `✗`（内部用户的默认值），跳过本段的其余部分——skills 使用执业级上下文，事项机制不可见。如果已启用且没有活跃事项，询问："这是哪个事项的？运行 `/corporate-legal:matter-workspace switch <slug>` 或说 `practice-level`。"加载活跃事项的 `matter.md` 以获取事项特定上下文和覆盖。将输出写入事项文件夹 `~/.prismclaw/plugins/legal-chineselaw/corporate-legal/matters/<matter-slug>/`。除非 `Cross-matter context` 为 `on`，否则永远不要阅读另一个事项的文件。

---

## 目的

你有一堆文件和一个需要在每份文件上一致回答的问题列表。一份尽调请求清单。一次供应商合同审计。一个租约组合审查。输出是一张表：文件行、数据点列，每个单元格都可追溯到来源中的确切文字。

这不是问题识别。`diligence-issue-extraction` 在 2,000 份文件中找到 30 个重要问题。此 skill 对所有 2,000 份文件回答相同的 15 个问题。两者都是合理的；它们回答不同的问题。

这也不能替代人类阅读文件。此 skill 产生的每个单元格都是一个**需要验证的线索**，而不是发现。输出的设计是为了使验证快速，而不是跳过它。

## 加载上下文

- `~/.prismclaw/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` → 尽调结构、重要性阈值、内部格式偏好
- `~/.prismclaw/plugins/legal-chineselaw/corporate-legal/deals/[code]/deal-context.md` 如果在处理特定交易
- 现有模式文件（如果用户有的话）（`.review-schema.yaml`）

## 列类型系统

使表格化审查有用的是第 C 列在第 1 行中的含义与在第 200 行中相同。自由文本会漂移。类型持稳。

每列都有一个**类型**来约束答案格式：

| 类型 | 返回内容 | 用于 |
|---|---|---|
| `verbatim` | 文件中的确切引用，逐字逐句 | 定义术语、实质性条款语言、任何文字很重要的内容 |
| `classify` | 你定义的固定列表中的一个值 | 是/否、存在/不存在、条款变体（例如，"sole consent"/"consent not unreasonably withheld"/"silent"） |
| `date` | ISO 日期 | 生效日期、到期日期、终止通知截止日期 |
| `duration` | 数字 + 单位 | 期限长度、通知期、存续期 |
| `currency` | 数字 + 货币代码 | 上限、阈值、费用、购买价格参考 |
| `number` | 纯数字 | 计数、百分比、页面引用 |
| `free` | 简短自由文本摘要 | 谨慎使用——这是会漂移的类型。仅在其他类型真的不适合时使用。 |

**逐字规则：** 每个非 `verbatim` 列还捕获支持答案的确切来源引用，作为伴随字段。单元格中的答案是解释；引用是证据。一个写着"consent not unreasonably withheld"的 `classify` 单元格，如果没有它来自的句子，就没有用，因为审查者的工作是检查这是否是正确的读法。

## "未找到"的三种状态

空白单元格隐藏信息。每当你无法产生肯定答案时，强制使用以下三种明确状态之一：

| 状态 | 含义 | 何时使用 |
|---|---|---|
| `not_present` | 文件已被阅读，条款不存在 | 你确信主题事项未被涉及 |
| `unclear` | 有些内容但你无法自信地分类 | 起草模糊、部分条款、相互冲突的条款 |
| `needs_review` | 你发现了一些内容但人工必须做出判断 | 边缘情况、不寻常的起草、答案取决于模式未捕获的判断 |

这是三条不同的信息。交易团队对"合同对转让沉默"与"转让条款模糊"的处理方式非常不同。将它们合并成一个空白单元格会失去这种区别。

## 工作流

### 步骤 0：内容和位置

确认：
1. **文件。** 它们在哪里？VDR MCP（Box、Datasite、iManage）、本地文件夹、Google Drive 文件夹或文件列表。有多少？如果 >200，警告这将需要一段时间并提议从重要性过滤的子集开始。
2. **模式。** 哪些列？两条路径：
   - 用户从 `${PRISMCLAW_PLUGIN_ROOT}/references/tabular-review/` 中选择模板（M&A 尽调标准是默认值）
   - 用户用自然语言描述列，你将它们构建成类型化模式
3. **输出。** Excel（`.xlsx`）还是 Google Sheets——询问团队使用哪个。CSV 和 markdown 始终作为备用写入。输出到交易文件夹、Drive 或用户指定的任何地方。

### 步骤 1：构建并确认模式

将用户的列列表转换为结构化模式。对于每列：一个稳定的 `id`、一个人类可读的 `label`、一个 `type`、一个 `prompt`（阅读文件的审查者会问的问题），以及对于 `classify` 列，一个 `options` 列表。

将其写入 `.review-schema.yaml`，放在输出旁边。此文件是可重用的工件——用户可以编辑它、添加列、针对新文件重新运行。在扇出前向用户显示并确认。

```yaml
schema:
  name: "M&A Diligence — Project [Code]"
  created: 2026-05-07
  columns:
    - id: counterparty
      label: "Counterparty"
      type: verbatim
      prompt: "Who is the contracting party other than the target?"
    - id: effective_date
      label: "Effective Date"
      type: date
      prompt: "When did the agreement become effective?"
    - id: change_of_control
      label: "Change of Control"
      type: classify
      options: [silent, consent_required, consent_not_unreasonably_withheld, automatic_termination, notice_only]
      prompt: "Does the agreement address a change of control of the target? What does it require?"
    - id: assignment
      label: "Assignment Restrictions"
      type: classify
      options: [silent, consent_required, consent_not_unreasonably_withheld, freely_assignable, assignable_to_affiliates]
      prompt: "Can the target assign this agreement? What restrictions apply?"
    # ... more columns
```

### 步骤 2：样本运行

不要在未经测试的模式上扇出到 200 份文件。先运行 3-5 份文件。向用户显示行。查找：
- 大多数答案为 `unclear` 的列——提示模糊，重写它
- `classify` 列中答案不符合选项的情况——添加选项或更改为 `free`
- `verbatim` 列返回释义的情况——强调必须逐字逐句

调整模式，重新运行样本，确认。这使用户免于必须丢弃的完整运行。

### 步骤 3：扇出

每份文件一个子 agent，并行。每个子 agent：

1. 读取整份文件（不是 RAG 块——整个文件）。
2. 对于每列，找到相关条款。
3. 返回结构化行：对于每列，`{value, state, quote, location}`。
   - `value` 是类型化的答案（如果 `state` 不是 `answered` 则为 null）
   - `state` 是 `answered | not_present | unclear | needs_review`
   - `quote` 是逐字支持文本（确切的，不是释义，句子内部不省略——如果你切割，在句子边界切割并标记）
   - `location` 是引用所在的位置（章节号、标题、页面——任何文件给你的内容）

**引用不是可选的，逐字规则是机械的，而不是劝诫。** 每个子 agent 在返回 `state: answered` 的单元格之前必须满足以下所有条件：

- `quote` 必须是来自源文件的连续文本的逐字逐句复制，可在子 agent 引用的 `location` 检索到。不要从章节标题加你期望存在的标准样板文字组成引用。不要释义然后称之为逐字引用。不要从记忆中重建引用，基于此类条款"通常"如何阅读。不要用跨越非连续文本的省略号拼接填充来源中的空白。
- `location` 必须足够具体，使规范化通过可以重新打开文件并重新阅读同一跨度——审查者可以导航到的章节号、标题或页面引用。
- 如果子 agent 无法定位和复制确切文本（来源被截断、OCR 垃圾、条款暗示但未写出、章节标题可见但正文未加载），单元格状态为 `needs_review`，`value` 为 null，`notes` 必须包含 `quote_unavailable: <reason>`。使用组合或重建的引用将 `state` 设置为 `answered` 永远是不可接受的。
- 同样的规则适用于 `verbatim` 类型列 **和** 附加到 `classify`/`date`/`duration`/`currency`/`number`/`free` 单元格的伴随来源引用。支持引用与单元格值具有相同的逐字义务。

步骤 4 中的规范化通过通过重新阅读引用的 `location` 处的来源并将存储的 `quote` 与来源文本逐字比较来抽查这一点。不匹配将单元格降级为 `needs_review`，注明 `quote_mismatch`，并标记整列进行更广泛的抽查——如果一个子 agent 组成了引用，同一运行中的其他子 agent 也可能如此。

### 步骤 4：规范化

扇出后，逐列读取整张表。这一步捕获每个表格化审查工具的失败模式：对不同文件中相同条款的不一致解释。

对于每个 `classify` 列：
- 检查每个 `answered` 值是否在选项列表中。异常值重新分类或提升到 `needs_review`。
- 检查聚类：如果 180 份文件说 `consent_required`，20 份说 `consent_not_unreasonably_withheld`，这可能是真实的。如果 195 份说 `consent_required`，5 份说 `freely_assignable`，查看那 5 份——它们要么是真正不同的，要么是错误分类的。

对于每个 `date`/`duration`/`currency` 列：
- 检查格式一致性。规范化。
- 标记不合理的值（99 年期限、$1 上限）为 `needs_review`。

对于每个 `verbatim` 列 **以及** 每个其他列上的伴随来源引用：
- 通过重新打开引用的 `location` 处的源文件，对随机样本（每列至少 3-5 行，或 10% 的行，以较大者为准）进行抽查，并将存储的 `quote` 与来源逐字比较。
- 如果任何引用被组成、释义、重建或无法在引用的跨度定位：将该单元格降级为 `needs_review`，注明 `quote_mismatch`，并标记整列——将抽查扩展到该列的其余部分，而不是假设其他行是干净的。一个伪造的引用足以扩大检查。
- `state: answered` 和引用不匹配的单元格是比 `unclear` 或 `needs_review` 单元格更高严重性的失败——它错误地表示了证据跟踪。积极降级。

### 步骤 5：输出

以三种格式写入表格：

**Markdown**（始终，用于会话内审查）：
```markdown
| 文件 | 对手方 | 生效日期 | 控制权变更 | 转让 | ⚠️ 标志 |
|---|---|---|---|---|---|
| Vendor MSA — Acme | Acme Corp | 2023-04-01 | consent_required | consent_required | — |
| Supply Agmt — Beta | Beta LLC | 2021-11-15 | ⚠️ unclear | silent | 控制权变更模糊 §14.2 |
```

**CSV**（`.csv`，始终）：
值用一个文件，引用和位置用一个伴随文件（`_sources.csv`）。保持主文件干净，证据跟踪完整。

**Excel**（`.xlsx`）或 **Google Sheets**——用户使用哪个。询问；不要猜测。两者遵循相同的工作簿结构（见 `${PRISMCLAW_PLUGIN_ROOT}/references/tabular-review/excel-output.md` 和 `${PRISMCLAW_PLUGIN_ROOT}/references/tabular-review/gsheets-output.md`）。对于 Excel：如果可用使用 PrismClaw in Excel（Office agent），`openpyxl` 备用。对于 Sheets：如果可用使用 Sheets MCP，通过 ADC 的 Sheets API，CSV 导入备用。在电子表格输出中：
- 每个数据列与包含引用和位置的隐藏来源列配对。可见列单元格上的单元格注释（Excel）或备注（Sheets）在悬停时显示引用。
- 按状态颜色编码：白色 = 已答复，黄色 = 不清楚或需要审查，灰色 = 不存在。
- 每个数据列后有一个 `Verified` 列，默认为空。审查者填写它。这是使表格可审计的验证/标记模式——交易团队可以一眼看到人工实际检查了什么。
- 一个 `_schema` 工作表，包含列定义，使文件自记录。

将 plugin 配置 `## Outputs` 中的工作产品标题作为顶行前置。在其旁边，包含分发说明：

> 此审查来自可能具有特权、机密或两者的源文件。它继承来源的特权和机密状态——超出特权圈的分发可能放弃特权。与事项的特权文件一起存储，并有意做出分发决定。

### 步骤 6：摘要

写入表格后，向用户提供一屏摘要：
- 文件数量、列数量、完成的行数
- 每列的 `not_present`、`unclear`、`needs_review` 计数——这是验证工作量
- 规范化通过标记 >10% 行的任何列
- 输出文件的位置
- 提醒：每个单元格是线索，不是发现。在此告知陈述、清单或备忘录之前需要验证。

## 以下一步决策树结束

根据 PRISMCLAW.md `## Outputs` 以下一步决策树结束。根据此 skill 刚刚生成的内容自定义选项——五个默认分支（起草 X、升级、获取更多事实、观察等待、其他）是起点，而不是锁定。树就是输出；律师选择。

## 此 skill 不做什么

- **它不能替代阅读文件。** 它告诉你在哪里查找。
- **它不产生置信度分数。** 0.73 不是信息。`unclear`/`needs_review` 状态和逐字引用是置信度信号——如果引用不支持值，标记它。
- **它不静默跳过文件。** 用户指向的每份文件都会有一行。无法读取的文件会有一行 `needs_review` 并附说明。
- **它不把释义当作引用。** 证据跟踪是重点所在。

## 与其他 skills 的关系

- `diligence-issue-extraction` 发现问题；此 skill 提取数据点。如果提取揭示了问题（引用特定收益目标的重大不利变化条款、毒丸），注意它并建议在该文件上运行 diligence-issue-extraction。
- `material-contract-schedule` 构建一张特定的表（披露清单）。它可以直接消费此 skill 的输出——清单是表格化审查的过滤、重新格式化的视图。
- `ai-tool-handoff` 在语料库太大或团队更倾向于专用平台时将批量审查交给配置的AI合同审查工具（元典或其他国内工具）。此 skill 是它能处理的任何内容的内部选项——先运行它，将剩余部分移交出去。

## 输出保障措施

每个输出都获得工作产品标题。每个单元格都获得来源引用或标记状态。摘要明确说明需要验证。Excel `Verified` 列使验证状态可审计。这不是让你跳过阅读的工具；这是使阅读更快的工具。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
