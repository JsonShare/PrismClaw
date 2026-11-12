---
name: expansion-update
description: '更新进行中的省市或国家扩张劳动法律合规项目状态——重新计算现已解除阻止的项目，标记任何逾期项目，并提示下一个优先事项。当自上次会话以来有新进展且扩张追踪器需要反映当前状态时使用。

  '
argument_hint: '[省市或国家名称]'
category: 劳动用工
---

<!--
This file is a Chinese translation and adaptation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
改造为中国劳动法语境。
-->


# /expansion-update

返回到打开的扩展追踪器并根据上次会话后发生的情况更新项目状态。重新计算现在已解除阻止的项目，标记任何逾期项目，并提示下一个优先事项。

## 说明

1. 加载 `~/.prismclaw/assets/plugins/legal-chineselaw/employment-legal/PRISMCLAW.md`。

2. 识别追踪器文件：`~/.prismclaw/assets/plugins/legal-chineselaw/employment-legal/expansion-[slug].yaml`。如果不存在，响应："未找到 [省市/国家] 的扩展追踪器。运行 `/employment-legal:expansion-kickoff [省市/国家]` 来启动一个。"

3. 读取追踪器。显示当前状态：

```
[省市/国家] 扩展 — 最后更新于 [日期]
待处理：[N] | 进行中：[N] | 已完成：[N] | 受阻：[N]

下一个优先事项（截止日期最早或依赖性最高的待处理项目）：
  [项目] — 负责人：[负责人]
  [项目] — 负责人：[负责人]
  [项目] — 负责人：[负责人]
```

4. 在单个提示中询问更新——不要逐个询问每个项目：

   > 自上次查看以来哪些项目发生了变化？告诉我发生了什么变化（例如，"劳动合同模板已本地化完成"，"社保开户已完成"，"工时制度审批材料已准备好，等待提交"）。你也可以添加新项目或更改截止日期。

5. 将更新应用于追踪器文件。对于任何新标记为 `done` 的项目，检查它是否解除了其他项目的阻止并将这些项目标记为现在可执行。

6. 如果任何项目的截止日期已过且仍然为 `open` 或 `in-progress`，标记它：

```
⚠️ 逾期：[项目] — 截止日期 [日期]，负责人：[负责人]
```

7. 写入更新后的追踪器。确认：

```
追踪器已更新 — [N] 个项目已关闭，[N] 个仍待处理。
下一个优先事项：[顶级待处理项目]。
```

## 示例

```
/employment-legal:expansion-update 浙江省
```

```
/employment-legal:expansion-update 新加坡
```

```
/employment-legal:expansion-update
(如果存在多个追踪器，将询问哪个省市或国家)
```

> 详细的追踪器架构、项目状态规则和依赖逻辑在 `international-expansion` 参考 skill 中——在进行实质性工作之前加载它。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
