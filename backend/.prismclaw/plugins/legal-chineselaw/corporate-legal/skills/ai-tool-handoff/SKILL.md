---
name: ai-tool-handoff
name_zh: AI 工具交接
description: >
  检测元典、北大法宝或类似批量合同审查工具何时在使用中，将大批量条款提取交接给它，
  并根据 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` 中的信任级别
  对其输出进行 QA。当用户说"批量审查"、"AI 提取"、"合同批量分析"，
  或当 diligence-issue-extraction 遇到大批量类别时使用。
category: 公司法务
invocation_policy: user_only
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# AI 工具交接

## 事项上下文

**事项上下文。** 检查执业级 PRISMCLAW.md 中的 `## Matter workspaces`。如果 `Enabled` 是 `✗`（内部用户的默认值），跳过本段其余部分——skills 使用执业级上下文，事项机制不可见。如果启用且没有活跃事项，询问："这是哪个事项的？Run `/corporate-legal:matter-workspace switch <slug>` or say `practice-level`。"加载活跃事项的 `matter.md` 以获取事项特定上下文和覆盖。将输出写入事项文件夹 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/matters/<matter-slug>/`。除非 `Cross-matter context` 是 `on`，否则永远不要阅读另一个事项的文件。

---

## 目的

批量合同审查工具（元典/其他国内AI合同工具）擅长一件事：阅读 500 份合同并找到每一条控制权变更条款。它们不太擅长判断——决定特定控制权变更条款是否确实被此交易结构触发。

此 skill 将批量提取交接给正确的工具，然后对返回结果运行 QA 层。

**交接之前：** 先尝试 `tabular-review`（`/corporate-legal:tabular-review`）。对于用户环境可以处理的任何内容——几百份文档、定义的列模式——原生表格审查设置更快、无按文档成本、保持工作产品本地。只有在语料库确实过大、团队已有配置好的批量审查工具、或事项需要具有已验证溯源链的工具时，才交接给外部AI工具。

## 加载上下文

`~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` → AI 辅助审查：
- 使用中的工具（元典 / 其他国内AI合同审查工具 / 无）
- 用于什么（哪些条款类型）
- 信任级别（按原样使用 / 抽查 / 完整重新审查）
- 交接流程（谁加载、谁 QA）

如果 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` 说没有 AI 工具 → 此 skill 为空操作。所有内容直接通过 diligence-issue-extraction。

## 何时交接

当以下全部满足时交接：
- 类别有 >50 份文档（低于此，直接阅读更快）
- 提取目标是工具擅长的条款类型（CoC、转让、排他性、MFN、终止、自动续约）
- 文档相当统一（都是类似纸质的客户合同——不是合同、信函和董事会纪要的混合）

不交接：
- 定制或重度谈判的文档
- 副函和修正案（依赖上下文，工具会遗漏与主协议的交互）
- 问题是"这对交易意味着什么"而非"此条款是否存在"的任何内容

## 交接

### 步骤 1：准备批次

- 识别批次文档（来自 VDR 清单）
- 根据 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` 指定提取目标（哪些条款类型）
- 注明重要性阈值，以便可以过滤工具输出

### 步骤 2：加载（或指示加载者）

根据 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md`——谁加载。如果是你，生成加载指令。如果是其他人，生成请求：

```markdown
## [工具] 加载请求 — [交易代码] — [类别]

**文档：** VDR 文件夹 [路径] 中的 [N] 份文档
**加载到：** [工具工作区/事项]
**提取目标：**
- 控制权变更 / 转让
- 排他性
- [根据 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` 的其他项]

**过滤输出：** 仅标记提取目标存在的内容——不需要为每份文档输出"未找到 CoC 条款"。

**在 [日期] 前返回**
```

### 步骤 3：QA 输出

当工具返回结果时，应用信任级别：

**"按原样使用"：** 直接摄入到尽调发现中。（仅在 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` 明确说明时——这很少见。）

**"抽查 X%"：** 随机抽样 X% 的标记文档。对每份，阅读实际条款并与工具提取结果比较。如果错误率低，接受批次。如果发现错误，扩大样本。

**"完全人工审查标记项"：** 工具缩小范围（500 份文档 → 80 份有 CoC 条款）。人工阅读全部 80 份。工具节省了阅读 420 份干净文档的时间。

### 步骤 4：判断层

工具找到了条款。现在应用判断：

对每条标记的 CoC 条款：它是否真的被此交易触发？
- 股份出售 vs. 资产出售 vs. 合并——不同触发条件
- 合同中"控制权变更"如何定义——多数所有权？董事会控制？其他？
- 是否有针对此类交易的豁免？

这是工具做不到的部分。输出以内部格式进入尽调发现。

## 输出

> 以下 QA 摘要源自 VDR 文档，可能具有特权、保密性或两者兼有。它继承来源的特权和保密状态——超出特权圈的分发可能放弃特权。与事项的特权文件一起存储。

```markdown
## AI 工具交接摘要 — [类别]

**工具：** [元典 / 其他国内AI合同审查工具]
**已处理文档：** [N]
**提取目标：** [条款类型]

### QA

**信任级别：** [根据 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md`]
**样本量：** [N] 份文档已抽查
**错误率：** [X]% — [已接受 / 已扩大样本 / 已触发完全重新审查]

### 结果

| 条款类型 | 标记文档 | 判断层后 | 重要 |
|---|---|---|---|
| 控制权变更 | [N] | [N 实际被交易结构触发] | [N 超过阈值] |
| 转让 | [N] | [N] | [N] |

**→ [N] 项发现已添加到尽调问题**
**→ [N] 项同意已添加到关闭检查清单**
```

## 以下一步决策树结束

根据 PRISMCLAW.md `## Outputs` 以下一步决策树结束。根据此 skill 刚刚生成的内容自定义选项——五个默认分支（起草 X、升级、获取更多事实、观察等待、其他）是起点，而非锁定。树就是输出；律师选择。

## 此 skill 不做什么

- 它不直接运行外部AI审查工具——它管理交接指令和 QA。人工（或工具自己的界面）运行提取。
- 它不会完全用自身判断替代工具输出——如果 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` 说抽查 10%，就检查 10%，不是 100%。
- 它不决定信任级别——那在 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` 中，在冷启动时根据团队对工具的经验设置。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
