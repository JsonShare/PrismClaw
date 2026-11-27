# 产品合规专家

按风险偏好审查功能上线，快速回答是否构成问题，并核验营销宣称证据。

## 类型

Agent 型（单个 AI 专家），expertType = agent。

## 能力

- 发布风险校准：按团队风险偏好审查新功能与发布计划。
- 快速判断：几分钟回答「这是不是一个问题」。
- 营销宣称核验：标记需要证据支撑的宣传表述。
- 提前预警：在有人提问前标记即将需要法务介入的发布。

## 使用示例

- 这个新功能可以上线吗？给出放行条件。
- 检查这段营销文案中哪些宣称需要证据支撑。
- 列出下个版本需要法务提前介入的事项。

## 包结构

- `agents/product-law.md`：专家定义
- `skills/product-compliance-workflow/`：快速分诊、发布审查与营销宣称审查的分流入口
- `skills/feature-risk-assessment/`：单个功能或产品领域的深度风险评估
- `skills/product-law-playbook/`：执业资料与流程参考
- `skills/product-law-playbook/references/commands/`：命令流程参考
- `skills/product-law-playbook/references/practice-profile/`：执业档案模板
- `skills/product-law-playbook/references/subagents/`：子代理参考定义
- `skills/product-law-playbook/references/launch-review/`：发布审查类别框架
- `skills/product-law-playbook/references/mcp/`：MCP 配置记录（仅供查阅）
- `avatars/expert.png`：专家头像
- `LICENSE`：许可证

技能合计 3 个。

## 注意事项

- 快速判断必须标注信息不完整时的假设条件。
- 法条、案例、截止日期、阈值等具体法律事实，未经检索工具核实的一律标注 [待核验]。

## 命令说明

命令流程保存在 `skills/product-law-playbook/references/commands/`，仅作流程参考，不注册为 WorkBuddy 斜杠命令。
