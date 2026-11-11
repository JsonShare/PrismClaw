<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# oc-status/ — 每周外部律师状态请求草稿

由 `/litigation-legal:oc-status` 生成的输出。按日期命名的每次运行文件夹，每个文件夹包含每个已起草事项的 Markdown 文件，以及一个 `_summary.md`。

## 目录结构

```
oc-status/
├── _README.md                       # 本文件
└── [YYYY-MM-DD]/
    ├── _summary.md                  # 运行内容、跳过内容及原因
    ├── [slug-1].md                  # 每个事项一份邮件草稿
    ├── [slug-2].md
    └── ...
```

当 Gmail MCP 已认证时，同时在用户收件箱中创建 Gmail 草稿。Markdown 文件是持久化记录；Gmail 草稿是操作层。

## 运行节奏

已设置定时时为每周（周一上午）运行。通过 `/litigation-legal:oc-status --setup-schedule` 注册定时计划。

随时按需运行：`/litigation-legal:oc-status`（默认筛选）或 `/litigation-legal:oc-status --slug=[slug]`（指定单个事项）。

## 文件清理

旧的日期文件夹会持续积累。外部律师已回复且事项历史已更新后即无需保留。可自行删除 30 天前的文件夹。
