# 知识产权专家

开展商标专利初筛、FTO 分类与侵权通知，审查开源合规并跟踪权利续展。

## 类型

Agent 型（单个 AI 专家），expertType = agent。

## 来源

独立专家包，与其他专家平级。

## 能力

- 商标与 FTO 初筛：一轮商标查询与自由实施风险分类。
- 发明披露评估：对发明披露做初步专利性判断与补正建议。
- 侵权处置：起草停止侵权函、平台侵权投诉与通知—删除（含 DMCA 适用场景）及响应。
- 组合与条款治理：开源合规、IP 条款审查、注册与续展期限跟踪。

## 使用示例

- 对这个发明披露做初步专利性评估，列出需要补强的部分。
- 起草一封停止侵权函，针对【对方】使用我方商标的行为。
- 检查这份合同的 IP 条款与开源依赖风险。

## 包结构

- `agents/ip-law.md`：专家定义
- `skills/ip-law-playbook/`：执业资料与流程参考（原根级 references 已折叠至此）
- `skills/`：5 个技能（`ip-contract-workflow`、`ip-law-playbook`、`ip-patent-workflow`、`ip-portfolio-workflow`、`ip-trademark-workflow`）
- `skills/ip-law-playbook/references/commands/`：原命令流程
- `skills/ip-law-playbook/references/subagents/`：原始子代理
- `avatars/expert.png`：专家头像
- `LICENSE`：源插件许可证

## 注意事项

- 初筛不构成正式法律意见；正式申请与诉讼需专业人员复核。

## 命令说明

原命令保存在 `skills/ip-law-playbook/references/commands/`，仅作流程参考，不注册为 系统注册命令。
