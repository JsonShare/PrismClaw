---
name: litigation-matter-workspace
description: 管理多客户执业的事项工作区——创建、列出、切换、关闭或脱离活跃事项。当用户想要创建新事项工作区、切换活跃事项、列出事项、归档事项或仅在没有活跃事项的情况下在执业级工作时使用。
argument_hint: <new | list | switch | close | none> [slug]
category: 争议诉讼
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /litigation-matter-workspace

从业者跨多个客户和事项工作。事项工作区将一个客户或委托的上下文与其他所有客户或委托分开。此命令管理这些工作区。

## 子命令

- `/litigation-legal:matter-workspace new <slug>` — 创建新事项工作区，运行简短 intake，写入 `matter.md`
- `/litigation-legal:matter-workspace list` — 列出事项及其状态和活跃标记
- `/litigation-legal:matter-workspace switch <slug>` — 设置活跃事项
- `/litigation-legal:matter-workspace close <slug>` — 归档事项（移至 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/matters/_archived/`，永不删除）
- `/litigation-legal:matter-workspace none` — 脱离任何活跃事项，仅在执业级工作

注意：`/litigation-legal:matter-briefing [slug]`（无子命令）是一个独立命令，生成特定事项的简报——对内部投资组合审查有用。事项工作区管理在这里。

## 指令

1. 阅读 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md`——确认 `## 事项工作区` 部分已填充。如果 `Enabled` 为 `✗`，告诉用户："事项工作区已关闭——您配置为内部法务执业，只有一个客户，因此插件自动从执业级上下文工作。如果您实际跨多个客户工作，重新运行 `/litigation-legal:cold-start-interview --redo` 并选择私人执业设置。否则，您根本不需要 `/litigation-matter-workspace`。" 不要报错——禁用状态是内部用户的预期状态。
2. 遵循以下工作流和参考。
3. 根据 `$ARGUMENTS` 的第一个标记分派：
   - `new` → 运行 intake 访谈，写入 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/matters/<slug>/matter.md`，种子化 `history.md` 和 `notes.md`。
   - `list` → 枚举 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/matters/*/matter.md`，打印表格，标记活跃事项。
   - `switch` → 更新执业级 PRISMCLAW.md 中的 `活跃事项：` 行。
   - `close` → 将 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/matters/<slug>/` 移至 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/matters/_archived/<slug>/`，在 `history.md` 中记录关闭日期。
   - `none` → 设置 `活跃事项：` 为 `none — 仅执业级上下文`。
4. 向用户展示变更并在写入前确认。

## 注意事项

- skill 永远不跨事项读取，除非执业级 PRISMCLAW.md 中 `Cross-matter context` 为 `on`。
- 归档不是删除——关闭的事项仍可读取以用于保留/冲突目的。
- Slug 为小写带连字符。如果 slug 在已归档和活跃之间重复，已归档的保留在 `_archived/<slug>/`。

---

# 事项工作区

多客户从业者（私人执业——独立、小律所、大律所）跨许多事项工作。一个事项的上下文不能泄漏到另一个。此 skill 是使这一点的薄文件管理层。

**默认状态为关闭。** 内部用户永远不会看到这个——他们仅在执业级运行。事项工作区在冷启动时为私人执业用户开启，或通过编辑执业级 PRISMCLAW.md 中的 `## 事项工作区` 开启。如果 `Enabled` 为 `✗`，此 skill 不运行；`/litigation-matter-workspace` skill 解释禁用状态并为实际需要事项隔离的用户建议 `/litigation-cold-start-interview --redo`。

## 存储布局

所有事项数据位于：

```
~/.prismclaw/plugins/legal-chineselaw/litigation-legal/
├── PRISMCLAW.md                       # 执业级执业档案
└── matters/
    ├── <slug>/
    │   ├── matter.md               # 客户、对方、事项类型、关键事实、覆盖
    │   ├── history.md              # 有日期的事件、决定、草稿、审查日志
    │   ├── notes.md                # 自由格式工作笔记
    │   └── outputs/                # 此事项的 skill 输出（可选子文件夹）
    └── _archived/
        └── <slug>/                 # 关闭的事项——可读但不活跃
```

Slug 为小写带连字符。示例：`acme-msa-2026`、`zenith-renewal`、`vendor-xyz-nda`。

## 活跃事项在执业 PRISMCLAW.md 中

执业级 PRISMCLAW.md 中 `## 事项工作区` 下的 `活跃事项：` 行是唯一的真相来源。切换事项编辑该行。没有单独的状态文件。

## 子命令逻辑

### `new <slug>`

