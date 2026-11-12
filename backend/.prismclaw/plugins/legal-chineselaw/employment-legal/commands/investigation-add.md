---
name: investigation-add
description: '向开放调查添加数据——文档、访谈笔记或观察。根据记录的提取标准处理批次，提取重要项目，并记录所有已审查内容用于覆盖验证。当新证据、访谈笔记或文档制作送达开放调查时使用。

  '
argument_hint: '[事项名称或 slug，然后粘贴或附加数据]'
category: 劳动用工
---

<!--
This file is a Chinese translation and adaptation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
改造为中国劳动法语境。
-->


# /investigation-add

向开放调查日志添加数据。使用记录的提取标准处理文档批次，提取重要项目，记录所有已审查内容用于覆盖验证。

## 说明

1. 加载 `~/.prismclaw/assets/plugins/legal-chineselaw/employment-legal/PRISMCLAW.md`。
2. 加载 `internal-investigation` 参考 skill 并运行模式 2（添加数据）。
3. 处理后，显示提取比率和提取项目列表。
4. 如果数据覆盖检查清单项目，提示更新来源检查清单。

**中国劳动法背景下的证据处理注意事项：**

- **电子证据合规** — 微信聊天记录、钉钉消息、企业邮件等电子证据在中国劳动仲裁中普遍采用，但须注意：截图的真实性可能被质疑，必要时可做公证；务必保留完整对话记录而非片段截取。
- **员工书面说明** — 员工自愿书写的情况说明是重要证据；须确保是自愿书写（不能有强迫行为），否则可能被认定为无效。
- **录音录像** — 依法取得的录音录像可作为证据；秘密录音在中国劳动仲裁中一般可采，但须注意：在谈话前事先告知可提高证据效力。
- **个人信息保护** — 调查过程中处理员工个人信息须符合《个人信息保护法》要求；敏感个人信息（如健康信息、生物识别信息）须特别谨慎。

## 示例

```
/employment-legal:investigation-add [事项名称]
[粘贴访谈笔记]
```

```
/employment-legal:investigation-add [事项名称]
[附加邮件导出/聊天记录截图]
```

> 详细的提取流程、日志条目格式、提取比率规则和来源检查清单跟踪位于 `internal-investigation` 参考 skill 中——在进行实质性工作之前加载它。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
