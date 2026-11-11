<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# Legal Builder Hub Plugin

社区法律技能发现和安装。浏览 GitHub 注册中心（lpm-skills、[其他注册中心——通过 /legal-builder-hub:registry-browser 添加] 等）、安装并自动更新、在你的其他法律 plugins 中显示相关社区技能。冷启动访谈就是入门包推荐器——询问你的执业类型，推荐安装什么。

**每个社区技能在安装前都原始显示，扫描提示注入模式，并根据法律技能设计框架进行评估。Plugin 帮助你发现和评估；你决定信任什么。**

## 适用对象

使用其他法律 plugins 的每个人。这是应用商店。

## 首次运行：冷启动

询问你的执业类型、行业、团队规模、工具舒适度。推荐匹配的社区技能入门包。安装你选择的那些。

```
/legal-builder-hub:cold-start-interview
```

你的配置存储在 `~/.prismclaw/plugins/legal-chineselaw/legal-builder-hub/PRISMCLAW.md`，在 plugin 更新后保留。

## 安全态势

已安装的社区技能在运行时可以访问你的客户数据、事项文件和团队的操作手册。Hub 将每次安装和每次更新都视为信任决策。四层防御，任何一层都不足以单独防御：

- **允许列表（管理员控制）：** `~/.prismclaw/plugins/legal-chineselaw/legal-builder-hub/allowlist.yaml` 声明社区技能可以使用哪些注册中心、发布者和 MCP 连接器。`permissive` 模式（默认）对任何不在列表中的内容发出警告；`restrictive` 模式（推荐给律所/企业部署）拒绝它。在安装程序读取任何第三方内容之前检查允许列表。参见 `skills/skill-installer/references/allowlist.md` 了解架构。
- **原始来源，而非摘要：** 在写入任何内容之前，安装程序向你显示完整的原始 `SKILL.md`——而非 AI 摘要。摘要是便利；做手脚的技能必须在原始显示会展示的文本中做。
- **启发式扫描：** 安装程序和 `skills-qa` 都扫描技能以查找提示注入模式（覆盖/权限主张、超出范围的读写、外部 URL、隐藏 unicode、shell 执行、凭证请求）。这些是 AI 启发式扫描，明确标记为这样——干净的扫描不是安全审计，它是让你自己阅读文本的提示。
- **每次都需要人工批准：** 没有新输入的 `yes` 就不会向磁盘写入任何内容。批准不会从先前消息推断。为了深度防御，安装程序建议在只读子代理中运行获取/分析，以便只有在批准后才能使用写入功能。

更新使用相同态势：自动更新程序固定到提交 SHA（而非可变标签），显示完整差异（包括 hook 和 MCP 更改），并要求每次更新都明确批准。没有自动应用模式。

如果技能在安装后出问题：`/legal-builder-hub:disable [skill]` 在不删除文件的情况下使其静默；`/legal-builder-hub:uninstall [skill]` 完全移除它。两者都仅限于通过此 hub 安装的社区技能——它们拒绝触及第一方 plugin 技能。

## 先决条件

- registry-sync agent 的 Slack 通知需要在你的环境中配置 Slack MCP 服务器。没有它，agent 将其摘要写入文件。
- `~/.prismclaw/plugins/legal-chineselaw/legal-builder-hub/PRISMCLAW.md` 中的默认注册中心列表除了 `lpm-skills` 外都是空的。通过 `/legal-builder-hub:registry-browser` 或编辑 `~/.prismclaw/plugins/legal-chineselaw/legal-builder-hub/PRISMCLAW.md` 添加你信任的注册中心。

## Commands

| Command | 功能 |
|---|---|
| `/legal-builder-hub:cold-start-interview` | 执业档案 + 入门包推荐 |
| `/legal-builder-hub:registry-browser [query]` | 在观察的注册中心中搜索技能 |
| `/legal-builder-hub:skill-installer [skill]` | 安装社区技能 |
| `/legal-builder-hub:auto-updater` | 检查已安装技能的更新 |
| `/legal-builder-hub:related-skills-surfacer` | 根据你一直在做的事情推荐技能 |
| `/legal-builder-hub:skills-qa [skill]` | 在安装前根据法律技能设计框架评估技能 |
| `/legal-builder-hub:disable [skill]` | 在不删除文件的情况下禁用已安装的社区技能 |
| `/legal-builder-hub:uninstall [skill]` | 卸载通过 hub 安装的社区技能 |

## Skills

| Skill | 目的 |
|---|---|
| **cold-start-interview** | 执业档案 → 入门包 |
| **registry-browser** | 在观察的注册中心中搜索 |
| **skill-installer** | 允许列表把关、获取、显示原始 SKILL.md、信任检查、QA、安装社区技能 |
| **uninstall** | 卸载通过 hub 安装的社区技能（第一方 plugin 技能不可触碰） |
| **disable** | 在不删除文件的情况下禁用社区技能；稍后重新启用 |
| **skill-manager** | 参考：`uninstall` 和 `disable` 技能使用的详细卸载/禁用/重新启用工作流 |
| **skills-qa** | 根据法律技能设计框架评估技能——设计、失败模式、信任面，以及提示注入启发式扫描 |
| **auto-updater** | 检查更新；显示差异和信任审查；仅在明确批准时应用 |
| **related-skills-surfacer** | 在任务后显示相关社区技能（直接或通过 hook） |

## 交互式命令 vs 计划代理

上面的命令在你调用时运行——用于当你处理事项时。下面的代理按计划运行——用于在你不注意时移动的内容：

| Agent | 观察什么 | 默认节奏 |
|---|---|---|
| **registry-sync** | 观察的注册中心中寻找新的和更新的技能；根据更新偏好发布通知 | 每周 |

## 观察的注册中心（默认）

默认允许列表随附我们预先配置审查的社区注册中心。编辑 repo 中的 `references/allowlist-default.yaml`，或你的每个安装的允许列表 `~/.prismclaw/plugins/legal-chineselaw/legal-builder-hub/allowlist.yaml`，以添加、删除或在限制和宽松模式之间切换。

- **lpm-skills** ——法律项目管理（Scott Margetts / LegalOps Consulting）—— `github.com/legalopsconsulting/lpm-skills`
- **Lawvable / awesome-legal-skills** ——精选的法律工作 AI agent 技能列表—— `github.com/lawvable/awesome-legal-skills`
- **Lawvable / agent-skills** ——精选的法律工作 agent 技能集合—— `github.com/lawvable/agent-skills`
- 通过 `/legal-builder-hub:registry-browser` 或编辑允许列表添加你自己的

## 它如何学习

你在 `~/.prismclaw/plugins/legal-chineselaw/legal-builder-hub/PRISMCLAW.md` 的执业档案不是静态的——它会随着你使用 plugin 而改进。Hub 在每次 `/legal-builder-hub:registry-browser` 和 `/legal-builder-hub:related-skills-surfacer` 时重新读取它，因此调整你的执业类型、行业或观察的注册中心会改进未来的推荐。当你的工作变动时，直接编辑文件或重新运行 `/legal-builder-hub:cold-start-interview --redo`。

## 注意事项

- 社区技能在安装前被阅读。你在接受之前看到 **原始** SKILL.md——而非摘要。
- 自动更新默认关闭。如果你信任来源，可按技能开启。
- related-skills-surfacer 在其他 plugins 内部运行：当你做任务时，它检查社区是否有相关内容。
- 企业/律所部署：在 `allowlist.yaml` 中设置 `mode: restrictive` 并填充 `registries`、`publishers` 和 `connectors` 列表。在限制模式下，安装程序拒绝从非列出来源获取、分析或安装任何内容。
