---
name: commercial-matter-workspace
description: '管理事项工作区——新建、列表、切换、关闭或分离（执业级）。 当多客户执业者需要创建事项、切换活跃事项、列出事项、归档事项或分离到执业级上下文时，
  或当其他 skill 需要知道当前在哪个事项中工作时使用。

  '
argument_hint: <new | list | switch | close | none> [slug]
category: 商事合同
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /commercial-matter-workspace

执业者在多个客户和事项间工作。事项工作区将一个客户或业务的上下文与所有其他的分开。此命令管理这些工作区。

## 子命令

- `/commercial-legal:matter-workspace new <slug>` — 创建新的事项工作区，运行简短的接案访谈，写入 `matter.md`
- `/commercial-legal:matter-workspace list` — 列出事项及其状态和活跃标志
- `/commercial-legal:matter-workspace switch <slug>` — 设置活跃事项
- `/commercial-legal:matter-workspace close <slug>` — 归档事项（移至 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/matters/_archived/`，永不删除）
- `/commercial-legal:matter-workspace none` — 从任何活跃事项分离，仅在执业级工作

## 说明

1. 读取 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md`——确认 `## Matter workspaces` 部分已填充。如果 `Enabled` 为 `✗`，告诉用户："事项工作区已关闭——你被配置为仅有一个客户的内部执业——所以 plugin 自动从执业级上下文工作。如果你实际上在多个客户间工作，重新运行 `/commercial-legal:cold-start-interview --redo` 并选择私人执业设置。否则，你根本不需要 `/commercial-matter-workspace`。"不要报错——禁用状态是内部用户的预期状态。
2. 使用以下子命令逻辑。
3. 按 `$ARGUMENTS` 的第一个标记分发：
   - `new` → 运行接案访谈，写入 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/matters/<slug>/matter.md`，初始化 `history.md` 和 `notes.md`。
   - `list` → 枚举 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/matters/*/matter.md`，打印表格，标记活跃事项。
   - `switch` → 更新执业级 PRISMCLAW.md 中的 `Active matter:` 行。
   - `close` → 将 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/matters/<slug>/` 移至 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/matters/_archived/<slug>/`，在 `history.md` 中记录关闭日期。
   - `none` → 将 `Active matter:` 设为 `none — practice-level context only`。
4. 向用户展示更改内容并在写入前确认。

## 说明

- 除非执业级 PRISMCLAW.md 中的 `Cross-matter context` 为 `on`，否则 skill 绝不跨事项读取。
- 归档不是删除——关闭的事项保持可读，用于留存/冲突目的。
- Slug 使用小写加连字符。如果 slug 在归档和活跃事项间重复，归档的一个保存在 `_archived/<slug>/` 下。

---

## 事项上下文

**事项上下文。** 检查执业级 PRISMCLAW.md 中的 `## Matter workspaces`。如果 `Enabled` 为 `✗`，跳过本段的其余部分——skills 使用执业级上下文，事项机制不可见。如果已启用且没有活跃事项，询问："这是哪个事项的？运行 `/commercial-legal:matter-workspace switch <slug>` 或说 `practice-level`。"加载活跃事项的 `matter.md` 以获取事项特定上下文和覆盖。将输出写入事项文件夹 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/matters/<matter-slug>/`。除非 `Cross-matter context` 为 `on`，否则永远不要阅读另一个事项的文件。

---

多客户执业者（私人执业——独立、小律所、大律所）在许多事项间工作。一个事项的上下文绝不能泄露到另一个。此 skill 是使这成为现实的薄层文件管理层。

**默认状态为关闭。** 内部用户永远不会看到这个——他们只在执业级运行。事项工作区在冷启动时为私人执业用户开启，或通过编辑执业级 PRISMCLAW.md 中的 `## Matter workspaces` 开启。如果 `Enabled` 为 `✗`，此 skill 不运行；上面的工作流解释了禁用状态，并为实际需要事项隔离的用户建议 `/commercial-legal:cold-start-interview --redo`。

## 存储布局

所有事项数据位于：

```
~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/
├── PRISMCLAW.md                       # 执业级执业档案
└── matters/
    ├── <slug>/
    │   ├── matter.md               # 客户、对手方、事项类型、关键事实、覆盖
    │   ├── history.md              # 事件、决定、草案、审查的日期记录
    │   ├── notes.md                # 自由格式的工作笔记
    │   └── outputs/                # 此事项的 skill 输出（可选子文件夹）
    └── _archived/
        └── <slug>/                 # 已关闭的事项——可读但不活跃
```

Slug 使用小写加连字符。示例：`acme-msa-2026`、`zenith-renewal`、`vendor-xyz-nda`。

## 活跃事项在执业 PRISMCLAW.md 中

执业级 PRISMCLAW.md 中 `## Matter workspaces` 下的 `Active matter:` 行是单一事实来源。切换事项会编辑该行。没有单独的状态文件。

## 子命令逻辑

### `new <slug>`

