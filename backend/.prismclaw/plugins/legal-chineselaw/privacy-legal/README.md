<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# 隐私法律顾问 Plugin

内部隐私法律顾问工作流：DPA 审查、DSAR 响应起草、PIA 生成，以及法规到政策的差距分析。围绕从你的实际隐私政策、DPA 模板和参考 PIA 学习的团队执业档案构建。

**每个输出都是供律师审查的草稿——带引用、标记和把关——不是法律结论。** Plugin 完成工作：阅读文档、应用你的剧本、查找问题、起草备忘录。律师审查、验证并决定。引用按来源标记，因此你知道哪些来自研究工具，哪些需要检查。特权标记保守应用，因此不会意外放弃。重要行动——提交、发送、执行——在明确确认后进行。

## 目标用户

| 角色 | 主要工作流 |
|---|---|
| **隐私法律顾问** | DPA 审查、PIA 签署、法规差距分析 |
| **隐私项目经理** | DSAR 处理、PIA 接收、供应商隐私审查 |
| **产品法律顾问** | 发布的 PIA 生成 |
| **支持 / CS** | DSAR 一线响应（带升级） |

## 首次运行：冷启动访谈

Plugin 访谈你以了解：你是控制者还是处理者，哪些法规实际适用，你在 DPA 中会同意什么、不会同意什么。然后它阅读三个种子文档——你的隐私政策、你的 DPA 模板、一个你满意的 PIA——并学习你的实际立场和内部风格。

你的配置存储在 `~/.prismclaw/plugins/legal-chineselaw/privacy-legal/PRISMCLAW.md` 并在 Plugin 更新后保留。

```
/privacy-legal:cold-start-interview
```

## 命令

| 命令 | 作用 |
|---|---|
| `/privacy-legal:cold-start-interview` | 冷启动访谈 |
| `/privacy-legal:use-case-triage [activity]` | 这需要 PIA 吗？快速分类 + 条件 |
| `/privacy-legal:dpa-review [file]` | 根据你的剧本审查 DPA（自动检测方向） |
| `/privacy-legal:dsar-response` | 完成 DSAR 并起草响应 |
| `/privacy-legal:pia-generation [feature]` | 按你的内部风格生成 PIA |
| `/privacy-legal:reg-gap-analysis [regulation]` | 将新法规与当前政策/实践进行比较 |
| `/privacy-legal:policy-monitor` | 每周扫描政策漂移，或直接查询提议的新实践 |
| `/privacy-legal:matter-workspace` | 管理事项工作区（仅多客户私人执业）— 新建、列表、切换、关闭、无 |

## Skills

| Skill | 目的 |
|---|---|
| **cold-start-interview** | 从访谈 + 种子文档写入 PRISMCLAW.md |
| **use-case-triage** | 这需要 PIA / DPIA / 可以继续吗？政策冲突检查 + 交接 |
| **dpa-review** | 双向（处理者/控制者）DPA 逐条款审查 |
| **dsar-response** | 身份验证 → 系统检查 → 豁免 → 响应草稿 |
| **pia-generation** | 内部格式的 PIA，带政策一致性检查 |
| **reg-gap-analysis** | 新法规与当前状态，补救计划 |
| **policy-monitor** | 扫描输出以查找实践漂移；起草政策语言更新 |
| **matter-workspace** | 为多客户执业创建、列表、切换和关闭事项工作区；隔离每个客户/事项，因此上下文不会在它们之间泄漏 |

## 快速开始

### 1. 设置

```
/privacy-legal:cold-start-interview
```

准备好：你的公开隐私政策 URL、你的标准 DPA、一个参考 PIA。

### 2. 分类新功能或处理活动

```
/privacy-legal:use-case-triage "营销希望将行为数据用于广告个性化"
```

输出：继续 / 需要 PIA / 强制 DPIA / 停止 — 带条件表、合法基础问题，并提议在同一会话中启动 PIA。

### 3. 审查客户 DPA

```
/privacy-legal:dpa-review customer-dpa.pdf
```

输出：自动检测方向、逐条款与剧本比较、提议的红线、政策一致性检查。

### 4. 处理 DSAR

```
/privacy-legal:dsar-response
```

引导你完成：分类 → 验证 → 定位 → 豁免 → 草稿。使用配置的 PRISMCLAW.md 中的系统列表。

### 5. 为新功能制作 PIA

```
/privacy-legal:pia-generation "位置共享功能"
```

接收问题 → 按你的内部格式的 PIA → 政策差异 → 条件列表。

## 如何学习

你在 `~/.prismclaw/plugins/legal-chineselaw/privacy-legal/PRISMCLAW.md 中的执业档案不是静态的——它随着你使用 Plugin 而改进。Skills 告诉你输出何时使用了你应该调整的默认值。`policy-monitor` skill 监视你的政策与你的实践之间的漂移，并提议更新。你可以重新运行设置、直接编辑文件，或告诉 skill 记录新立场。

## 文件结构

```
privacy-legal/
├── .plugin/plugin.json
├── .mcp.json
├── PRISMCLAW.md
├── README.md
├── skills/
│   ├── cold-start-interview/
│   ├── use-case-triage/
│   ├── dpa-review/
│   ├── dsar-response/
│   ├── pia-generation/
│   ├── reg-gap-analysis/
│   ├── policy-monitor/
│   └── matter-workspace/
└── hooks/hooks.json
```

## 注意事项

- DPA 审查是双向的：相同的 skill 处理客户 DPA（保护操作灵活性）和供应商 DPA（保护数据）。方向自动检测，或询问。
- PIA 格式来自你的种子 PIA。如果在设置期间未提供，则使用通用结构——重新运行设置并提供参考 PIA 以修复。
- 差距分析（`reg-gap-analysis`）处理传入法规。政策监视器处理内部实践漂移。不同方向的变更使用不同工具。
- 政策监视器需要配置输出文件夹（在设置期间设置）才能运行扫描。直接查询模式无需它即可工作。
