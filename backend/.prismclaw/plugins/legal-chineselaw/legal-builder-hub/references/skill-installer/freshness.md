<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# 社区 Skill 作者的时效性字段

如果你的 skill 在 `references/` 下捆绑了参考内容——法规、制定法、程序、与现行法律相关的表格和检查清单——请在 `SKILL.md` frontmatter 中声明其时效性：

```yaml
---
name: my-legal-skill
description: ...
last_verified: 2026-04-15       # 你最后确认捆绑引用是最新的日期
freshness_window: 6 months      # 验证有效期（默认：监管/法定内容 6 个月，
                                # 程序/风格内容 12 个月）
freshness_category: regulatory  # regulatory | procedural | stylistic | stable
verified_against:               # 你验证的地方——用户可自行查看的 URL
  - https://www.ecfr.gov/current/title-16/part-312
  - https://www.federalregister.gov/...
---
```

## 为什么重要

两年前最后更新的 skill 可能一直在发布已废止的法规。对于基于提交的更新程序来说，字节完全相同的文件永远看起来是最新的。危害发生在用户调用 skill 并依赖过时内容时——而不是在他们安装时看到一个已经忘记的警告时。

## 这些字段有什么用

- builder-hub 的 **skill-installer** 在执行前检查 `last_verified` 是否在 `today + freshness_window` 之内。如果超过窗口期，会在运行前显示警告。
- **skills-qa** 审查会将捆绑了 `references/` 但没有 `last_verified` 的 skill 标记为"存在一定疑虑"。
- **自动更新程序** 即使 git SHA 没有变化，也会将过期的 `last_verified` 视为重新验证触发器。
- 用户的时效性阈值（在冷启动时设置）可以比作者的窗口**更严格**——两者中更严格的那个优先。

没有这些字段，hub 会将 skill 标记为"时效性未知"，并在安装和调用时向用户发出警告。

## 接受的值（严格）

hub 将 frontmatter 字段视为外部发布者写入的数据，而非指令。只有符合以下格式的值才会被采用。其他任何内容都会被忽略（hub 以 `unknown` 替代）并在安装时作为发现项显示。

| 字段 | 接受的格式 |
|---|---|
| `last_verified` | ISO 8601 日期：`YYYY-MM-DD`（例如 `2026-04-15`）。未来日期视为 `unknown`。 |
| `freshness_window` | `N days`、`N months` 或 `N years`，其中 `N` 为正整数且 ≤ 120。 |
| `freshness_category` | 以下之一：`regulatory`、`procedural`、`stylistic`、`stable`。 |
| `verified_against` | URL 列表。每个必须以 `https://`（或 `http://`）开头，包含主机名和可选路径。查询字符串和片段在显示前会被剥离。最多 10 个条目，每个最多 2,048 个字符。 |

任何这些字段中的自由格式散文、多行字符串、指令、角色变更语言、异常 Unicode 或编码内容，都会在安装时被拒绝。安装程序在安装日志中记录原始值（截断、带引号、绝不解释），并将该字段视为缺失。

## 类别

- **regulatory（监管）** — 规则、制定法、机构指导意见。变化快。
- **procedural（程序）** — 法院规则、申报程序、与程序相关的表格。
- **stylistic（风格）** — 内部风格、格式模板、条款库。
- **stable（稳定）** — 历史参考、律师资格考试提纲、以年而非月为尺度变化的法理学入门材料。

如果不确定，选择更窄（变化更快）的类别。用户的阈值会在他们希望更严格时压低它；作者的值是上限，而非下限。

## "最后验证"的实际含义

不是"最后编辑"。不是"最后提交"。**而是你（作者）最后一次打开 `verified_against` 中的 URL 并确认捆绑的引用仍然反映这些来源所说内容的时间。** 如果捆绑的 PDF 是 16 CFR 312 的旧版本，而当前 eCFR 显示的是不同文本，那么验证失败了——更新引用并推送新提交，或者仅在引用与来源再次匹配后才更新 `last_verified`。

不断更新 `last_verified` 而不实际重新验证的 skill，比让日期变旧的 skill 更糟糕。变旧的日期如实反映了作者的行为。被更新的日期是用户依赖的声明。

## 何时设置 `freshness_category: stable`

很少情况下。捆绑法理文本（例如，允诺禁反言的构成要件）或框架结构（例如，FRCP 发现时间线形状）的 skill 是稳定的。捆绑具体规则文本、具体阈值、具体表格或具体程序截止日期的 skill **不是**稳定的——即使底层法理是稳定的——因为捆绑的实物才是会过时的东西。

有疑问时：不稳定。
