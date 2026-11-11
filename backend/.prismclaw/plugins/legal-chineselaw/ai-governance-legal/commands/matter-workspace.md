---
name: aig-matter-workspace
description: '管理事项工作区——新建、列表、切换、关闭或分离（执业级别）。 用于将一个客户或业务的上下文与其他所有上下文隔离的文件管理逻辑。 当跨多个客户或事项工作时、当用户说"new
  matter"、"switch matter"、 "list matters"、"close matter"，或任何实质性 skill 需要知道当前所处事项时使用。

  '
argument_hint: <new | list | switch | close | none> [slug]
category: AI治理
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /aig-matter-workspace

执业律师需要跨多个客户和事项工作。事项工作区将一个客户或业务的上下文与其他所有上下文隔离。此 skill 管理这些工作区。

## 子命令

- `/ai-governance-legal:matter-workspace new <slug>` — 创建新事项工作区，运行简短的接案访谈，写入 `matter.md`
- `/ai-governance-legal:matter-workspace list` — 列出所有事项及其状态和活跃标记
- `/ai-governance-legal:matter-workspace switch <slug>` — 设置当前活跃事项
- `/ai-governance-legal:matter-workspace close <slug>` — 归档事项（移至 `~/.prismclaw/plugins/legal-chineselaw/ai-governance-legal/matters/_archived/`，永不删除）
- `/ai-governance-legal:matter-workspace none` — 从任何活跃事项分离，仅在执业级别工作

## 说明

1. 读取 `~/.prismclaw/plugins/legal-chineselaw/ai-governance-legal/PRISMCLAW.md`——确认 `## Matter workspaces` 部分已填充。如果 `Enabled` 为 `✗`，告知用户："Matter workspaces are off — you're configured as an in-house practice with one client, so the plugin works from practice-level context automatically. If you actually work across multiple clients, re-run `/ai-governance-legal:cold-start-interview --redo` and select a private-practice setting. Otherwise, you don't need `/aig-matter-workspace` at all." 不要报错——禁用状态是内部用户的预期状态。
2. 使用以下工作流。
3. 根据 `$ARGUMENTS` 的第一个 token 进行分发：
   - `new` → 运行接案访谈，写入 `~/.prismclaw/plugins/legal-chineselaw/ai-governance-legal/matters/<slug>/matter.md`，初始化 `history.md` 和 `notes.md`。
   - `list` → 枚举 `~/.prismclaw/plugins/legal-chineselaw/ai-governance-legal/matters/*/matter.md`，打印表格，标记活跃事项。
   - `switch` → 更新执业级 PRISMCLAW.md 中的 `Active matter:` 行。
   - `close` → 将 `~/.prismclaw/plugins/legal-chineselaw/ai-governance-legal/matters/<slug>/` 移至 `~/.prismclaw/plugins/legal-chineselaw/ai-governance-legal/matters/_archived/<slug>/`，在 `history.md` 中记录关闭日期。
   - `none` → 将 `Active matter:` 设为 `none — practice-level context only`。
4. 向用户展示变更内容并在写入前确认。

## 注意事项

- 除非执业级 PRISMCLAW.md 中 `Cross-matter context` 为 `on`，否则 skill 绝不跨事项读取文件。
- 归档不是删除——已关闭事项仍可用于保留/冲突目的。
- Slug 使用小写加连字符。若 slug 在归档和活跃事项中重复使用，归档的保留在 `_archived/<slug>/` 下。

---

多客户执业律师（私人执业——独立、小律所、大律所）需要跨多个事项工作。来自一个事项的上下文绝不能泄漏到另一个。此 skill 是实现这一目标的轻量文件管理层。

**默认状态为关闭。** 内部用户永远不会看到这个——他们仅在执业级别运行。事项工作区在私人执业用户的冷启动时开启，或通过编辑执业级 PRISMCLAW.md 中的 `## Matter workspaces` 开启。如果 `Enabled` 为 `✗`，此 skill 不运行；上方工作流说明了禁用状态，并为真正需要事项隔离的用户建议运行 `/ai-governance-legal:cold-start-interview --redo`。

## 存储布局

所有事项数据位于：

```
~/.prismclaw/plugins/legal-chineselaw/ai-governance-legal/
├── PRISMCLAW.md                       # 执业级执业档案
└── matters/
    ├── <slug>/
    │   ├── matter.md               # 客户、对手方、事项类型、关键事实、覆盖
    │   ├── history.md              # 带日期的事件、决策、草稿、审查日志
    │   ├── notes.md                # 自由格式工作笔记
    │   └── outputs/                # 此事项的 skill 输出（可选子文件夹）
    └── _archived/
        └── <slug>/                 # 已关闭事项——可读但非活跃
```

Slug 使用小写加连字符。示例：`acme-msa-2026`、`zenith-renewal`、`vendor-xyz-nda`。

## 活跃事项记录在执业 PRISMCLAW.md 中

执业级 PRISMCLAW.md 中 `## Matter workspaces` 下的 `Active matter:` 行是唯一真实来源。切换事项会编辑该行。无单独的状态文件。

## 子命令逻辑

### `new <slug>`

