# 商事合同专家

按剧本审查供应商协议与NDA，追踪续约并输出业务可读的风险摘要。

## 类型

Agent 型（单个 AI 专家），expertType = agent。

## 来源

独立专家包，与其他专家平级。

## 能力

- NDA 红黄绿分级：对入站保密协议快速分类，只把需要律师时间的案件升级。
- 协议按剧本审查：供应商协议、SaaS 订阅、框架协议按 Playbook 定位风险条款。
- 续约与通知期限：追踪续约、取消通知窗口，提前触发行动。
- 升级与业务摘要：按审批人路由升级事项，输出业务可读摘要。

## 使用示例

- 按我们的剧本审这份 SaaS 协议，标出红线条款并给出业务可读摘要。
- 这份 NDA 是绿黄红哪一级？依据是什么？
- 列出未来 90 天内需要续约或发通知的协议。

## 包结构

- `agents/contract-law.md`：专家定义
- `skills/contract-law-playbook/`：执业资料与流程参考（原根级 references 已折叠至此）
- `skills/`：7 个技能（`commercial-renewal-workflow`、`commercial-review-workflow`、`contract-law-playbook`、`nda-review`、`saas-msa-review`、`stakeholder-summary`、`vendor-agreement-review`）
- `skills/contract-law-playbook/references/commands/`：原命令流程
- `skills/contract-law-playbook/references/subagents/`：原始子代理
- `avatars/expert.png`：专家头像
- `LICENSE`：源插件许可证

## 注意事项
- 不得在未核实法律依据时承诺条款的法律效力。

## 命令说明

原命令保存在 `skills/contract-law-playbook/references/commands/`，仅作流程参考，不注册为 系统注册命令。
