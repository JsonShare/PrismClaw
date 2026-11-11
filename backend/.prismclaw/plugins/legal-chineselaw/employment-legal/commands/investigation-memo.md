---
name: investigation-memo
description: '从调查日志起草或更新保密调查备忘录。当调查进展到足以撰写首次备忘录草稿，或新数据已添加且现有草稿需要更新时使用。

  '
argument_hint: '[事项名称]'
category: 劳动用工
---

<!--
This file is a Chinese translation and adaptation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
改造为中国劳动法语境。
-->


# /investigation-memo

从日志起草保密调查备忘录的首次草稿，或在添加新数据时更新现有草稿。

## 说明

1. 加载 `internal-investigation` 参考 skill 并运行模式 4（起草或更新备忘录）。
2. 如果是首次起草，当高优先级来源在检查清单中仍然开放时发出警告。
3. 如果是更新，在重写之前展示变更内容。
4. 所有输出标记为：保密 — 律师工作底稿 — 仅供律师及其授权委托人使用。

**中国劳动法语境下调查备忘录的特殊要求：**

- **用于支持解除决定的调查** — 备忘录须明确：违纪事实是否清晰、规章制度依据是否充分（制度合法性+公示告知证明）、处分程序是否完整（工会通知+送达）。如果调查不支持解除，须明确说明替代处理建议。
- **可信度评估** — 对关键证人陈述作可信度分析（陈述是否前后一致、是否有其他证据印证、是否有利害关系影响陈述真实性）。
- **法律风险评估** — 基于调查结果，评估：①如处分是否构成违法解除风险；②如不处分是否构成对其他员工的管理失职；③是否存在打击报复投诉人的风险。
- **证据闭环检查** — 确认证据链是否完整：违纪事实-规章制度依据-程序履行-处分决定-送达记录。

## 示例

```
/employment-legal:investigation-memo [事项名称]
```

```
/employment-legal:investigation-memo [事项名称]
（如果备忘录已存在则更新）
```

> 详细的备忘录结构、可信度评估框架和更新规则位于 `internal-investigation` 参考 skill 中——在进行实质性工作之前加载它。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
