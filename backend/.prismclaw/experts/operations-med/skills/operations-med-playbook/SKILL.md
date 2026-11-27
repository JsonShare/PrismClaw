---
name: operations-med-playbook
name_zh: 医疗运营专家执业资料
description: >
  本包的命令流程、执业档案、子代理、MCP 配置与领域文档，按主题按需读取。命令仅作流程参考，不注册为斜杠命令。
invocation_policy: user_only
category: 医疗运营专家
---

# 医疗运营专家 · 执业资料

本 skill 提供编排总览与直调路由。

## 资料索引
- `references/commands/` — 命令流程（参考文档，不是可调用的 `/命令`）
- `references/mcp/` — 原 MCP 配置（仅供查阅；专家包不内嵌 MCP）
- `references/plugin-docs/` — 原插件文档
- `references/practice-profile/` — 执业档案模板与配置说明
- `references/subagents/` — 原始子代理定义

## 直调路由

以下流程不在两个工作流技能的覆盖范围内，按用户意图直接读取：

- `references/commands/inventory-check.md` — 药品与耗材库存盘点
- `references/commands/cold-start-interview.md` — 执业档案冷启动
- `references/commands/customize.md` — 执业档案与模块配置调整

## 使用约定

- 先读 `references/practice-profile/PLUGIN.md` 确认执业档案状态；仍有未填占位项时先走冷启动流程。
- 需要具体流程时再读对应 `references/commands/*.md`，不要一次性加载全部资料。
- `references/mcp/` 只是原始配置记录；所需工具必须由 WorkBuddy 侧全局连接器提供。
