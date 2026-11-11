---
name: supervisor-review-queue
description: '教授审查队列——学生输出在这里等待教授批准，然后再发送给客户或法院。 仅当在设置时选择了"正式审查队列"监督风格时才激活；否则休眠。 当教授想要查看等待审查的内容、批准、编辑后批准或返回项目时使用。

  '
argument_hint: '[--approve ID | --return ID ''note'' | --edit ID]'
category: 法律诊所
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /supervisor-review-queue

1. 检查 `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/PRISMCLAW.md` → 监督风格。如果不是"正式审查队列"：说明诊所设置为 [flags/lighter-touch]，不存在正式队列，以及如何切换。
2. 使用以下工作流。
3. 默认：显示等待的内容，按紧急程度、按学生。
4. 操作：批准 / 编辑后批准 / 带备注返回。全部记录。

```
/legal-clinic:supervisor-review-queue
```

```
/legal-clinic:supervisor-review-queue --approve Q-003
```

```
/legal-clinic:supervisor-review-queue --return Q-004 "检查服务要求——当地规则已更改"
```

---

# 主管审查队列（可选）

## 目的

一些诊所想要一个正式门槛：学生起草，教授审查，输出发布。其他人认为这太死板——他们通过案件轮查和一对一来监督，而不是通过队列。

**此 skill 仅在 `~/.prismclaw/plugins/legal-chineselaw/legal-clinic/PRISMCLAW.md` → 监督风格为"正式审查队列"时才激活。**否则它休眠——冷启动访谈询问教授想要哪个模型，这是三个选项之一。

是否使用正式审查工作流确实是诊所采用的一个开放问题。这取决于学生经验水平、案件量以及教授已经如何运行监督。教授在设置时决定，可以稍后更改。

## 加载上下文

`~/.prismclaw/plugins/legal-chineselaw/legal-clinic/PRISMCLAW.md` → 监督风格。如果不是"正式审查队列"：回复"诊所设置为 [flags/lighter-touch] 监督——没有正式队列。[教授] 通过 [诊所的现有结构] 审查。要切换到正式队列，编辑 PRISMCLAW.md → 监督风格。"

如果启用了正式队列 → 读取标记触发器并继续。

## 队列

位于 `${PRISMCLAW_PLUGIN_ROOT}/references/supervisor-review-queue/review-queue.yaml`。每个条目：

```yaml
- id: Q-001
  type: "draft"  # intake | draft | memo | status | client-letter
  client: "[名称或 ID]"
  student: "[名称]"
  submitted: [时间戳]
  flags:
    - rule: "法院提交"
      detail: "驱逐答复——始终排队"
  content_path: "[文档路径]"
  status: "pending"  # pending | approved | edited-approved | returned
```

## 模式

### 等待什么

```markdown
## 审查队列——[日期]

**待处理：** [N] | **最旧的：** [N] 小时

### 🔴 截止期限敏感
| ID | 类型 | 客户 | 学生 | 标记原因 | 等待时间 |
|---|---|---|---|---|---|

### 标准
[相同表格]

### 按学生
[细分——发现模式：谁排了很多，谁可能需要检查]
```

### 审查项目

显示完整内容 + 标记原因 + 学生备注。

### 批准 / 编辑后批准 / 返回

- **批准：** 状态 → 已批准，学生通知，已记录。
- **编辑后批准：** 教授内联编辑，批准版本是编辑版本，原始保存在日志中以便学生看到差异（教学时刻）。
- **返回：** 带备注。学生修改并重新提交。

## 记录

每个操作都记录。批准日志是诊所记录——它们记录许可的律师、事务律师、大律师或其他管辖诊所的授权法律专业人员在学生工作发送给客户或法院之前审查了学生工作。这对诊所自身的合规和学生评估很重要。

## 教学信号

队列也是数据。返回模式（"学生 X 不断错过服务要求"）是教练对话。编辑模式（"每个人的要求信都太长"）是下学期 `/ramp` 更新。

## 此 skill 不做什么

- **除非教授选择否则运行。** 它是三种监督模型之一，不是唯一的。
- **自动批准。** 教授批准。
- **替代诊所的现有监督结构。** 它是工作产品的门槛，而不是案件轮查、一对一或观看学生行动的替代品。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
