# 公司与并购专家

运行尽调、表格审查与披露时间表，起草董事会文件并跟踪实体合规。

## 类型

Agent 型（单个 AI 专家），expertType = agent。

## 来源

独立专家包，与其他专家平级。

## 能力

- 尽调问题抽取：从资料室材料抽取问题，按主题与严重度归类并保留引用。
- 表格化审查：按披露表列格式输出带引用的逐项审查结论。
- 治理文件起草：董事会同意、书面同意、会议纪要与重大合同附表。
- 交割与实体合规：生成交割检查清单并跟踪各辖区实体合规期限。

## 使用示例

- 对【目标公司】做交割前法律尽调，输出问题清单与披露时间表。
- 按内部格式起草这份董事会同意与会议纪要。
- 列出各实体今年到期的合规申报与截止日。

## 包结构

- `agents/corporate-law.md`：专家定义
- `skills/corporate-law-playbook/`：执业资料与流程参考（原根级 references 已折叠至此）
- `skills/`：7 个技能（`ai-tool-handoff`、`board-minutes`、`corporate-governance-workflow`、`corporate-law-playbook`、`corporate-ma-workflow`、`deal-team-summary`、`tabular-review`）
- `skills/corporate-law-playbook/references/commands/`：原命令流程
- `skills/corporate-law-playbook/references/subagents/`：原始子代理
- `avatars/expert.png`：专家头像
- `LICENSE`：源插件许可证

## 注意事项

- 治理文件须标明为公司内部草稿，需董事/授权签署人确认。

## 命令说明

原命令保存在 `skills/corporate-law-playbook/references/commands/`，仅作流程参考，不注册为 系统注册命令。
