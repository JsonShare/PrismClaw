---
name: gaps
description: 未整改处理的内规差异台账——已标记但尚未关闭的项目。用户问"开放的内规差异有什么"、"内规差异台账"、"整改状态"，或想关闭（--close
  GAP-ID）/ 风险接受（--accept GAP-ID）某条已跟踪内规差异时使用。
argument_hint: '[可选：--close GAP-ID | --accept GAP-ID]'
category: 监管合规
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0

中国法改造说明：2026-06
- 术语更新：gap → 内规差异，gap tracker → 内规差异台账
-->


# /gaps

1. 读内规差异台账 `~/.prismclaw/plugins/legal-chineselaw/regulatory-legal/gap-tracker.yaml`。
2. 如果 `--close`：标记内规差异为关闭，附解决方案说明。
3. 如果 `--accept`：记录风险接受理由和接受人，状态 → risk-accepted。
4. 否则：按年龄和重要性报告开放的内规差异。

> 详细的台账结构定义、状态报告格式、所有者通知逻辑（每次发送确认，无例外）、提醒节奏、关闭/风险接受模式以及后果行动关卡位于 **gap-surfacer** 参考技能中——在进行实质性工作之前加载它。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
