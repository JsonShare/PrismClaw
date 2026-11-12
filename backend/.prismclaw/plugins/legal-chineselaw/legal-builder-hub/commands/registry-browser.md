---
name: registry-browser
description: '搜索监控的注册表以查找社区法律 skills，显示匹配结果及其描述， 并提供在安装前查看完整 SKILL.md 的选项。当用户说"浏览"、
  "搜索 skills"、"查找一个 skill"、"有什么可用的"，或想要向监控列表 添加新注册表时使用。

  '
argument_hint: '[搜索查询]'
category: 技能管理
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /registry-browser

1. 加载 `~/.prismclaw/assets/plugins/legal-chineselaw/legal-builder-hub/PRISMCLAW.md` → 监控的注册表。
2. 使用以下工作流。
3. 搜索每个注册表。显示匹配结果及其描述。
4. 提供查看任何匹配结果的完整 SKILL.md 的选项。

---

## 目的

在监控的注册表中查找 skills。搜索、预览、决定。

## 加载上下文

`~/.prismclaw/assets/plugins/legal-chineselaw/legal-builder-hub/PRISMCLAW.md` → 监控的注册表列表。

## 工作流

### 步骤 1：获取注册表索引

对于每个监控的注册表：

- GitHub 仓库：获取 `skills/` 目录列表和每个 `SKILL.md` 的 frontmatter（name + description）。
- 市场式注册表：获取索引。

在本地缓存索引（`${PRISMCLAW_PLUGIN_ROOT}/references/registry-browser/registry-cache.json`）以便浏览快速。如果缓存超过 7 天或用户请求则刷新。

### 步骤 2：搜索

将查询与 skill 名称和描述匹配。简单的关键字匹配即可——这些足够小，模糊搜索没有必要。

另外：如果注册表按类别组织 skills，也支持按类别浏览。

### 步骤 3：展示匹配结果

```markdown
## 搜索："[query]"

**在 [M] 个注册表中找到 [N] 个 skills：**

### [skill-name]
**来自：** [注册表名称]
**描述：** [来自 frontmatter]
[查看完整 SKILL.md] [安装]

### [skill-name]
[...]
```

### 步骤 4：预览

在"查看完整 SKILL.md"时：获取并显示整个文件。用户在决定安装之前阅读。没有意外。

### 步骤 5：添加注册表

如果用户有一个不在监控列表中的注册表 URL：

1. 获取它，验证它是一个 skills 仓库（有 `skills/` 或 `.plugin/`）
2. 显示其中有什么
3. 确认后添加到 `~/.prismclaw/assets/plugins/legal-chineselaw/legal-builder-hub/PRISMCLAW.md` → 监控的注册表

## 默认注册表

- **lpm-skills** — 14 个法律项目管理 skills。与执业领域无关。良好的起点。
- 随着生态系统的发展，可添加其他注册表。

## 此 skill 不做什么

- 安装任何东西。它只浏览。skill-installer 负责安装。
- 评级或审查 skills。它向您展示 SKILL.md；您来判断。
- 搜索整个互联网。仅搜索监控的注册表。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
