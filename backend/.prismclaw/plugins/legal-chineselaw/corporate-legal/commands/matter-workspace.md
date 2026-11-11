---
name: corporate-matter-workspace
description: '管理事项工作区——创建、列出、切换、关闭或分离活跃事项，以便多客户从业者将一个客户的上下文与所有其他客户分开。任何需要知道其在哪个事项中工作的实质性
  skill 都会阅读此内容。当用户说"new matter"、"switch matter"、"list matters"、"close matter"或只想在执业级工作时使用。

  '
argument_hint: <new | list | switch | close | none> [slug]
category: 公司法务
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /corporate-matter-workspace

从业者处理多个客户和事项。事项工作区将一个客户或业务的上下文与所有其他客户分开。此 skill 管理这些工作区。

## 子命令

- `/corporate-legal:matter-workspace new <slug>` — 创建新事项工作区，运行简短访谈，写入 `matter.md`
- `/corporate-legal:matter-workspace list` — 列出带有状态和活跃标志的事项
- `/corporate-legal:matter-workspace switch <slug>` — 设置活跃事项
- `/corporate-legal:matter-workspace close <slug>` — 归档事项（移动到 `~/.prismclaw/plugins/legal-chineselaw/corporate-legal/matters/_archived/`，永不删除）
- `/corporate-legal:matter-workspace none` — 从任何活跃事项分离，仅在执业级工作

## 说明

1. 阅读 `~/.prismclaw/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` — 确认 `## Matter workspaces` 部分已填充。如果 `Enabled` 为 `✗`，告诉用户："事项工作区已关闭——您配置为只有一个客户的内部执业，因此 plugin 自动从执业级上下文工作。如果您实际上处理多个客户，请重新运行 `/corporate-legal:cold-start-interview --redo` 并选择私人执业设置。否则，您根本不需要 `/corporate-matter-workspace`。"不要错误——禁用状态是内部用户的预期状态。
2. 使用以下工作流。
3. 根据 `$ARGUMENTS` 的第一个令牌分派：
   - `new` → 运行访谈，写入 `~/.prismclaw/plugins/legal-chineselaw/corporate-legal/matters/<slug>/matter.md`，播种 `history.md` 和 `notes.md`。
   - `list` → 枚举 `~/.prismclaw/plugins/legal-chineselaw/corporate-legal/matters/*/matter.md`，打印表格，标记活跃事项。
   - `switch` → 更新执业级 PRISMCLAW.md 中的 `Active matter:` 行。
   - `close` → 将 `~/.prismclaw/plugins/legal-chineselaw/corporate-legal/matters/<slug>/` 移动到 `~/.prismclaw/plugins/legal-chineselaw/corporate-legal/matters/_archived/<slug>/`，在 `history.md` 中记录关闭日期。
   - `none` → 将 `Active matter:` 设置为 `none — practice-level context only`。
4. 向用户显示更改内容并在写入前确认。

## 说明

- 除非执业级 PRISMCLAW.md 中的 `Cross-matter context` 为 `on`，否则 skill 永远不会跨事项阅读。
- 归档不是删除——已关闭的事项仍可阅读以用于保留/冲突目的。
- Slugs 使用小写和连字符。如果 slug 在已归档和活跃项中重用，已归档的项保存在 `_archived/<slug>/` 下。

---

多客户从业者（私人执业——独立、小律所、大律所）处理许多事项。一个事项的上下文不得泄露到另一个事项。此 skill 是使这成为可能的薄文件管理层。

**默认状态为关闭。** 内部用户永远不会看到这个——他们仅在执业级工作。事项工作区在冷启动时为私人执业用户打开，或通过编辑执业级 PRISMCLAW.md 中的 `## Matter workspaces` 打开。如果 `Enabled` 为 `✗`，则此 skill 不运行；`/corporate-legal:matter-workspace` 解释禁用状态并建议实际需要事项隔离的用户运行 `/corporate-legal:cold-start-interview --redo`。

## 存储布局

所有事项数据位于：

```
~/.prismclaw/plugins/legal-chineselaw/corporate-legal/
├── PRISMCLAW.md                       # 执业级执业档案
└── matters/
    ├── <slug>/
    │   ├── matter.md               # 客户、对手方、事项类型、关键事实、覆盖
    │   ├── history.md              # 事件、决定、草稿、审查的日期日志
    │   ├── notes.md                # 自由格式工作笔记
    │   └── outputs/                # 此事项的 skill 输出（可选子文件夹）
    └── _archived/
        └── <slug>/                 # 已关闭事项——可阅读但不活跃
```

Slugs 使用小写和连字符。示例：`acme-msa-2026`、`zenith-renewal`、`vendor-xyz-nda`。

## 活跃事项在执业 PRISMCLAW.md 中

执业级 PRISMCLAW.md 中 `## Matter workspaces` 下的 `Active matter:` 行是单一真实来源。切换事项会编辑该行。没有单独的状态文件。

## 子命令逻辑

### `new <slug>`

