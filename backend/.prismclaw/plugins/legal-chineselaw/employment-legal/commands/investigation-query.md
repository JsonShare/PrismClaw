---
name: investigation-query
description: '针对开放调查日志提问——证人说了什么、陈述在哪里冲突、存在什么差距、每个问题上最强证据是什么。当律师需要查询调查记录而无需重新阅读每个条目时使用。

  '
argument_hint: '[事项名称] [问题]'
category: 劳动用工
---

<!--
This file is a Chinese translation and adaptation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
改造为中国劳动法语境。
-->


# /investigation-query

针对调查日志回答问题——证人说了什么、陈述在哪里冲突、存在什么差距、每个问题上最强证据是什么。

## 说明

1. 加载 `internal-investigation` 参考 skill 并运行模式 3（查询）。
2. 始终在答案中引用日志条目 ID。
3. 如果日志中没有任何与问题相关的内容，明确说明——"我在此调查日志中没有看到任何关于 [主题] 的信息（已审查 [N] 个条目）"——并提供将其标记为差距的选项。

## 示例

```
/employment-legal:investigation-query [事项名称]
被投诉人对十二月团队晚餐事件说了什么？
```

```
/employment-legal:investigation-query [事项名称]
投诉人和被投诉人的陈述在哪里冲突？
```

```
/employment-legal:investigation-query [事项名称]
支持投诉人陈述的最强证据是什么？
```

```
/employment-legal:investigation-query [事项名称]
我们还需要什么证据才能形成完整的调查闭环？
```

> 详细的日志查询流程、引用规则和差距标记模板位于 `internal-investigation` 参考 skill 中——在进行实质性工作之前加载它。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
