# 医学科研专家

制定检索策略与综述大纲，设计研究方案与统计计划，并列出伦理注册待办。

## 类型

Agent 型（单个 AI 专家），expertType = agent。

## 来源

独立专家包，与其他专家平级。

## 能力

- 文献综述：检索策略、纳入排除标准与综述大纲。
- 研究设计：RCT/队列/真实世界研究方案框架。
- 统计方案：检验选择、样本量与偏倚控制建议。
- 临床试验：试验流程与里程碑管理。

## 使用示例

- 为【研究课题】做文献综述大纲与检索式。
- 设计一项随机对照试验的方案框架。
- 为这个真实世界数据研究选择统计方法并列出偏倚控制。

## 包结构

- `agents/research-med.md`：专家定义
- `skills/`：11 个技能（`biomedical-statistics`、`clinical-trial-protocol`、`clinical-trial-workflow`、`literature-review-workflow`、`medical-grant-outline`、`medical-manuscript-writing`、`medical-ppt`、`medical-scientific-figures`、`medical-translation`、`research-evidence`、`research-med-playbook`）
- `skills/research-med-playbook/`：执业资料与流程参考
- `skills/research-med-playbook/references/commands/`：命令流程参考
- `skills/research-med-playbook/references/practice-profile/`：执业档案模板
- `skills/research-med-playbook/references/mcp/`：MCP 配置（仅供查阅）
- `avatars/expert.png`：专家头像
- `LICENSE`：源插件许可证

## 注意事项

- 统计结论须由统计专家复核；不得编造文献或试验结果。

## 命令说明

命令流程收在 `skills/research-med-playbook/references/commands/`，仅作流程参考，不注册为斜杠命令。
