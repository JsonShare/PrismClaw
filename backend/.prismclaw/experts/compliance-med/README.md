# 医疗合规专家

以《个人信息保护法》《数据安全法》为主口径开展合规与隐私审计，输出 PIA、泄露响应与整改清单；美国 HIPAA 体系按辖区适用。

## 类型

Agent 型（单个 AI 专家），expertType = agent。

## 定位

医疗合规审计助手，输出差距清单、PIA 与事件响应草稿。HIPAA 审查仅适用于美国体系；中国境内主体与患者数据按《个人信息保护法》《数据安全法》等中国法规核查。两套体系不混用，辖区不明时先向用户确认。美国 payer 事前授权与医保反欺诈流程不在本包范围。

## 能力

- 合规体系核查：按辖区选定适用体系，逐条核对义务与控制措施。
- 隐私影响评估：对数据处理活动做风险分级与 PIA。
- 泄露响应：按时间线组织通报与补救清单。
- 审计与授权：病历审计追踪与事前授权流程核查。

## 使用示例

- 对这个数据处理场景做隐私影响评估。
- 按 HIPAA 核对这套流程的义务与缺口。
- 拟定一份数据泄露响应清单与时间线。

## 包结构

- `agents/compliance-med.md`：专家定义
- `skills/compliance-med-playbook/`：执业资料与流程参考
- `skills/`：3 个技能（`compliance-med-playbook`、`data-privacy-workflow`、`hipaa-compliance-workflow`）
- `skills/compliance-med-playbook/references/commands/`：命令流程参考文档
- `skills/compliance-med-playbook/references/practice-profile/`：执业档案模板
- `skills/compliance-med-playbook/references/subagents/`：子代理定义
- `skills/compliance-med-playbook/references/mcp/`：MCP 配置（仅供查阅）
- `avatars/expert.png`：专家头像
- `LICENSE`：源插件许可证

## 注意事项

- 涉及真实泄露事件时提示用户同步法务与监管机构，不得私下处置。
- 输出为合规工作底稿，不构成法律意见或合规认证；法条、通知时限与罚则须标 `[待核验]` 并由法务复核。

## 命令说明

原命令保存在 `skills/compliance-med-playbook/references/commands/`，仅作流程参考，不注册为 WorkBuddy 斜杠命令。
