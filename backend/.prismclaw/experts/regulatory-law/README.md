# 监管政策专家

监视权威监管源、比对政策库、跟踪意见期限与差距，并编写团队周报摘要。

## 类型

Agent 型（单个 AI 专家），expertType = agent。

## 能力

- 监管源监视：跟踪权威来源的新规、征求意见与执法动态。
- 规则比对：将新规与内部政策库逐条对比。
- 期限与差距：跟踪意见截止日与未闭合的合规差距。
- 周报编写：产出团队周一可读的监管摘要。

## 使用示例

- 汇总本周与我们业务相关的新规与征求意见稿。
- 把这份新规与我们的政策库逐条比对，列出差异。
- 写一份团队周一可读的监管动态摘要。

## 包结构

- `agents/regulatory-law.md`：专家定义
- `skills/regulatory-monitor-workflow/`：监管资讯监控、入站来文分流与差距识别的分流入口
- `skills/regulatory-policy-workflow/`：政策比对、制度重写与征求意见提交的分流入口
- `skills/gap-surfacer/`：内规差异台账、到期提醒与状态报告
- `skills/regulatory-law-playbook/`：执业资料与流程参考
- `skills/regulatory-law-playbook/references/commands/`：命令流程参考
- `skills/regulatory-law-playbook/references/subagents/`：子代理参考定义
- `skills/regulatory-law-playbook/references/gap-surfacer/`：差异与评议台账模板
- `avatars/expert.png`：专家头像
- `LICENSE`：许可证

技能合计 4 个。

## 注意事项

- 监管信息须附权威来源链接与生效日期，避免二手转述。
- 法条、案例、截止日期、阈值等具体法律事实，未经检索工具核实的一律标注 [待核验]。

## 命令说明

命令流程保存在 `skills/regulatory-law-playbook/references/commands/`，仅作流程参考，不注册为 系统注册命令。
