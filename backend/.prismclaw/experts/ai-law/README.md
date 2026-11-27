# AI 合规专家

分类 AI 用例、评估制度影响、生成 AIA，并审查供应商条款差距。

## 类型

Agent 型（单个 AI 专家），expertType = agent。

## 能力

- 用例分类：按注册表把用例分级并匹配适用制度。
- 影响评估：跨制度评估新用例的影响与义务。
- AIA 生成：按模板产出算法影响评估。
- 供应商条款：识别数据训练、责任与审计权差距。

## 使用示例

- 按我们的注册表对这些 AI 用例做分类与影响评估。
- 为这个自动化决策功能生成算法影响评估草稿。
- 审查这份供应商 AI 条款中的数据训练与责任差距。

## 包结构

- `agents/ai-law.md`：专家定义
- `skills/aig-ai-governance-workflow/`：AI 盘点、供应商审查、用例分级、影响评估与制度监控的分流入口
- `skills/ai-law-playbook/`：执业资料与流程参考
- `skills/ai-law-playbook/references/commands/`：命令流程参考
- `avatars/expert.png`：专家头像
- `LICENSE`：许可证

技能合计 2 个。

## 注意事项

- 涉及自动化决策、生物识别等敏感场景时，主动提示升级人工复核。
- 法条、案例、备案期限、阈值等具体法律事实，未经检索工具核实的一律标注 [待核验]。

## 命令说明

命令流程保存在 `skills/ai-law-playbook/references/commands/`，仅作流程参考，不注册为 系统注册命令。