1. 确认 slug 在 `matters/<slug>/` 或 `matters/_archived/<slug>/` 中不存在。如果重复使用，让用户选择不同的 slug。
2. 运行接案访谈：
   - **客户**（我们代理的一方，或内部用户的内部业务部门）
   - **对手方**（另一方——可能有多个）
   - **事项类型**（读取 plugin 的执业档案了解典型类别；对于 commercial-legal：vendor MSA | customer agreement | NDA | SaaS subscription | amendment | renewal | other）
   - **保密级别**（标准 | 提高 | 洁净团队——提高会在跨事项设置中提示额外谨慎）
   - **关键事实**（2-5 句话：此事项是关于什么的、利益相关者是谁、什么处于危险中）
   - **执业剧本的事项特定覆盖**（例如，"客户需要 24 个月 LoL 上限而非 12 个月"、"对手方是战略合作伙伴——维护关系的语气"）
   - **相关事项**（任何关联事项的 slug）
3. 使用以下模板写入 `matters/<slug>/matter.md`。
4. 用一条"已开启"条目初始化 `matters/<slug>/history.md`。
5. 创建空的 `matters/<slug>/notes.md`。
6. **不**自动切换到新事项。询问："现在想切换到 `<slug>` 吗？（`/commercial-legal:matter-workspace switch <slug>`）"

### `list`

枚举 `matters/*/matter.md`。读取每个文件的前置内容或前几行提取状态。打印表格：

| Slug | 客户 | 事项类型 | 状态 | 开启时间 | 活跃 |
|---|---|---|---|---|---|

用 `*` 标记当前活跃事项。如果有已归档的事项，在单独的"已归档"标题下列出 `_archived/*`。

### `switch <slug>`

1. 确认 `matters/<slug>/matter.md` 存在。如果不存在，提议 `/commercial-legal:matter-workspace new <slug>`。
2. 将执业级 PRISMCLAW.md 中的 `Active matter:` 行编辑为 `Active matter: <slug>`。
3. 向用户展示 matter.md 摘要，以便他们确认切换到了正确的事项。

### `close <slug>`

1. 确认 `matters/<slug>/` 存在。
2. 在 `matters/<slug>/history.md` 中附加"已关闭"条目，包含今天的日期。
3. 将 `matters/<slug>/` 移至 `matters/_archived/<slug>/`。
4. 如果关闭的事项是活跃事项，将 `Active matter:` 设为 `none — practice-level context only`。

### `none`

将执业级 PRISMCLAW.md 中的 `Active matter:` 设为 `none — practice-level context only`。向用户确认。

## `matter.md` 模板

```markdown
[WORK-PRODUCT HEADER — per plugin config ## Outputs — differs by role; see `## Who's using this` in the practice-level PRISMCLAW.md]

# Matter: [Client] — [short description]

**Slug:** [slug]
**Opened:** [YYYY-MM-DD]
**Status:** active
**Confidentiality:** [standard / heightened / clean-team]

---

## Parties

**Client:** [name]
**Counterparty:** [name(s)]

## Matter type

[vendor MSA | customer agreement | NDA | SaaS subscription | amendment | renewal | other — with one-line rationale]

## Key facts

[2–5 sentences. What this matter is about. Who the stakeholders are. What's at stake. What makes it different from the default playbook.]

## Matter-specific overrides

*Any deviation from the practice-level playbook that applies to this matter and only this matter.*

- [e.g., "LoL cap: client requires 24 months, not house standard 12."]
- [e.g., "Tone: relationship-preserving — counterparty is a strategic partner."]
- [e.g., "Governing law: must be English law, not Delaware."]

## Related matters

- [slug — one line why related]

## Notes on confidentiality

[If heightened or clean-team, describe why. Who may see matter files. Whether cross-matter context is permissible even if globally on.]
```

## `history.md` 初始条目

```markdown
# History: [Client] — [short description]

仅追加事件记录。最新在上。

---

## [YYYY-MM-DD] — 事项已开启

接案完成。Slug: `[slug]`。状态：活跃。
[任何值得在 matter.md 之外保留的初始上下文——例如，"因收到 [对手方] 的入站 MSA 草案而开启。"]
```

## 跨事项上下文

执业级 PRISMCLAW.md 有一个 `Cross-matter context:` 标志。当它为 `off`（默认）时，在事项 A 中工作的 skill **绝不读取** `matters/B/` 中的文件，对任何其他 `B`。这是该设置存在以提供的保密保证。

当它为 `on` 时，skill 只能在用户明确请求时（例如，"比较过去五个供应商事项中我们的责任上限立场"）跨事项文件夹读取。即使为 `on`，默认是只加载活跃事项，除非用户请求跨事项视图。

## 此 skill 不做什么

- **运行冲突检查。** 冲突是执业者/律所的工作；接案捕获用户声明的内容。
- **执行留存政策。** 关闭归档事项；不删除。留存政策超出范围。
- **自动路由输出。** 实质性 skill 决定在哪里写入；此 skill 告诉它*哪个文件夹*是活跃的，而不是写入什么。
- **决定跨事项是否适当。** 它读取标志并服从。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
