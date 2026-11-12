<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# demand-letters/ — 诉前催款函工作

此文件夹存放律师发出的每封催款函的工作产品：付款催告函、违约/补救通知、停止侵权函、劳动关系离职催告函、证据保全要求函。

与 `matters/` 分开，原因如下：

- 并非每封催款函都会升级为追踪中的事项。小额付款催告函和例行催收不需要台账行。
- 每封催款函都有相同的工作流形态（intake → 起草 → 发送 → 检查清单），无论其是否后续成为事项。
- 当催款函确实成为事项时，事项的 `matter.md` 反向链接到此处——起草历史保留在信函中。

## 目录结构

```
demand-letters/
├── _README.md                     # 本文件
└── [slug]/
    ├── intake.md                  # 背景收集、策略、杠杆、特权过滤器
    ├── draft-v1.docx              # 信函（v2、v3 随迭代）
    └── checklist.md               # 发送后检查清单——投递、副本、已入日历的截止日期、跟进
```

## Slug 惯例

`[类型]-[对手方]-[yyyy-mm]`。示例：

- `payment-acme-2026-04`
- `ceasedesist-competitor-x-2026-04`
- `breach-supplier-2026-04`
- `separation-smith-2026-04`
- `preservation-vendor-2026-04`

## 工作流

1. `/litigation-legal:demand-intake [标题]` → 运行自适应 intake，写入 `intake.md`
2. `/litigation-legal:demand-draft [slug]` → 运行 FRE 408 / 特权 / 放弃检查清单，起草 `.docx`，写入 `checklist.md`，提议创建事项

## 与事项的关系

起草催款函后，`demand-draft` 根据内部 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` 中的重大性启发式评估重大性，并提议创建事项。如果是，一个事项行进入 `matters/_log.yaml`，其中 `source: demand-letter`，`matters/[matter-slug]/matter.md` 反向链接到此催款函文件夹。

非重大催款函仅保留在此处。它们仍然是工作产品记录——只是不在组合中追踪。

## 更正和版本控制

绝不覆盖已发送的草稿。如果信函已发送且需要修订（例如，补充催款），开始 `draft-v2.docx`。版本历史本身就是有用的记录。
