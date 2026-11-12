<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->

# AI Governance Plugin

内部 AI 治理法律工作流：使用案例分类、AI 影响评估、供应商 AI 审查，以及法规与政策差距分析。围绕从你的 AI 政策、参考影响评估和你的主要供应商 AI 协议中学到的团队执业档案构建。

**每个输出都是供律师审查的草稿——带引用、标记和把关——而不是法律结论。** plugin 完成工作：阅读文档、应用你的剧本、发现问题、起草备忘录。律师审查、验证并决定。引用按来源标记，以便你知道哪些来自研究工具，哪些需要检查。特权标记保守应用，因此不会意外放弃任何东西。重要行动——提交、发送、执行——在明确确认后进行。

## Who this is for

| 角色 | 主要工作流 |
|---|---|
| **隐私律师 / AI 治理律师** | 影响评估、供应商 AI 审查、法规差距分析 |
| **产品律师** | 使用案例分类、AI 组件的发布审查 |
| **GC / 法律 ops** | AI 政策治理、升级、董事会级别问题 |
| **采购 / 法律** | 供应商 AI 合同审查 |

## First run: the cold-start interview

plugin 对你进行访谈以了解：你是构建者、部署者，还是两者都是——哪些法规真正适用——你的使用案例红线是什么——以及这里好的影响评估是什么样子的。然后它阅读你的种子文档并了解你的真实立场和内部风格。

```
/ai-governance-legal:cold-start-interview
```

## Commands

| 命令 | 功能 |
|---|---|
| `/ai-governance-legal:cold-start-interview` | 冷启动访谈——编写你的执业档案 |
| `/ai-governance-legal:ai-inventory [list \| add \| edit \| classify \| show]` | 管理欧盟 AI 法案的系统清单——跟踪每个系统的角色和风险等级 |
| `/ai-governance-legal:use-case-triage [use case]` | 根据你的注册表对使用案例进行分类（批准 / 有条件 / 从不） |
| `/ai-governance-legal:aia-generation [use case]` | 以你的内部风格运行 AI 影响评估（AIA） |
| `/ai-governance-legal:vendor-ai-review [vendor/file]` | 根据你的立场审查供应商 AI 协议 |
| `/ai-governance-legal:reg-gap-analysis [regulation]` | 将新法规或指南与当前政策/实践进行比较 |
| `/ai-governance-legal:policy-monitor` | 每周扫描 AI 政策漂移，或直接查询提议的新实践 |
| `/ai-governance-legal:policy-starter` | 从已发布的模型政策起草坚定的 AI 使用政策，适应你的执业档案（供律师审查的草稿） |
| `/ai-governance-legal:matter-workspace` | 管理事项工作区（仅多客户私人执业）——新建、列表、切换、关闭、无 |

## Skills

| Skill | 目的 |
|---|---|
| **cold-start-interview** | 从访谈 + 种子文档编写 `~/.prismclaw/assets/plugins/legal-chineselaw/ai-governance-legal/PRISMCLAW.md` |
| **ai-inventory** | 欧盟 AI 法案的系统清单——每个系统的角色（供应商、部署者、进口商、分销商、授权代表、产品制造商）和风险等级 |
| **use-case-triage** | 根据注册表对使用案例进行分类；标记缺失的评估 |
| **aia-generation** | 内部格式的 AI 影响评估（AIA） |
| **vendor-ai-review** | 根据治理立场进行 AI 特定的供应商合同审查 |
| **reg-gap-analysis** | 新法规/指南与当前状态、补救计划 |
| **policy-monitor** | 扫描输出以查找实践漂移；起草 AI 政策语言更新 |
| **policy-starter** | 从已发布的模型政策（ABA、州律师协会、ILTA、CLOC、NIST、欧盟 AI 法案、同行政策）生成第一稿 AI 使用政策，适应你的执业档案——供律师审查的草稿，不是最终政策 |
| **matter-workspace** | 为多客户执业创建、列表、切换和关闭事项工作区；隔离每个客户/事项，因此上下文不会在它们之间泄露 |

