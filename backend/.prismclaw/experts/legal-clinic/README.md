# 法律援助专家

负责诊所搭建、学生入职、结构化案件登记与防失职期限管理，并组织学期交接。

## 类型

Agent 型（单个 AI 专家），expertType = agent。

## 定位

只处理法律诊所的运行流程：诊所搭建、学生入职、结构化 intake、期限台账与复核队列、学期交接。具体案件的诉讼策略与定稿文书由承办律师处理。

## 能力

- 诊所搭建与学生入职：建立诊所指南、角色与入职清单。
- 结构化 intake：统一案件登记、利益冲突与授权检查。
- 期限防失职：追踪关键期限并建立复核队列。
- 学期交接：案件状态、档案与后续责任人交接。

## 使用示例

- 为新学期搭建法律诊所的入职清单与督导流程。
- 对这个新案件做结构化 intake 与利益冲突检查。
- 列出本周即将到期的案件期限与复核队列。

## 包结构

- `agents/legal-clinic.md`：专家定义
- `skills/legal-clinic-playbook/`：执业资料与流程参考
- `skills/`：5 个技能（`clinic-case-workflow`、`clinic-client-workflow`、`clinic-management-workflow`、`clinic-supervisor-workflow`、`legal-clinic-playbook`）
- `skills/legal-clinic-playbook/references/commands/`：命令流程（参考文档）
- `skills/legal-clinic-playbook/references/practice-profile/`：执业档案模板
- `skills/legal-clinic-playbook/references/plausibility-bands/`：期限合理性区间（`CN.md` 中国已填充；`CA.md` 加州已填充；`IL.md` 占位结构）
- `skills/legal-clinic-playbook/references/mcp/`：MCP 配置（仅供查阅）
- `avatars/expert.png`：专家头像
- `LICENSE`：源插件许可证

## 注意事项

- 所有对外文书须经督导审阅；不得让学生独立作出重大诉讼决定。
- 中国辖区期限区间见 `plausibility-bands/CN.md`，覆盖民事诉讼程序期限、涉外期限与两类常见实体时效，条文号按《民事诉讼法》2023 修正版标注、仍须核对现行版本。
- `CN.md` 未收录的程序类型（执行、行政、刑事、劳动人事争议仲裁、商事仲裁、破产、知识产权行政程序等）须先按模板补录，补录前相关条目视为未做合理性检查。

## 命令说明

流程参考文档位于 `skills/legal-clinic-playbook/references/commands/`，仅作流程参考，不注册为 WorkBuddy 斜杠命令。
