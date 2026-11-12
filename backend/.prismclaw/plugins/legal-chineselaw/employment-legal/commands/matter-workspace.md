---
name: employment-matter-workspace
description: '管理事项工作空间——新建、列表、切换、关闭或分离（执业级）。创建、列出、切换、关闭和分离活跃事项，使一个客户参与的上下文永不泄漏到另一个。当多客户从业者说"新事项"、"切换事项"、"列出我的事项"、"关闭此事项"或需要管理哪个事项活跃时使用。

  '
argument_hint: <new | list | switch | close | none> [slug]
category: 劳动用工
---

<!--
This file is a Chinese translation and adaptation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
改造为中国劳动法语境。
-->


# /employment-matter-workspace

从业者处理多个客户和事项。事项工作空间将一个客户或参与的上下文与其他所有事项分开。此 skill 管理这些工作空间。

## 子命令

- `/employment-legal:matter-workspace new <slug>` — 创建新事项工作空间，运行简短接收，写入 `matter.md`
- `/employment-legal:matter-workspace list` — 列出事项及状态和活动标志
- `/employment-legal:matter-workspace switch <slug>` — 设置活动事项
- `/employment-legal:matter-workspace close <slug>` — 归档事项（移动到 `~/.prismclaw/assets/plugins/legal-chineselaw/employment-legal/matters/_archived/`，永不删除）
- `/employment-legal:matter-workspace none` — 从任何活动事项分离，仅在执业级工作

## 说明

1. 阅读 `~/.prismclaw/assets/plugins/legal-chineselaw/employment-legal/PRISMCLAW.md` — 确认 `## Matter workspaces` 部分已填充。如果 `Enabled` 为 `✗`，告诉用户："事项工作空间已关闭——你配置为单个客户的内部执业，因此插件自动从执业级上下文工作。如果你实际处理多个客户，重新运行 `/employment-legal:cold-start-interview --redo` 并选择私人执业设置。否则，你根本不需要 `/employment-matter-workspace`。"不要报错——禁用状态是内部用户的预期状态。
2. 使用下面的子命令逻辑。
3. 根据 `$ARGUMENTS` 的第一个令牌分派：
   - `new` → 运行接收访谈，写入 `~/.prismclaw/assets/plugins/legal-chineselaw/employment-legal/matters/<slug>/matter.md`，种子 `history.md` 和 `notes.md`。
   - `list` → 枚举 `~/.prismclaw/assets/plugins/legal-chineselaw/employment-legal/matters/*/matter.md`，打印表格，标记活动事项。
   - `switch` → 更新执业级 PRISMCLAW.md 中的 `Active matter:` 行。
   - `close` → 将 `~/.prismclaw/assets/plugins/legal-chineselaw/employment-legal/matters/<slug>/` 移动到 `~/.prismclaw/assets/plugins/legal-chineselaw/employment-legal/matters/_archived/<slug>/`，在 `history.md` 中记录关闭日期。
   - `none` → 设置 `Active matter:` 为 `none — practice-level context only`。
4. 向用户显示更改内容并在写入前确认。

## 说明

- 此 skill 永不跨事项读取，除非执业级 PRISMCLAW.md 中的 `Cross-matter context` 为 `on`。
- 归档不是删除——关闭的事项仍然可读以用于保留/冲突目的。
- Slug 为小写带连字符。如果 slug 在已归档和活动中重用，已归档的保留在 `_archived/<slug>/` 下。

---

## 参考

多客户从业者（私人执业——单人、小所、大所）处理许多事项。一个事项的上下文绝不能泄漏到另一个。此 skill 是使这成为可能的薄文件管理层。

**默认状态为关闭。** 内部法务用户永远不会看到此——他们仅在执业级工作。事项工作空间在冷启动时为私人执业用户打开，或通过编辑执业级 PRISMCLAW.md 中的 `## Matter workspaces`。

## 存储布局

所有事项数据位于：

```
~/.prismclaw/assets/plugins/legal-chineselaw/employment-legal/
├── PRISMCLAW.md                       # 执业级执业档案
└── matters/
    ├── <slug>/
    │   ├── matter.md               # 客户、对方、事项类型、关键事实、覆盖
    │   ├── history.md              # 事件、决策、草稿、审查的日期日志
    │   ├── notes.md                # 自由形式工作笔记
    │   └── outputs/                # 此事项的 skill 输出（可选子文件夹）
    └── _archived/
        └── <slug>/                 # 关闭的事项——可读但不活动
```

Slug 为小写带连字符。示例：`某公司-解除-2026`、`张某-竞业限制-2025`、`xyz公司-裁员-2026`。

## 活动事项在执业 PRISMCLAW.md 中

执业级 PRISMCLAW.md 中 `## Matter workspaces` 下的 `Active matter:` 行是单一真实来源。切换事项编辑该行。没有单独的状态文件。

## 子命令逻辑

### `new <slug>`

