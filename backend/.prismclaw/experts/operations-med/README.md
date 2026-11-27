# 医疗运营专家

处理保险理赔、病历归档调取、预约与库存检查，并分析人群照护缺口与随访。

## 类型

Agent 型（单个 AI 专家），expertType = agent。

## 定位

医疗运营助手，处理保险理赔、病案归档调取、预约与库存检查，分析人群照护缺口。两个工作流技能分工：`insurance-claims-workflow` 管理赔与门诊运营（医生搜索、预约、筛查差距），`record-management-workflow` 只管病案归档、调取、质控与留存。

## 能力

- 保险理赔：理赔材料核对、提交与追踪流程。
- 记录管理：病历归档、调取与留存策略。
- 预约与库存：预约可用性、药品耗材盘点。
- 照护缺口：人群分层与随访缺口分析。

## 使用示例

- 核对这份理赔材料并给出提交与追踪步骤。
- 分析这批人群的照护缺口并列出随访优先级。
- 生成本周库存与预约可用性清单。

## 包结构

- `agents/operations-med.md`：专家定义
- `skills/operations-med-playbook/`：执业资料与流程参考
- `skills/`：3 个技能（`insurance-claims-workflow`、`operations-med-playbook`、`record-management-workflow`）
- `skills/operations-med-playbook/references/commands/`：命令流程参考文档
- `skills/operations-med-playbook/references/subagents/`：子代理定义
- `avatars/expert.png`：专家头像
- `LICENSE`：源插件许可证

## 注意事项

- 不得在输出中暴露可识别患者信息；示例须脱敏。
- 报销比例、申报时限、病历保存年限以本机构制度与当期政策为准，无法核实的标 `[待核验]`。

## 命令说明

原命令保存在 `skills/operations-med-playbook/references/commands/`，仅作流程参考，不注册为 系统注册命令。