## Quick start

### 1. Setup

```
/ai-governance-legal:cold-start-interview
```

准备好（如果存在）：你的 AI 或可接受使用政策、一份先前的影响评估、主要供应商 AI 协议、模型清单或已批准工具列表。

你的配置存储在 `~/.prismclaw/assets/plugins/legal-chineselaw/ai-governance-legal/PRISMCLAW.md` 并在 plugin 更新后保留。

### 2. 分类新使用案例

```
/ai-governance-legal:use-case-triage "销售团队希望使用 AI 自动对潜在客户进行评分"
```

输出：风险等级、注册表匹配或差距、所需条件、是否需要影响评估。

### 3. 运行影响评估

```
/ai-governance-legal:aia-generation "用于 HR 的 AI 简历筛选"
```

收集问题 → 你内部格式的影响评估 → 政策一致性检查 → 缓解条件。

### 4. 审查供应商 AI 协议

```
/ai-governance-legal:vendor-ai-review openai-terms.pdf
```

输出：逐条与你的立场比较、提议的红线、需要升级的差距。

## Plugin triangle: AI governance ↔ product counsel ↔ privacy

这三个 plugin 设计为协同工作。AI 治理是第三条腿。

- **产品律师** 检测发布何时具有 AI 组件 → 移交至 `/ai-governance-legal:use-case-triage` 和 `/ai-governance-legal:aia-generation`
- **隐私** 检测 AI 使用案例何时涉及个人数据 → 如果 plugin 已安装，移交至 `/privacy-legal:pia-generation`
- **AI 治理** 检测影响评估何时引发数据保护问题 → 如果 plugin 已安装，移交至 `/privacy-legal:pia-generation`

交接是明确的：每个 plugin 标记何时需要另一个 plugin，并说明在那里要回答什么问题。

## File structure

```
ai-governance-legal/
├── PRISMCLAW.md
├── README.md
└── skills/
    ├── cold-start-interview/
    ├── use-case-triage/
    ├── aia-generation/
    ├── vendor-ai-review/
    ├── reg-gap-analysis/
    ├── policy-monitor/
    ├── policy-starter/
    └── matter-workspace/
```

## How it learns

你在 `~/.prismclaw/assets/plugins/legal-chineselaw/ai-governance-legal/PRISMCLAW.md` 的执业档案不是静态的——它会随着你使用 plugin 而改进。Skill 会告诉你何时输出使用了你应该调整的默认值。`policy-monitor` agent 监视你的 AI 治理政策与你的实践之间的漂移，并提议更新。你可以重新运行设置、直接编辑文件，或告诉 skill 记录新立场。

## Notes

- 差距检查（`reg-gap-analysis`）处理传入的法规。政策监视器处理内部实践漂移。不同的工具应对不同的变化方向。
- 政策监视器需要配置输出文件夹（在设置期间设置）才能使扫描工作。直接查询模式无需它即可工作。
- 使用案例分类仅与注册表一样好。花时间在设置访谈中正确获取红线——它们驱动一切。
- 影响评估格式来自你的种子评估。如果你在设置期间没有提供一个，它会使用基线结构——使用参考重新运行设置以改进它。
- 构建者和部署者义务被分别对待。如果你两者都是，skill 会询问你为每个任务戴着哪顶帽子。
- 差距分析是手动的（你将其指向法规或指南文档）。对于自动监视，如果已安装 plugin，可与 `regulatory-legal` plugin 配对。
- 按照惯例，`## Company profile` 部分是 `~/.prismclaw/assets/plugins/legal-chineselaw/ai-governance-legal/PRISMCLAW.md` 的第一个块。如果你运行其他 `-counsel` plugin，你可以跨复制它，而不是重新输入相同的上下文。
