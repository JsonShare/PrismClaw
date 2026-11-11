<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# matters/ — 案件组合数据

本文件夹存放案件组合，分两层结构：

- **`_log.yaml`** — 账本。每个事项一行，可被 skill 解析，是汇总的数据源。
- **`[slug]/`** — 每个事项的详细信息。叙述与历史记录，供人类阅读和编辑。

## 目录结构

```
matters/
├── _log.yaml                  # 账本（所有事项，含已结案）
├── _README.md                 # 本文件
└── [matter-slug]/
    ├── matter.md              # 叙述性接案信息 + 案件理论 + 当前状态
    └── history.md             # 仅追加的事件日志
```

## 命名规范

小写字母、连字符、末尾加年份。示例：
- `acme-v-us-2026`
- `ftc-inquiry-2026`
- `employment-smith-2026`

末尾加年份使命名在类似案件出现时仍保持稳定。文件夹名称与 slug 完全一致。

## 各文件由谁写入

| 文件 | 写入方 | 是否可直接编辑？ |
|---|---|---|
| `_log.yaml` | `/matter-intake`、`/matter-update`、`/matter-close` | 可以，但须在该事项的 `history.md` 中记录变更 |
| `matter.md` | 接案时由 `/matter-intake` 创建；`/matter-close` 追加 | 可以，用于补充演变中的案件理论 / 状态说明 |
| `history.md` | `/matter-intake` 创建初始内容；`/matter-update` 和 `/matter-close` 追加 | 实践中仅追加——将历史条目视为正式记录 |

## 已结案事项

保留在此处，不要删除。`/portfolio-status` 默认从活跃汇总中过滤掉已结案事项；`/portfolio-status --all` 包含已结案事项。已结案事项是案件组合判断的训练数据。

## 纠错

如果过去的历史条目有误，不要编辑它，而是追加一条新条目，引用并纠正该错误。纠错记录与纠正内容本身同等重要。