1. 确认 slug 不在 `matters/<slug>/` 或 `matters/_archived/<slug>/` 中已存在。若重复，要求用户选择不同的 slug。
2. 运行接案访谈：
   - **客户**（我们代理的一方，或内部用户时为内部业务单元）
   - **对手方**（另一方——可能有多个）
   - **事项类型**（读取 plugin 的执业档案了解典型类别；对于 ai-governance-legal：内部用例 | 供应商 AI 审查 | AIA | 监管变化 | 政策项目 | 其他）
   - **保密级别**（standard | heightened | clean-team——heightened 在跨事项设置中提示额外注意）
   - **关键事实**（2-5 句话：此事项是关于什么的、利益相关者是谁、风险是什么）
   - **针对执业剧本的事项特定覆盖**（例如，"客户要求 24 个月 LoL 上限而非 12 个月"，"对手方是战略合作伙伴——关系维护基调"）
   - **相关事项**（任何相关事项的 slug）
3. 使用下方模板写入 `matters/<slug>/matter.md`。
4. 在 `matters/<slug>/history.md` 中初始化一条"已开立"记录。
5. 创建空的 `matters/<slug>/notes.md`。
6. **不**自动切换到新事项。询问："Want to switch to `<slug>` now? (`/ai-governance-legal:matter-workspace switch <slug>`)"

### `list`

枚举 `matters/*/matter.md`。读取每个文件的前言或开头几行以提取状态。打印表格：

| Slug | 客户 | 事项类型 | 状态 | 开立时间 | 活跃 |
|---|---|---|---|---|---|

用 `*` 标记当前活跃事项。如果存在已归档事项，在单独的"已归档"标题下列出 `_archived/*`。

### `switch <slug>`

1. 确认 `matters/<slug>/matter.md` 存在。若不存在，提供 `/ai-governance-legal:matter-workspace new <slug>`。
2. 将执业级 PRISMCLAW.md 中的 `Active matter:` 行编辑为 `Active matter: <slug>`。
3. 向用户展示 matter.md 摘要，让他们确认处于正确的事项。

### `close <slug>`

1. 确认 `matters/<slug>/` 存在。
2. 在 `matters/<slug>/history.md` 中追加带今日日期的"已关闭"记录。
3. 将 `matters/<slug>/` 移至 `matters/_archived/<slug>/`。
4. 如果被关闭的事项是活跃事项，将 `Active matter:` 设为 `none — practice-level context only`。

### `none`

将执业级 PRISMCLAW.md 中的 `Active matter:` 设为 `none — practice-level context only`。与用户确认。

## `matter.md` 模板

```markdown
[工作产品标头——按 plugin 配置 ## 输出部分——因角色而异；见执业级 PRISMCLAW.md 中的 `## 谁在使用这个`]

# 事项：[客户] — [简短描述]

**Slug：** [slug]
**开立：** [YYYY-MM-DD]
**状态：** active
**保密级别：** [standard / heightened / clean-team]

---

## 当事方

**客户：** [名称]
**对手方：** [名称]

## 事项类型

[vendor MSA | customer agreement | NDA | SaaS subscription | amendment | renewal | other — 附一行理由]

## 关键事实

[2-5 句话。此事项是关于什么的。利益相关者是谁。风险是什么。与默认剧本的不同之处。]

## 事项特定覆盖

*仅适用于此事项而非其他事项的、对执业级剧本的任何偏离。*

- [例如，"LoL 上限：客户要求 24 个月，而非内部标准 12 个月。"]
- [例如，"基调：关系维护——对手方是战略合作伙伴。"]
- [例如，"适用法律：必须为英国法，而非特拉华州法。"]

## 相关事项

- [slug — 一行说明相关原因]

## 保密说明

[若为 heightened 或 clean-team，说明原因。谁可以查看事项文件。即使全局开启，跨事项上下文是否允许。]
```

## `history.md` 初始内容

```markdown
# 历史：[客户] — [简短描述]

仅追加的事件日志。最新记录在顶部。

---

## [YYYY-MM-DD] — 事项开立

接案完成。Slug：`[slug]`。状态：active。
[任何值得保存在 matter.md 之外的初始背景——例如，"因收到 [对手方] 的入站 MSA 草案而开立。"]
```

## 跨事项上下文

执业级 PRISMCLAW.md 有一个 `Cross-matter context:` 标志。当为 `off`（默认）时，在事项 A 中工作的 skill **绝不读取** `matters/B/` 中的文件，无一例外。这是该设置存在的保密保证。

当为 `on` 时，skill 只有在用户明确要求时才可以跨事项文件夹读取文件（例如，"比较过去五个供应商事项中我们关于责任上限的立场"）。即使为 `on`，默认也只加载活跃事项，除非用户要求跨事项视图。

## 此 skill 不做什么

- **运行冲突检查。** 冲突检查是执业律师/律所的工作；接案访谈记录用户申报的内容。
- **执行保留政策。** 关闭归档事项；不删除。保留政策不在范围内。
- **自动路由输出。** 实质性 skill 决定写到哪里；此 skill 告诉它*哪个文件夹*是活跃的，而非放什么内容。
- **决定跨事项是否适当。** 它读取标志并遵守。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
