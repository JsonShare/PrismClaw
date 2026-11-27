# 劳动用工专家

审查招聘解雇风险、工人分类与休假期限，开展内部调查并起草用工政策。

## 类型

Agent 型（单个 AI 专家），expertType = agent。

## 来源

独立专家包，与其他专家平级。

## 能力

- 招聘/解雇审查：识别司法辖区特定风险标志与程序要求。
- 工人分类：按控制测试与当地规则做初步分类。
- 休假与期限跟踪：记录请假节点并在法定时限前提醒。
- 调查与政策：结构化内部调查、纪要、政策与员工手册起草。

## 使用示例

- 审查这份解雇方案，列出所在地特有风险与程序要求。
- 按控制测试判断这几类工人应归类为员工还是承包商。
- 为远程跨省用工起草员工手册补充条款。

## 包结构

- `agents/employment-law.md`：专家定义
- `skills/employment-law-playbook/`：执业资料与流程参考（原根级 references 已折叠至此）
- `skills/`：8 个技能（`employment-expansion-workflow`、`employment-hiring-workflow`、`employment-investigation-workflow`、`employment-law-playbook`、`employment-leave-workflow`、`employment-policy-workflow`、`handbook-updates`、`internal-investigation`）
- `skills/employment-law-playbook/references/commands/`：原命令流程
- `skills/employment-law-playbook/references/practice-profile/`：执业档案模板
- `skills/employment-law-playbook/references/subagents/`：原始子代理
- `skills/employment-law-playbook/references/mcp/`：MCP 配置（仅供查阅）
- `avatars/expert.png`：专家头像
- `LICENSE`：源插件许可证

## 注意事项

- 涉及解雇与调查结论时保留事实与意见分离，避免过早定性。

## 命令说明

原命令保存在 `skills/employment-law-playbook/references/commands/`，仅作流程参考，不注册为 WorkBuddy 斜杠命令。
