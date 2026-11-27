---
name: contracts-med-playbook
name_zh: 医疗合同专家执业资料
description: >
  本包的命令流程、执业档案、MCP 配置记录与领域文档，按主题按需读取。命令仅作流程参考，不注册为斜杠命令。
invocation_policy: user_only
category: 医疗合同专家
---

# 医疗合同专家 · 执业资料

本 skill 提供编排总览与直调路由。

## 资料索引
- `references/mcp/` — MCP 配置记录（仅供查阅；专家包不内嵌 MCP）
- `references/practice-profile/` — 执业档案模板与配置说明

## 使用约定

- 先读 `references/practice-profile/PLUGIN.md` 确认执业档案状态；仍有未填占位项时先走冷启动流程。
- `references/mcp/` 只是原始配置记录；所需工具必须由 WorkBuddy 侧全局连接器提供。
