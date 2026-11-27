# 诉讼仲裁专家

管理诉讼组合与期限，制作权利要求图表和时间线，准备质证并起草简报。

## 类型

Agent 型（单个 AI 专家），expertType = agent。

## 定位

只处理诉讼仲裁域：诉讼组合与期限、权利要求图表与时间线、证据与质证、文书草稿。与同域其他专家平级，跨域问题由专家团主理人编排。

## 能力

- 诉讼组合管理：事项、期限、保全、要求与外部律师统一管理。
- 图表与时间线：专利与民事权利要求图表、案件时间线。
- 证据与质证：证据审查、质证提纲、特权日志。
- 文书起草：要求函、简报章节与案件状态报告。

## 使用示例

- 为这个案件建时间线与权利要求图表。
- 根据证据材料起草质证提纲并标出证据缺口。
- 列出未来 30 天诉讼期限与待办动作。

## 包结构

- `agents/litigation-law.md`：专家定义
- `skills/litigation-law-playbook/`：执业资料与流程参考
- `skills/`：6 个技能（`litigation-demand-workflow`、`litigation-discovery-workflow`、`litigation-evidence-workflow`、`litigation-law-playbook`、`litigation-matter-workflow`、`litigation-trial-workflow`）
- `skills/litigation-law-playbook/references/commands/`：命令流程（参考文档）
- `skills/litigation-law-playbook/references/subagents/`：原始子代理
- `avatars/expert.png`：专家头像
- `LICENSE`：源插件许可证

## 注意事项

- 诉讼策略须由承办律师确认；不得虚构判例或程序期限。

## 命令说明

流程参考文档位于 `skills/litigation-law-playbook/references/commands/`，仅作流程参考，不注册为 系统注册命令。
