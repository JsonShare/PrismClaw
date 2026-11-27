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

## 使用约定

检查会话中的执业配置。
- `references/mcp/` 只是原始配置记录；所需工具必须由 系统全局连接器提供。
