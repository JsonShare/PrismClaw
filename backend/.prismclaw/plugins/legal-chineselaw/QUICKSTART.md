<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# 快速开始

**60 秒。** 让您快速开始使用 plugin。

## 在 PrismClaw Cowork 中安装
1. [安装 PrismClaw Desktop](https://prismclaw.ai/download)
2. 获取 PrismClaw Cowork 访问权限
3. 按照下方视频中的说明操作：

https://github.com/user-attachments/assets/51394f0a-5277-4fe2-b81c-5c5e9ac876b5

## 在 PrismClaw 中安装

1. **打开 PrismClaw**（在您的终端中）或 **PrismClaw Cowork**（桌面应用）。不确定您拥有哪个？如果您有一个打开的终端窗口，里面运行着 PrismClaw，那就是 PrismClaw。

2. **添加市场。** 在 PrismClaw 中，输入 `/plugin marketplace add `（末尾加一个空格），然后**将解压后的 `legal-chineselaw` 文件夹拖到终端窗口**——它会自动填写路径。然后按 Enter。

   （或直接输入完整路径：`/plugin marketplace add ~/Downloads/legal-chineselaw`）

3. **安装您的 plugin。** 从下表中选择与您工作匹配的，然后：
   ```
   /plugin install privacy-legal@legal-chineselaw
   ```

4. **⚠️ 重启 PrismClaw。** 关闭并重新打开。此步骤不可省略——重启后 plugin 才会生效。

5. **运行设置。** 需要 2 分钟（快速启动）或 10-15 分钟（完整）。
   ```
   /privacy-legal:cold-start-interview
   ```

6. **连接研究工具。** 没有工具的话引用会标记为未核实。在 Cowork 中：设置 → 连接器 → 添加 CourtListener。在 PrismClaw 中：plugin 已在其配置中列出研究 MCP；第一次有 skill 需要它时会提示您授权。

## 安装为用户级别，而非项目级别

运行 `/plugin install` 时，您可能会被询问是仅为此项目安装还是为所有项目安装（用户级别）。**选择用户级别。**

这看起来反直觉：项目级别感觉更安全。但项目级别会阻止 plugin 读取项目文件夹之外的文件——您在 Downloads 中的提纲、Documents 中的合同、Dropbox 中的客户文件。大多数 skill 需要读取您的文件。用户级别不会给 plugin 任何额外的文件访问权限——plugin 只能读取您明确指向的文件或当前目录中的文件。它仅意味着 plugin 可以在任何文件夹而不是仅在一个文件夹中工作。

如果您已经安装为项目级别并想切换：`/plugin uninstall <plugin>`，然后从您的主目录运行 `/plugin install <plugin>@legal-chineselaw`。

## 哪个 plugin 适合我？

| 您是…… | 安装…… | 第一个命令 |
|---|---|---|
| 隐私律师 / DPO | `privacy-legal` | `/privacy-legal:use-case-triage` |
| 商业 / 合同律师 | `commercial-legal` | `/commercial-legal:review` |
| 公司法务 / 并购律师 | `corporate-legal` | `/corporate-legal:diligence-issue-extraction` |
| 劳动法律师 / HR 法律顾问 | `employment-legal` | `/employment-legal:wage-hour-qa` |
| 产品法律顾问 | `product-legal` | `/product-legal:is-this-a-problem` |
| IP 律师 / 专利代理人 | `ip-legal` | `/ip-legal:clearance` |
| 诉讼律师（内部或律所） | `litigation-legal` | `/litigation-legal:matter-intake` |
| 监管 / 合规顾问 | `regulatory-legal` | `/regulatory-legal:reg-feed-watcher` |
| AI 治理负责人 | `ai-governance-legal` | `/ai-governance-legal:use-case-triage` |
| 诊所督导（法学院） | `legal-clinic` | `/legal-clinic:cold-start-interview` |
| 法学院学生 | `law-student` | `/law-student:cold-start-interview` |
| 法律运营 / 寻找 skill | `legal-builder-hub` | `/legal-builder-hub:registry-browser` |

## 您安装了什么

每个 plugin 通过设置访谈了解您的 playbook，将其写入执业档案文件（`~/.prismclaw/plugins/legal-chineselaw/<plugin>/PRISMCLAW.md`），并且每个 skill 都会读取它。档案是您的——编辑它、重新运行设置，或告诉 skill 更新它。

**每个输出都是供律师审阅的草稿。** plugin 会标记其不确定的内容，按来源标记引用，并对任何不可逆的内容设置关卡。律师负责审阅、核实并承担责任。它们让审阅更快；它们不替代审阅。

## 盒子里有什么

12 个执业领域 plugin、5 个 managed-agent cookbook、16 个以上连接器。完整参考见 [README.md](README.md)。

## 卡住了？

- **安装后出现"Command not found"** → 您忘记了步骤 4。重启 PrismClaw。
- **提示"Run setup first"** → 在任何其他命令之前运行 `/<plugin>:cold-start-interview`。
- **引用标记 `[verify]`** → 连接研究工具（步骤 6）。没有工具的话，每个引用都来自训练数据，而非当前数据库。
- **"我无法读取 [文件]"** → 这通常意味着 plugin 是项目级别的，而文件在项目文件夹之外。请参阅上文"安装为用户级别，而非项目级别"——重新安装为用户级别或将文件移到项目文件夹。
- **plugin 不做 X** → 运行 `/legal-builder-hub:related-skills-surfacer` 查找更匹配的内容，或查看 plugin 的 README 了解"此 plugin 不做什么"。
