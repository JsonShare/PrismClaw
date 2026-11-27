---
name: litigation-matter-workflow
name_zh: 诉讼事项管理
description: >
  争议诉讼事项管理入口工作流。覆盖案件立项、进度更新、结案归档、案情简报全流程。
  触发条件：'案件管理'、'立项'、'创建事项'、'更新案件'、'结案'、'案件简报'。
  不适用：文书起草、质证与证据审查本身（见其他诉讼工作流）；本工作流只管理事项、期限与状态。
category: 争议诉讼
invocation_policy: auto
priority: 10
---

# 诉讼事项管理

## 场景分流

按用户意图匹配以下场景，按需调用对应流程：

**新建案件 / 立项**
→ `skills/litigation-law-playbook/references/commands/matter-intake.md`

**更新事项 / 跟踪进度**
→ `skills/litigation-law-playbook/references/commands/matter-update.md`

**案件简报 / 管理层汇报**
→ `skills/litigation-law-playbook/references/commands/matter-briefing.md`

**结案归档**
→ `skills/litigation-law-playbook/references/commands/matter-close.md`

---

以下原子命令文档可在 `references/commands/` 目录查阅，按需读取：

| 场景 | 文件 |
|---|---|
| 立项 intake | `matter-intake.md` |
| 更新事项 | `matter-update.md` |
| 简报 | `matter-briefing.md` |
| 结案 | `matter-close.md` |
| 事项概览 | （我们系统暂不实现事项工作区，按执业级上下文执行） |
| 证据整理 | `chronology.md` / `claim-chart.md`（在 `litigation-evidence-workflow`） |
| 策略与辩护 | `brief-section-drafter.md`（在 `litigation-discovery-workflow`） |
