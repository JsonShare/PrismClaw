<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# inbound/ — 入站法律函件

此文件夹存放来自外部的所有入站项目的分流和应对工作产品：收到的催款函、送达公司的传票、监管机构询问、证据保全要求、针对我们的停止侵权函。

与 `demand-letters/`（出站）和 `matters/`（追踪中的组合）分开，因为入站项目有其自己的工作流：阅读 → 分流 → 决策 → 应对（或升级为事项）。并非所有收到的内容都会成为追踪中的事项。

## 目录结构

```
inbound/
├── _README.md
└── [slug]/
    ├── incoming.pdf              # 或 .eml / .docx — 原件（或链接/指针）
    ├── triage.md                 # 分析：范围、价值、选项、建议
    └── response-v1.docx          # 起草的应对，如果我们应对（v2、v3 随迭代）
```

## Slug 惯例

`[类型]-[发件方简称]-[yyyy-mm]`。示例：

- `demand-rec-acme-2026-04`（收到的催款函）
- `subpoena-smith-v-us-2026-04`（第三方传票）
- `regulator-ftc-inquiry-2026-04`
- `preservation-vendor-2026-04`（收到的证据保全函）

## 工作流

| 类型 | 命令 | 输出 |
|---|---|---|
| 收到的催款函 | `/litigation-legal:demand-received [path]` | triage.md + 可选应对草稿 |
| 送达的传票 | `/litigation-legal:subpoena-triage [path]` | triage.md + 反对意见备忘录 |
| 监管询问 | *未来 skill* | |

每次分流都会交叉检查 `matters/_log.yaml` 中的相关事项（同一对手方、主题重叠）。如果存在相关事项，分流会标注并提议将此项添加为 `related_matter` 条目。如果此入站项目本身应成为追踪中的事项，分流会移交给 `/matter-intake` 并预填充字段。

## 与事项的关系

- 入站 + 与现有事项相关 → 通过 `_log.yaml` 中的 `related_matters` 字段链接；文件保留在 `inbound/` 中。
- 入站 + 应成为事项 → 创建事项；matter.md 反向链接到 `inbound/[slug]/`。
- 入站 + 已处理并关闭（无需创建事项）→ 保留在 `inbound/` 中作为记录。

## 与出站的关系

如果对入站催款函的应对本身是出站催款函（反催款），分流会移交给预填充的 `/demand-intake`。出站催款函保存在 `demand-letters/` 中，并反向链接到此入站文件夹。
