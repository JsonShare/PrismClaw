---
name: privacy-law-playbook
name_zh: 数据隐私专家执业资料
description: >
  本包的执业资料索引：命令流程、执业档案模板、MCP 配置记录与领域文档，按主题按需读取。触发条件：需要查阅冷启动访谈、DPA 剧本、DSAR 流程、PIA 模板、政策监控流程或法规时效清单时。不适用于直接产出结论——具体交付走 privacy-compliance-workflow 与 privacy-policy-workflow。命令仅作流程参考，不注册为斜杠命令。
invocation_policy: user_only
category: 数据隐私专家
---

# 数据隐私专家 · 执业资料

本 skill 提供编排总览与直调路由。

## 资料索引
- `references/commands/` — 命令流程（参考文档，不是可调用的 `/命令`）
- `references/mcp/` — MCP 配置记录（仅供查阅；专家包不内嵌 MCP）
- `references/currency-watch.md` — 法规时效性监控清单（含最后验证日期）

## 适用场景
- 需要读取具体流程、执业档案状态或领域资料时。
- 不适用于从零作答：结论性产出由 workflow 技能按其输出规范生成。

## 执行流程
检查会话中的执业配置。
2. 按所需主题读取单个 `references/commands/*.md`，不要一次性加载全部资料。
3. 按该流程文件的输出格式产出草稿，标注 [待核验] 项与律师复核提示。

## 输出
- 本 skill 自身不产出结论，只交付读取到的流程要点与路由建议。

## 边界
- 只读参考：不注册斜杠命令，不内嵌 MCP。
- `references/mcp/` 只是原始配置记录；所需工具必须由 系统全局连接器提供。
- 不替代对应 workflow 技能，也不直接对外发送任何文件。
