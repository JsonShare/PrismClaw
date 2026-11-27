# 数据隐私专家

分类处理活动、生成 PIA、审查 DPA、按时限起草 DSAR 响应并监控政策漂移。

## 类型

Agent 型（单个 AI 专家），expertType = agent。

## 能力

- 处理活动分类：对数据处理场景做分级并确定适用制度。
- PIA 生成：按模板产出隐私影响评估。
- DPA 审查：分别按控制者/处理者视角审查条款缺口。
- DSAR 与漂移：在法定时限内起草响应，并监控政策与实践差异。

## 使用示例

- 为这个新数据处理活动生成隐私影响评估草稿。
- 从控制者视角审查这份 DPA 的条款缺口。
- 起草一份 DSAR 响应，并标出法定时限。

## 包结构

- `agents/privacy-law.md`：专家定义
- `skills/privacy-compliance-workflow/`：PIA、DPA、DSAR、用例分级与法规缺口分析的分流入口
- `skills/privacy-policy-workflow/`：隐私政策与监管变化监控的分流入口
- `skills/privacy-law-playbook/`：执业资料与流程参考
- `skills/privacy-law-playbook/references/commands/`：命令流程参考
- `skills/privacy-law-playbook/references/practice-profile/`：执业档案模板
- `skills/privacy-law-playbook/references/mcp/`：MCP 配置记录（仅供查阅）
- `avatars/expert.png`：专家头像
- `LICENSE`：许可证

技能合计 3 个。

## 注意事项

- 跨境传输与敏感个人信息处理需额外提示用户核实当地要求。
- 法条、案例、截止日期、阈值等具体法律事实，未经检索工具核实的一律标注 [待核验]。

## 命令说明

命令流程保存在 `skills/privacy-law-playbook/references/commands/`，仅作流程参考，不注册为 WorkBuddy 斜杠命令。
