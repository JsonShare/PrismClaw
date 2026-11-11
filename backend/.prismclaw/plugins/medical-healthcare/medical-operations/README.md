# 医疗运营 Plugin

跨三个核心运营领域的工作流：病历管理、保险理赔和库存管理。

## 目标用户

| 角色 | 活跃模块 |
|---|---|
| **病历管理员** | 病历管理 |
| **保险协调员** | 保险理赔 |
| **库存/药剂管理员** | 库存管理 |
| **运营总监** | 全部三个 |

## 首次运行

```
/operations-cold-start-interview
```

## 命令

| 命令 | 作用 |
|---|---|
| `/operations-cold-start-interview` | 模块化冷启动 |
| `/medical-operations:record-management` | 病历管理工作流 |
| `/medical-operations:insurance-claim` | 保险理赔处理 |
| `/medical-operations:inventory-check` | 库存检查 |

## Skills

| Skill | 模块 | 目的 |
|---|---|---|
| **record-management-workflow** | 病历管理 | 病历归档、调取和质量管理 |
| **insurance-claims-workflow** | 保险理赔 | 索赔提交、追踪和申诉 |

## Agents

| Agent | 模块 | 它监视什么 | 默认节奏 |
|---|---|---|---|
| **appointment-watcher** | 病历管理 | 预约变更和病历完成状态 | 每日 |

## 安全声明

本插件生成的所有内容均为运营辅助工具。临床决策和合规要求须由持相应资质的人员审核。