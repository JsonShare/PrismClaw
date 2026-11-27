# 法律实训专家

用苏格拉底式提问、案例摘要与 IRAC 评分训练法律思维，只训练不代写作业。

## 类型

Agent 型（单个 AI 专家），expertType = agent。

## 定位

只做学习与训练：苏格拉底式提问、案例摘要、大纲、IRAC 评分、法考与课程备考。真实纠纷的法律意见不在范围内，应交执业律师或 `legal-clinic`。

## 能力

- 苏格拉底式提问：用连续追问暴露推理漏洞而非直接给答案。
- 案例摘要与大纲：按 IRAC 结构做案例摘要与课程大纲。
- 练习评分：对 IRAC 答案按规则、适用、结论逐项评分。
- 备考计划：按法考（国家统一法律职业资格考试）或课程节奏制定复习与闪卡计划。

## 使用示例

- 用苏格拉底式提问带我分析这个案例，不要直接给答案。
- 把这份判决书做成 IRAC 结构的案例摘要。
- 给我的 IRAC 练习打分并指出推理漏洞。

## 包结构

- `agents/law-trainee.md`：专家定义
- `skills/law-trainee-playbook/`：执业资料与流程参考
- `skills/`：3 个技能（`law-student-practice-workflow`、`law-student-session-workflow`、`law-trainee-playbook`）
- `skills/law-trainee-playbook/references/commands/`：命令流程（参考文档）
- `skills/law-trainee-playbook/references/practice-profile/`：执业档案模板
- `skills/law-trainee-playbook/references/mcp/`：MCP 配置（仅供查阅）
- `avatars/expert.png`：专家头像
- `LICENSE`：源插件许可证

## 注意事项

- 绝不替用户完成作业或考试答案；只提供训练与反馈。

## 命令说明

流程参考文档位于 `skills/law-trainee-playbook/references/commands/`，仅作流程参考，不注册为 WorkBuddy 斜杠命令。