1. 确认 slug 尚未存在于 `matters/<slug>/` 或 `matters/_archived/<slug>/` 中。如果重用，请用户选择不同的 slug。
2. 运行访谈：
   - **客户**（我们代表的方，或内部业务单位（如果内部））
   - **对手方**（另一方——可能有多个）
   - **事项类型**（阅读 plugin 的执业档案以获取典型类别；对于 corporate-legal：M&A 买方 | M&A 卖方 | 融资 | 董事会事项 | 实体重组 | 集成项目 | 其他）
   - **保密级别**（standard | heightened | clean-team —— heightened 提示在跨事项设置中格外小心）
   - **关键事实**（2-5 句话：此事项的内容、利益相关者是谁、风险是什么）
   - **执业剧本的特定事项覆盖**（例如，"客户要求 24 个月 LoL 上限而不是 12"，"对手方是战略合作伙伴——关系保护语气"）
   - **相关事项**（任何相关事项的 slugs）
3. 使用以下模板写入 `matters/<slug>/matter.md`。
4. 用单个"Opened"条目播种 `matters/<slug>/history.md`。
5. 创建空的 `matters/<slug>/notes.md`。
6. **不要**自动切换到新事项。询问："要现在切换到 `<slug>` 吗？（`/corporate-legal:matter-workspace switch <slug>`）"

### `list`

枚举 `matters/*/matter.md`。阅读每个文件的前言或前几行以提取状态。打印表格：

| Slug | 客户 | 事项类型 | 状态 | 打开 | 活跃 |
|---|---|---|---|---|---|

用 `*` 标记当前活跃事项。如果存在任何 `_archived/*`，在单独的"已归档"标题下包含。

### `switch <slug>`

1. 确认 `matters/<slug>/matter.md` 存在。如果不存在，提供 `/corporate-legal:matter-workspace new <slug>`。
2. 将执业级 PRISMCLAW.md 中的 `Active matter:` 行编辑为 `Active matter: <slug>`。
3. 向用户显示 matter.md 摘要，以便他们确认自己在正确的事项上。

### `close <slug>`

1. 确认 `matters/<slug>/` 存在。
2. 将"关闭"条目追加到 `matters/<slug>/history.md`，并带有今天的日期。
3. 移动 `matters/<slug>/` → `matters/_archived/<slug>/`。
4. 如果关闭的事项是活跃事项，将 `Active matter:` 设置为 `none — practice-level context only`。

### `none`

将执业级 PRISMCLAW.md 中的 `Active matter:` 设置为 `none — practice-level context only`。向用户确认。

## `matter.md` 模板

```markdown
[WORK-PRODUCT HEADER — per plugin config ## Outputs — differs by role; see `## Who's using this` in the practice-level PRISMCLAW.md]

# Matter: [客户] — [简短描述]

**Slug:** [slug]
**Opened:** [YYYY-MM-DD]
**Status:** active
**Confidentiality:** [standard / heightened / clean-team]

---

## 各方

**Client:** [名称]
**Counterparty:** [名称]

## 事项类型

[vendor MSA | 客户协议 | NDA | SaaS 订阅 | 修正案 | 续约 | 其他——带一行理由]

## 关键事实

[2-5 句话。此事项的内容。利益相关者是谁。风险是什么。使其与默认剧本不同的原因。]

## 特定事项覆盖

*任何仅适用于此事项而非其他事项的执业级剧本偏差。*

- [例如，"LoL 上限：客户要求 24 个月，而不是公司标准 12 个月。"]
- [例如，"语气：关系保护——对手方是战略合作伙伴。"]
- [例如，"适用法律：必须是英国法，而不是特拉华州。"]

## 相关事项

- [slug——相关原因]

## 保密说明

[如果是 heightened 或 clean-team，说明原因。谁可以查看事项文件。即使全局打开，跨事项上下文是否允许。]
```

## `history.md` 种子

```markdown
# History: [客户] — [简短描述]

仅追加事件日志。最近的事件在顶部。

---

## [YYYY-MM-DD] — 事项打开

访谈完成。Slug：`[slug]`。状态：active.
[任何值得保留的初始上下文，超出 matter.md——例如，"针对来自 [对手方] 的入站 MSA 草案而打开。"]
```

## 跨事项上下文

执业级 PRISMCLAW.md 具有 `Cross-matter context:` 标志。当它为 `off`（默认值）时，在事项 A 中工作的 skill **永远不会阅读**任何其他 `B` 的 `matters/B/` 中的文件。句号。这就是设置存在以提供的保密保证。

当它为 `on` 时，skill 只有在用户明确要求时才能跨事项文件夹阅读文件（例如，"比较我们在过去五个供应商事项中的责任上限立场"）。即使为 `on`，默认情况下也只加载活跃事项，除非用户要求跨事项视图。

## 此 skill 不做什么

- **运行冲突检查。** 冲突是从业者/律所的工作；访谈捕获用户声明的内容。
- **执行保留。** 关闭归档事项；它不删除。保留策略超出范围。
- **自动路由输出。** 实质性 skill 决定写入位置；此 skill 告诉它*哪个文件夹*是活跃的，而不是放入什么。
- **决定跨事项是否合适。** 它读取标志并服从。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
