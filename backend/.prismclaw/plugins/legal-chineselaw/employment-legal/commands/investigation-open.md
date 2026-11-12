---
name: investigation-open
description: '打开新的内部调查事项——运行接收、生成来源检查清单、创建持久调查日志。当投诉或指控出现且律师需要建立保密调查工作空间时使用。适用于骚扰投诉、歧视投诉、举报、违规行为调查等场景。

  '
argument_hint: '[指控的简要描述]'
category: 劳动用工
---

<!--
This file is a Chinese translation and adaptation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
改造为中国劳动法语境。
-->


# /investigation-open

打开新的调查事项——运行接收、生成来源检查清单、创建持久调查日志。

## 说明

1. 加载 `~/.prismclaw/assets/plugins/legal-chineselaw/employment-legal/PRISMCLAW.md`。
2. 加载 `internal-investigation` 参考 skill 并运行模式 1（打开）。
3. 如果具有相同 slug 的事项已存在，在覆盖前发出警告。

**中国劳动法背景下的内部调查注意事项：**

- **涉及违纪解除的调查** — 若调查结果可能用于"严重违纪"解除，须确保：调查程序合法（不得逼供）、证据可采（聊天记录、书面说明、证人证词等）、规章制度有相应依据、处分决定通过合法程序（工会通知、送达）。
- **职场骚扰投诉** — 须保护举报人，防止打击报复；了解相关省市的职场性骚扰规定（部分省市有专项规定）。
- **举报人保护** — 员工因举报单位违法行为而遭受不利对待，可能构成违法解除或打击报复，须特别谨慎。
- **调查文件保密** — 调查材料应标注保密，限制知悉范围；避免调查材料在仲裁或诉讼中被对方利用。

## 示例

```
/employment-legal:investigation-open
上海分公司一名经理被投诉骚扰下属。
```

```
/employment-legal:investigation-open
（skill 将询问详情）
```

> 详细的接收、保密形成要求、来源检查清单和日志模板位于 `internal-investigation` 参考 skill 中——在进行实质性工作之前加载它。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
