---
name: disable
description: '禁用通过中心安装的社区 skill 而不删除其文件。 当用户想要暂时安静社区 skill（"disable [skill]"）、 在保留其配置的同时停止其
  hooks 触发，或重新启用之前禁用的 skill 时使用。

  '
argument_hint: '[skill name]'
category: 技能管理
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /disable

从 skill-manager 参考 skill 对命名 skill 运行 `disable` 工作流。

禁用的作用：

- 将 skill 的 `SKILL.md` 重命名为 `SKILL.md.disabled`，以便 PrismClaw 不再将其发现为活跃 skill。文件、引用、模板和配置保持不变。
- 如果 skill 在 `hooks/hooks.json` 中提供 hooks，也将该文件重命名为 `hooks/hooks.json.disabled`，以便在 skill 禁用时不会触发自动触发器。
- 将操作记录到 `~/.prismclaw/plugins/legal-chineselaw/legal-builder-hub/install-log.yaml`。

安全规则：

1. **仅禁用通过此中心安装的社区 skills。** 与卸载相同的检查——咨询安装日志和 PRISMCLAW.md 安装表。
2. **永不禁用第一方插件 skill。** 禁区。
3. **重命名之前确认。** 显示路径，获得明确的 `yes`。

通过使用相同的 skill 名称再次运行命令来重新启用——skill-manager 工作流识别禁用的 skill 并将重命名翻转回来。

> 详细的卸载、禁用和重新启用工作流程位于 `skill-manager` 参考 skill 中——在进行实质性工作之前加载它。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
