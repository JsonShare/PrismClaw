# 医疗合规 Plugin

跨三个核心合规领域的工作流：HIPAA 合规审查、数据隐私管理和审计追踪。仅激活适用于你角色的模块。

**每个输出都是供合规专业人员审阅的草稿——不构成法律意见或合规认证。**

## 目标用户

| 角色 | 活跃模块 |
|---|---|
| **合规专员/DPO** | 全部三个 |
| **隐私官** | 数据隐私 + 审计追踪 |
| **质控人员** | 审计追踪 |
| **法务** | HIPAA 合规 + 数据隐私 |

## 首次运行

```
/compliance-cold-start-interview
```

## 命令

| 命令 | 作用 |
|---|---|
| `/compliance-cold-start-interview` | 模块化冷启动 |
| `/medical-compliance:hipaa-review` | HIPAA 合规审查 |
| `/medical-compliance:data-breach-response` | 数据泄露响应流程 |
| `/medical-compliance:audit-trail` | 审计追踪和访问日志分析 |

## Skills

| Skill | 模块 | 目的 |
|---|---|---|
| **hipaa-compliance-workflow** | HIPAA 合规 | HIPAA 规则审查、合规差距分析 |
| **data-privacy-workflow** | 数据隐私 | 数据处理活动审查、隐私风险评估 |

## Agents

| Agent | 模块 | 它监视什么 | 默认节奏 |
|---|---|---|---|
| **privacy-auditor** | 审计追踪 | 数据访问模式和可疑活动 | 每日 |

## 安全声明

本插件生成的所有内容均为草稿，不构成法律意见或合规认证。在实施任何合规措施之前，必须咨询持相应资质的法律或合规专业人员。