<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# handoffs/ — 学期末案件交接备忘录

每学期一个文件夹，每个活跃案件一份交接备忘录。由 `/legal-clinic:semester-handoff` 在学期末生成。接收学生在 `/ramp` 入职培训时读取，了解其接手案件的情况。

## 目录结构

```
handoffs/
├── _README.md                             # 本文件
└── [YYYY-semester]/                       # 例如：2026-spring、2026-fall
    ├── _summary.md                        # 跨案件汇总：哪些案件在交接、交接对象是谁
    ├── [case-id].md                       # 每个活跃案件一份
    └── ...
```

## 命名规范

学期文件夹：`[年份]-[spring|summer|fall]`。示例：
- `2026-spring`
- `2026-summer`
- `2026-fall`

案件备忘录：使用 case_id（来自 `deadlines.yaml` 或接案记录），与该案件的其他文件命名保持一致。

## 交接备忘录包含内容

- 案件摘要（事实、执业领域、当前状态）
- 离任学生姓名 + 与当事人建立的关系（如有）
- 待处理截止日期（从 `deadlines.yaml` 提取）
- 未决问题 / 待定决策
- 通信历史（从 `client-comms/[case-id]/log.md` 提取）
- 已起草 / 已提交的文件（指向案件文件的指针）
- 接收学生需要了解的内容 / 首先要做的事
- 教授对接收学生的特别说明（如有）

## 工作流程

1. `/legal-clinic:semester-handoff` 由教授（或离任学生就其自己负责的案件）在学期结束前约 1-2 周运行。
2. 输出每个案件的备忘录和汇总。
3. 接收学生在下学期开始时运行 `/legal-clinic:ramp`；`/ramp` 会展示每位新生所分配案件的交接备忘录。

## 留存

交接备忘录保存在磁盘上。历史交接记录对诊所自身记录案件流转以及学生了解案件跨学期演变过程均有价值。
