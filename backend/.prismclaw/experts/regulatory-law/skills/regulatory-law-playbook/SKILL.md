---
name: regulatory-law-playbook
name_zh: 监管政策专家执业资料
description: >
  本包的执业资料索引：命令流程、执业档案模板、子代理参考定义、台账模板、MCP 配置记录与领域文档，按主题按需读取。触发条件：需要查阅冷启动访谈、政策库与重要性阈值、内规差异响应流程、监管来文处理规则或信息源目录时。不适用于直接产出结论——具体交付走 regulatory-monitor-workflow、regulatory-policy-workflow 与 gap-surfacer。命令仅作流程参考，不注册为斜杠命令。
invocation_policy: user_only
category: 监管政策专家
---

# 监管政策专家 · 执业资料

本 skill 提供编排总览与直调路由。

## 资料索引
- `references/commands/` — 命令流程（参考文档，不是可调用的 `/命令`）
- `references/mcp/` — MCP 配置记录（仅供查阅；专家包不内嵌 MCP）
- `references/plugin-docs/` — 领域文档
- `references/practice-profile/` — 执业档案模板与配置说明
- `references/subagents/` — 子代理参考定义
- `references/gap-surfacer/` — 差异与评议台账模板（yaml）
- `references/reg-feed-watcher/` — 监管信息源目录
- `references/regulatory-incoming-letter/` — 来文分类规则与检查任务模板

## 适用场景
- 需要读取具体流程、执业档案状态或领域资料时。
- 不适用于从零作答：结论性产出由 workflow 技能与 gap-surfacer 按其输出规范生成。

## 执行流程
1. 先读 `references/practice-profile/PLUGIN.md` 确认执业档案状态；仍有未填占位项时先走冷启动流程。
2. 按所需主题读取单个 `references/commands/*.md`，不要一次性加载全部资料。
3. 按该流程文件的输出格式产出草稿，标注 [待核验] 项与律师复核提示。

## 输出
- 本 skill 自身不产出结论，只交付读取到的流程要点与路由建议。

## 边界
- 只读参考：不注册斜杠命令，不内嵌 MCP。
- `references/mcp/` 只是原始配置记录；所需工具必须由 WorkBuddy 侧全局连接器提供。
- 不替代对应 workflow 技能，也不直接对外发送任何文件或消息。
