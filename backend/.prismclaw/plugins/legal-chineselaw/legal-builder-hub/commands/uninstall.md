---
name: uninstall
description: '卸载通过中心安装的社区 skill。删除文件前确认，拒绝触碰第一方插件 skills，并记录每个操作。当用户想要完全移除社区 skill
  （"uninstall [skill]"、"remove this skill"）而不是仅仅禁用时使用。

  '
argument_hint: '[skill 名称]'
category: 技能管理
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /uninstall

对命名 skill 运行 skill-manager 参考 skill 的 `uninstall` 工作流。

安全规则：

1. **仅卸载通过此中心安装的社区 skills。** 检查 `~/.prismclaw/plugins/legal-chineselaw/legal-builder-hub/install-log.yaml` 和 PRISMCLAW.md 已安装入门包表。如果 skill 未记录在那里，拒绝并告诉用户。
2. **永不卸载第一方插件的 skill。** 随 legal-chineselaw 附带的 12 个核心插件不受此命令约束。如果命名的 skill 解析到这些插件之一的路径，拒绝。
3. **删除文件前确认。** 向用户展示将被删除的每个路径。仅在明确 `yes` 后继续。
4. **记录卸载。** 以 `action: uninstall` 和时间戳追加到 `install-log.yaml`，以便审计跟踪完整。

如果用户想要停止 skill 运行但保留文件（例如，为了日后重新启用，或保留配置），建议改用 `/legal-builder-hub:disable`。

> 详细的卸载、禁用和重新启用工作流位于 `skill-manager` 参考 skill 中——在进行实质性工作之前加载它。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