1. 确认 slug 尚未出现在 `matters/<slug>/` 或 `matters/_archived/<slug>/` 中。
2. 运行接收访谈：
   - **委托人**（我们代表的一方，或内部业务单位）
   - **对方**（另一方——可能有多个）
   - **事项类型**（阅读插件的执业档案以获取典型类别；对于 employment-legal：hire | termination | investigation | leave | accommodation | classification | country/region expansion | policy project | other）
   - **保密级别**（standard | heightened | clean-team——heightened 在跨事项设置中提示格外小心）
   - **关键事实**（2-5 句话：此事项关于什么、利益相关者是谁、有什么利害关系）
   - **针对此事项的执业手册覆盖**（例如，"客户要求协商解除而非单方解除"）
   - **相关事项**（任何连接事项的 slug）
3. 使用下面的模板写入 `matters/<slug>/matter.md`。
4. 使用单个"Opened"条目种子 `matters/<slug>/history.md`。
5. 创建空的 `matters/<slug>/notes.md`。
6. **不要**自动切换到新事项。询问："要立即切换到 `<slug>` 吗？(`/employment-legal:matter-workspace switch <slug>`)"

### `list`

枚举 `matters/*/matter.md`。阅读每个文件的 frontmatter 或前几行以提取状态。打印表格：

| Slug | 委托人 | 事项类型 | 状态 | 打开日期 | 活跃 |
|---|---|---|---|---|---|

用 `*` 标记当前活动事项。如果存在任何 `_archived/*`，在单独的"已归档"标题下包括它们。

### `switch <slug>`

1. 确认 `matters/<slug>/matter.md` 存在。如果不存在，提供 `/employment-legal:matter-workspace new <slug>`。
2. 将执业级 PRISMCLAW.md 中的 `Active matter:` 行编辑为 `Active matter: <slug>`。
3. 向用户显示 matter.md 摘要，以便他们确认自己在正确的事项上。

### `close <slug>`

1. 确认 `matters/<slug>/` 存在。
2. 在 `matters/<slug>/history.md` 中追加一个"Closed"条目，附带今天的日期。
3. 移动 `matters/<slug>/` → `matters/_archived/<slug>/`。
4. 如果关闭的事项是活动事项，设置 `Active matter:` 为 `none — practice-level context only`。

### `none`

将执业级 PRISMCLAW.md 中的 `Active matter:` 设置为 `none — practice-level context only`。与用户确认。

## `matter.md` 模板

```markdown
[工作底稿标题 — 根据 plugin 配置 ## Outputs — 因角色而异；见执业级 PRISMCLAW.md 的 `## Who's using this`]

# 事项：[委托人] — [简短描述]

**Slug：** [slug]
**打开日期：** [YYYY-MM-DD]
**状态：** active
**保密级别：** [standard / heightened / clean-team]

---

## 各方

**委托人：** [名称]
**对方：** [名称]

## 事项类型

[解除 | 裁员 | 调查 | 休假 | 特殊情形 | 劳动关系认定 | 省市扩张 | 规章制度 | 其他——附一行理由]

## 关键事实

[2-5 句话。此事项关于什么。利益相关者是谁。有什么利害关系。它为什么与默认手册不同。]

## 针对此事项的覆盖

*任何与执业级手册的偏差，仅适用于此事项。*

- [例如，"经济补偿金计算：需确认员工在上海的实际工龄是否含在其他公司的工龄。"]
- [例如，"特殊情形：员工可能处于医疗期，须优先确认医疗期状态。"]

## 相关事项

- [slug — 一行为何相关]

## 保密说明

[如 heightened 或 clean-team，描述原因。谁可以查看事项文件。即使全局开启，跨事项上下文是否允许。]
```

## `history.md` 种子

```markdown
# 历史：[委托人] — [简短描述]

仅追加事件日志。最近的在顶部。

---

## [YYYY-MM-DD] — 事项打开

接收完成。Slug：`[slug]`。Status：active。
[任何值得在 matter.md 之外保留的初始上下文——例如，"因来自 [对方] 的劳动仲裁申请而打开。"]
```

## 跨事项上下文

执业级 PRISMCLAW.md 有一个 `Cross-matter context:` 标志。当它为 `off`（默认）时，在事项 A 中工作的 skill **永不读取** `matters/B/` 中任何其他 `B` 的文件。这是设置存在的保密保证。

当它为 `on` 时，skill 可能仅在用户明确要求时跨事项文件夹读取文件。即使为 `on`，默认也仅加载活动事项，除非用户要求跨事项视图。

## 此 skill 不做什么

- **运行冲突检查。** 冲突是从业者/事务所的工作；接收捕获用户声明的内容。
- **强制保留。** 关闭归档事项；它不删除。保留政策超出范围。
- **自动路由输出。** 实质 skill 决定写入位置；此 skill 告诉它*哪个文件夹*是活动的，而非其中放什么。
- **决定跨事项是否适当。** 它读取标志并服从。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
