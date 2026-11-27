# 临床诊疗专家

整理病史摘要、分诊与检验解读，审查用药过敏风险，输出须医师复核的草稿。

## 类型

Agent 型（单个 AI 专家），expertType = agent。

## 定位

面向临床诊疗场景的助手：整理病史与检查、提示用药与过敏风险、生成病例复盘与治疗计划草稿。中国临床场景以 ICD-10 国临版/医保版为准；用户明确处于美国申报体系时，诊断与操作编码分别由 `icd10-cm`、`procedure-coding` 承接（须连接器查码）。

## 能力

- 病史与分诊：结构化病史摘要、症状与生命体征初步整理。
- 用药安全：过敏与药物相互作用风险提示、药品安全核查。
- 检验与编码：检验结果解读草稿、诊断与操作编码建议（按所在编码体系）。
- 病历与计划：病历字段抽取、病例复盘与治疗计划草稿。

## 使用示例

- 把这份患者病史整理成结构化草稿，标出用药与过敏风险。
- 解读这几项检验结果，列出需要医师确认的判断。
- 从这份病历中抽取结构化字段并给出 ICD-10 编码建议。

## 包结构

- `agents/clinical-med.md`：专家定义
- `skills/clinical-med-playbook/`：执业资料与流程参考
- `skills/`：10 个技能（`clinical-assessment-workflow`、`clinical-case-review`、`clinical-documentation`、`clinical-evidence`、`clinical-med-playbook`、`clinical-note-extract`、`clinical-trial-lookup`、`medical-patient-education`、`prescription-safety-workflow`、`treatment-plan-writer`）
- `skills/clinical-med-playbook/references/commands/`：命令流程参考文档
- `skills/clinical-med-playbook/references/subagents/`：子代理定义
- `avatars/expert.png`：专家头像
- `LICENSE`：源插件许可证

## 注意事项

- 所有输出均为草稿，不构成诊断或处方；急症立即引导线下就医。

## 命令说明

原命令保存在 `skills/clinical-med-playbook/references/commands/`，仅作流程参考，不注册为 系统注册命令。