1. 确认 slug 未在 `matters/<slug>/` 或 `matters/_archived/<slug>/` 中存在。如果重复，请用户选择不同的 slug。
2. 运行 intake 访谈：
   - **客户**（我们代表的一方，或内部法务的业务部门）
   - **对方**（另一方——可能多个）
   - **事项类型**（阅读插件的执业档案获取典型类别；对于 litigation-legal：合同纠纷 | 劳动就业 | 知识产权 | 监管/调查 | 产品责任 | 集体诉讼 | 其他）
   - **保密级别**（标准 | 加强 | clean-team——加强提示在跨事项设置中格外小心）
   - **关键事实**（2-5 句话：此事项关于什么、利益相关者是谁、利害关系是什么）
   - **事项特定的执业手册覆盖**（例如，"客户要求 24 个月而非 12 个月的诉讼时效上限"、"对方是战略合作伙伴——保持关系的语气"）
   - **相关事项**（任何连接事项的 slug）
3. 使用下面的模板写入 `matters/<slug>/matter.md`。
4. 用单个"已开启"条目种子化 `matters/<slug>/history.md`。
5. 创建空的 `matters/<slug>/notes.md`。
6. **不**自动切换到新事项。询问："要切换到 `<slug>` 吗？（`/litigation-legal:matter-workspace switch <slug>`）"

### `list`

枚举 `matters/*/matter.md`。阅读每个文件的 frontmatter 或前几行提取状态。打印表格：

| Slug | 客户 | 事项类型 | 状态 | 开启日期 | 活跃 |
|---|---|---|---|---|---|

用 `*` 标记当前活跃的事项。如有存在，在单独的"已归档"标题下包含 `_archived/*`。

### `switch <slug>`

1. 确认 `matters/<slug>/matter.md` 存在。如果不存在，提供 `/litigation-legal:matter-workspace new <slug>`。
2. 编辑执业级 PRISMCLAW.md 中的 `活跃事项：` 行为 `活跃事项： <slug>`。
3. 向用户展示 matter.md 摘要以便确认他们在正确的事项上。

### `close <slug>`

1. 确认 `matters/<slug>/` 存在。
2. 向 `matters/<slug>/history.md` 附加今天的日期的"已关闭"条目。
3. 将 `matters/<slug>/` 移至 `matters/_archived/<slug>/`。
4. 如果关闭的事项是活跃事项，设置 `活跃事项：` 为 `none — 仅执业级上下文`。

### `none`

设置执业级 PRISMCLAW.md 中的 `活跃事项：` 为 `none — 仅执业级上下文`。与用户确认。

## `matter.md` 模板

```markdown
[工作产品标题——根据插件配置 ## Outputs——因角色而异；见执业级 PRISMCLAW.md 中的 `## 谁在使用这个`]

# 事项：[客户] — [简要描述]

**Slug：** [slug]
**开启：** [YYYY-MM-DD]
**状态：** active
**保密：** [standard / heightened / clean-team]

---

## 各方

**客户：** [name]
**对方：** [name(s)]

## 事项类型

[供应商 MSA | 客户协议 | NDA | SaaS 订阅 | 修订 | 续约 | 其他——附一行理由]

## 关键事实

[2-5 句话。此事项关于什么。利益相关者是谁。利害关系是什么。与默认手册的不同之处。]

## 事项特定覆盖

*任何适用于此事项且仅此事项的偏离执业级手册的内容。*

- [例如，"诉讼时效上限：客户要求 24 个月，非内部标准 12。"]
- [例如，"语气：保持关系——对方是战略合作伙伴。"]
- [例如，"适用法律：必须是英国法，非特拉华州法。"]

## 相关事项

- [slug — 一行原因]

## 保密说明

[如果加强或 clean-team，描述原因。谁可以查看事项文件。即使全局开启，跨事项上下文是否允许。]
```

## `history.md` 种子

```markdown
# 历史：[客户] — [简要描述]

仅追加事件日志。最新在前。

---

## [YYYY-MM-DD] — 事项已开启

Intake 完成。Slug：`[slug]`。状态：active。
[任何值得在 matter.md 之外保留的初始上下文——例如，"因 [对方] 的入站 MSA 草稿而开启。"]
```

## 跨事项上下文

执业级 PRISMCLAW.md 有一个 `Cross-matter context:` 标志。当它为 `off`（默认）时，在事项 A 中工作的 skill **永远不会读取** 任何其他 `B` 的 `matters/B/` 文件。这是该设置存在的保密性保证。

当它为 `on` 时，skill 仅在用户明确要求时才能跨事项文件夹读取文件（例如，"比较我们在最近五个供应商事项中的责任上限立场"）。即使 `on`，默认也只加载活跃事项，除非用户要求跨事项视图。

## 此 skill 不做什么

- **运行冲突检查。** 冲突是从业者/律所的工作；intake 捕获用户声明的内容。
- **强制保留。** 关闭归档事项；不删除。保留政策超出范围。
- **自动路由输出。** 实质性 skill 决定写入哪里；此 skill 告诉它*哪个文件夹*是活跃的，而不是放入什么。
- **决定跨事项是否适当。** 它读取标志并遵守。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
