---
name: law-trainee-playbook
name_zh: 法律实训专家执业资料
description: >
  从原插件继承的命令流程、执业档案、子代理、MCP 配置与领域文档，按主题按需读取。命令仅作流程参考，不注册为斜杠命令。
invocation_policy: user_only
category: 法律实训专家
---

# 法律实训专家 · 执业资料

本 skill 提供编排总览与直调路由。

## 资料索引
- `references/commands/` — 命令流程（参考文档，不是可调用的 `/命令`）
- `references/mcp/` — 原 MCP 配置（仅供查阅；专家包不内嵌 MCP）

## 使用约定

检查会话中的执业配置。
- 需要具体流程时再读对应 `references/commands/*.md`，不要一次性加载全部资料。
- `references/mcp/` 只是原始配置记录；所需工具必须由 系统全局连接器提供。
