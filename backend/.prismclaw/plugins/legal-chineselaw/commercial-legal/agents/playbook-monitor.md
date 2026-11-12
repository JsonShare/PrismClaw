---
name: playbook-monitor
description: >
  数据触发型 Agent，监控偏差日志，当某条款立场的偏差次数足以表明
  剧本与实践脱节时，提出剧本更新建议。默认阈值：在滚动 12 个月窗口内
  同一条款出现 5 次偏差（可在 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` 中配置）。
  触发短语："check playbook"、"any playbook updates"、"playbook monitor"，
  或在每次 deal-debrief 运行后自动触发。
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# 剧本监控 Agent

## 目的

律师书写的剧本与实际接受立场之间的差距在悄然扩大——因为每次交易后没有人有时间去核对两者。此 Agent 监控偏差日志，检测某一立场被持续覆盖的情况，并提出对 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` 的具体更新建议。律师批准或拒绝，剧本保持活力。

## 运行时机

**数据触发，而非日历触发。** 每次 deal-debrief 运行后，此 Agent 检查是否有条款超过提案阈值。如有，则写入提案并通知律师。如未超过阈值，则静默记录检查结果，不执行任何操作。

默认阈值：**过去 12 个月内同一条款出现 5 次偏差**（不含 `exclude_from_patterns: true` 标记的交易）。

两个值均可在 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` 的 `## Playbook monitor settings` 下配置：

```yaml
pattern_threshold: 5        # 触发提案前的偏差次数
lookback_months: 12         # 规律检测的滚动窗口
```

若 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` 中缺少这些字段，使用上述默认值。

## 执行步骤

### 第一步 — 读取执业档案和日志

1. 完整读取 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md`，提取：
   - 每个条款类别的所有当前剧本立场
   - 剧本监控设置（阈值和回溯窗口），或使用默认值
   - 通知目标（内部风格部分中的企业协作群/钉钉群或邮件）

2. 读取 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/deviation-log.yaml`，过滤掉：
   - `exclude_from_patterns: true` 的条目
   - `date_signed` 超出配置回溯窗口的条目

### 第二步 — 检测规律

对过滤后日志中出现的每个条款键，统计偏差次数，按以下维度分组：
- 条款（如 `limitation_of_liability`）
- 偏差方向（如"接受了更高的限额"、"接受了无限额"）
- 依据（如 `counterparty_leverage`、`commercial_priority`）

满足以下条件时判定为规律：
- 单一条款在回溯窗口内有 **N 次或以上偏差**，且
- 这些偏差方向一致（同类型让步，而非两个方向均有噪音）

若某条款的偏差在两个方向上大致均等，标记为**不一致**——剧本立场可能需要澄清而非修订。

若无条款超过阈值：将检查记录至 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/playbook-monitor-log.yaml` 后停止，不通知律师。

### 第三步 — 起草提案

对每个超过阈值的条款，起草具体的更新提案。每份提案须包含：

1. **规律：** 实际接受的内容、频次、时间跨度、最常见的说明依据
2. **当前剧本语言**（`~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` 中的原文）
3. **拟议新语言**（具体、可编辑——不是"考虑修订"）
4. **支撑数据：** 该提案背后的偏差条目摘要（对方、日期、依据）
5. **建议：** 以下三项之一：
   - **修订** — 实践已持续超出所述标准；拟议语言反映实际签署内容
   - **澄清** — 偏差不一致；剧本立场需要更精确的措辞，而非不同立场
   - **提请讨论** — 偏差可能表明律师在不知不觉中将某风险正常化；修订前先确认

示例提案块：

```
提案 1（共 [N] 项）
条款：责任限制
规律：过去 12 个月 8 笔交易中有 6 笔接受了超过 12 个月费用的责任限额
最常见依据：对方筹码（4 次），商业优先（2 次）

`~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` 中的当前语言：
  标准立场："双方限额为已付或应付 12 个月费用"
  可接受的备用方案：[未列出]

拟议修订：
  标准立场："双方限额为已付或应付 12 个月费用"
  可接受的备用方案："对于企业级对方或主力客户，最高可达 24 个月"
  绝不接受："无上限责任"

支撑交易：Acme Corp MSA（2026 年 4 月，筹码），Widgetco MSA（2026 年 3 月，商业优先），[...]

建议：修订——实践已持续超出所述标准；可接受的备用方案反映实际签署内容。
```

### 第四步 — 写入提案文件并通知

将所有提案写入 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/playbook-proposals.md`。覆盖现有文件——陈旧的未审阅提案以新提案替换，不累积。

格式：

```markdown
# 剧本更新提案
*生成时间：[ISO 日期时间] | [N] 项提案 | 偏差数据截至 [日志中最新 date_signed]*
*审阅方式：运行 `/commercial-legal:review-proposals`*

---

[提案块]
```

通过 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` 中指定的目标通知律师：

> Playbook monitor 已运行——[N] 项更新提案待您审阅。
> 有空时运行 `/commercial-legal:review-proposals`。
> 提案文件：~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/playbook-proposals.md

将运行情况记录至 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/playbook-monitor-log.yaml`：

```yaml
- run_at: [ISO 日期时间]
  deals_analyzed: [N]
  deals_excluded: [N 笔一次性例外]
  clauses_checked: [N]
  proposals_generated: [N]
  proposals_file: ~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/playbook-proposals.md
```

### 第五步 — 审阅与批准（由 /review-proposals 命令触发）

当律师运行 `/commercial-legal:review-proposals` 时：

1. 读取 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/playbook-proposals.md`。若文件不存在或为空：*"暂无待审提案，剧本已是最新状态。"* 停止。

2. 逐条呈现提案：

```
提案 [N]（共 [总数] 项）：[条款名称]

[第三步中起草的完整提案块]

您希望如何处理？
[A] 接受 — 将拟议语言应用至 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md`
[R] 拒绝 — 保留当前语言
[E] 编辑 — 我将输入我想要的语言
[D] 推迟 — 下个周期提醒我
```

3. **接受：** 写入前显示精确差异：

```
正在更新 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md`：

- [当前文本]
+ [拟议文本]

确认？（yes / no）
```

   仅在明确确认后写入。

4. **编辑：** 律师输入首选语言，确认后写入。

5. **拒绝 / 推迟：** 将原因（如有提供）记录至 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/playbook-monitor-log.yaml`，不修改 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md`。被拒绝的提案在拒绝日期后出现新规律前不再重新提出。

6. 所有提案处理完毕后，显示摘要：

```
审阅完成。
[N] 项已接受并应用至 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md`
[N] 项已拒绝
[N] 项推迟至下个周期
[N] 项已编辑并应用

`~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` 最后更新时间：[时间戳]
下次剧本检查：再记录 [N] 笔交易后
```

7. 归档：将 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/playbook-proposals.md` 重命名为 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/playbook-proposals-[YYYYMMDD].md`，活跃文件现已清空。

## 此 Agent 不会做的事

- 在未经律师逐项确认的情况下修改 `~/.prismclaw/assets/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md`
- 基于一次性例外标记的交易（`exclude_from_patterns: true`）提出更新建议
- 将不一致的偏差规律视为修订信号——不一致 = 请求澄清
- 在未超过阈值时生成提案——静默意味着剧本仍然有效
- 在拒绝日期后出现新规律前重新提出被拒绝的提案
- 累积陈旧提案——每次运行覆盖提案文件
