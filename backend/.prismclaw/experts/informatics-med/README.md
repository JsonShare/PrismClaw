# 医疗信息化专家

设计 FHIR R4 资源映射与 EHR 对接方案，提取临床数据并排查数据质量。

## 类型

Agent 型（单个 AI 专家），expertType = agent。

## 来源

独立专家包，与其他专家平级。

## 能力

- FHIR 集成：FHIR R4 资源映射与接口对接方案。
- EHR 适配：Epic/Cerner/MEDITECH 等系统的 SMART-on-FHIR 对接要点。
- 数据提取：临床数据结构化抽取与文档解析。
- 开发指引：FHIR 开发指南与常见错误排查。

## 使用示例

- 为 Epic 的 SMART-on-FHIR 对接给出资源映射与实现步骤。
- 从这批临床文本中抽取结构化字段并校验质量。
- 排查这份 FHIR 资源映射中的常见错误。

## 包结构

- `agents/informatics-med.md`：专家定义
- `skills/`：4 个技能（`doc-extract`、`fhir`、`fhir-developer`、`informatics-med-playbook`）
- `skills/informatics-med-playbook/`：执业资料与流程参考
- `skills/informatics-med-playbook/references/practice-profile/`：执业档案模板
- `skills/informatics-med-playbook/references/mcp/`：MCP 配置（仅供查阅）
- `avatars/expert.png`：专家头像
- `LICENSE`：源插件许可证

## 注意事项

- 对接生产系统前需在测试环境验证；不得绕过授权访问患者数据。

## 命令说明

