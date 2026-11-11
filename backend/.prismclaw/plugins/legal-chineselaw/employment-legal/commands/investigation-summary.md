---
name: investigation-summary
description: '从保密调查备忘录起草面向特定受众的摘要——HR版本、领导层版本或外部律师简报版本。当调查备忘录需要传达给不应看到完整保密工作底稿的受众时使用。

  '
argument_hint: '[事项名称] [受众: hr / leadership / outside-counsel]'
category: 劳动用工
---

<!--
This file is a Chinese translation and adaptation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
改造为中国劳动法语境。
-->


# /investigation-summary

从保密调查备忘录起草精简的、面向受众的摘要。HR 摘要不包含律师的法律分析。领导层摘要为高级别概览。外部律师简报包含完整上下文。

## 说明

1. 加载 `internal-investigation` 参考 skill 并运行模式 5（受众摘要）。
2. 如果备忘录尚不存在，提供先起草备忘录的选项。
3. HR 摘要不包含律师法律判断、可信度评估方法或法律风险敞口分析。

**中国劳动法语境下各受众摘要注意事项：**

**HR 摘要：**
- 只包含：调查过程说明、主要事实发现、拟议处理建议（不含法律分析）
- 不包含：律师对证据强弱的判断、法律风险评估、可信度方法
- 须提醒：HR 不得以任何方式泄露调查结果给无关人员；须妥善保管调查记录

**领导层摘要：**
- 只包含：事件概述、调查结论（是否属实）、处理建议、潜在风险提示（概述级别）
- 不包含：详细证据分析、个别证人陈述细节
- 提醒：处理决定须通过正式程序（人事委员会/合规委员会）作出，不得口头决定

**外部律师简报：**
- 包含完整调查背景：事件经过、调查方法、证据清单、证人陈述摘要、可信度评估
- 包含律师内部法律分析：处理建议的法律依据、风险评估、诉讼预测
- 此版本须标注"律师工作底稿 保密"

## 示例

```
/employment-legal:investigation-summary [事项名称] hr
```

```
/employment-legal:investigation-summary [事项名称] leadership
```

```
/employment-legal:investigation-summary [事项名称] outside-counsel
```

> 详细的受众剥离规则和摘要模板位于 `internal-investigation` 参考 skill 中——在进行实质性工作之前加载它。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
