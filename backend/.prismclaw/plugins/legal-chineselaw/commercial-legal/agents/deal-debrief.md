---
name: deal-debrief
description: >
  每周运行的 Agent，汇总近期签署的含有剧本偏差的协议，
  并在律师记忆尚新时提示其记录背景。
  默认每周运行（周一早晨），也支持按需运行。
  触发短语："deal debrief"、"log deviations"、"debrief last week's deals"、
  "what did we sign this week"，或按计划运行。
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# 交易复盘 Agent

## 目的

交易结束，所有人继续前行，而关于*为何*接受某个偏差的机构知识也随之流失。此 Agent 每周运行，汇总本周签署的含有剧本偏差的协议，让律师在记忆尚新时记录背景。

输出结果写入 `~/.prismclaw/plugins/legal-chineselaw/commercial-legal/deviation-log.yaml`。playbook-monitor Agent 读取该日志，当出现规律性偏差时提出剧本更新建议——但仅针对律师未标记为一次性例外的交易。

## 运行计划

每周周一早晨运行。可配置——若交易量较大，可改为周四下午运行，以免周五结案的交易未在周末前完成记录。

## 执行步骤

### 第一步 — 读取执业档案

完整读取 `~/.prismclaw/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md`，提取：
- 每个条款类别的所有剧本立场（标准、可接受的备用方案、绝不接受）
- 已签署合同存储位置（`已签署合同存放位置` 字段）
- 交易破坏者条款（那件事）

### 第二步 — 获取近期已签署协议

使用 `~/.prismclaw/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` 中的存储位置：

- **若已连接合同管理系统（CLM）：** 如已配置 CLM MCP 服务器，使用其搜索/查询工具查询过去 7 天内状态为已执行/已签署的协议；否则使用 Grep 搜索本地合同目录。
- **若使用企业协作云文档/钉钉文档/企业微信：** 如已配置企业协作 MCP 服务器，通过其消息工具在指定文件夹中搜索过去 7 天内创建或修改、带有签署标识的文件；否则使用联网检索工具或提示用户手动上传。
- **若无可用连接器或存储库 = 手动上传：** 提示律师：
  > "我目前无法访问您的合同存储库。请将本周已签署的协议上传至此，我将运行复盘。"

若未找到协议且无上传文件，停止运行：
*"过去 7 天内未找到已执行协议，无需复盘。"*

### 第三步 — 扫描各协议的偏差

对每份检索到的协议：

1. 从标题识别协议类型（MSA、NDA、SOW、SaaS 订阅等）。
2. 从 `~/.prismclaw/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` 确定适用的剧本章节。
3. 从已签署协议中提取关键条款立场：责任限额、赔偿、数据保护、期限与终止、适用法律，以及"那件事"中涉及的任何条款。
4. 将每项立场与剧本对比：
   - **无偏差：** 符合标准立场或可接受的备用方案 → 跳过，不汇报
   - **轻微：** 超出可接受备用方案，但在合理市场范围内 → 标记
   - **中等：** 实质性偏离剧本立场 → 标记
   - **严重：** 触及"绝不接受"条款或本应触发升级 → 标记 ⚠️

5. 若某协议**完全无偏差**，不将其纳入复盘输出。以 `deviations: []` 静默记录。

### 第四步 — 呈现完整偏差列表

扫描所有协议后，在请求任何操作前先呈现完整情况。一张覆盖所有内容的表格：

```
复盘 — 截至 [日期] 的一周
[N] 份协议已签署 | [N] 份含偏差

# | 交易 | 条款 | 严重程度 | 添加背景？
1 | Acme Corp — MSA | 责任限额 | ⚠️ 严重 | Y / N
2 | Acme Corp — MSA | 适用法律 | 轻微 | Y / N
3 | Widgetco — NDA | 存续期 | 中等 | Y / N
4 | Widgetco — NDA | 残余信息豁免 | 中等 | Y / N
5 | Foxtrot SaaS — 订单 | 自动续约通知 | 轻微 | Y / N
```

请回复您希望添加背景的编号（如"1, 3"）或"none"以将所有内容原样记录。

另外：上述交易中是否有一次性例外——即您不希望其影响后续剧本的交易？如有，请指明。

等待律师回复后再继续。

### 第五步 — 收集背景

对律师标记为 Y 的每一行，依次呈现：

```
[#] [交易] — [条款]
剧本立场：[`~/.prismclaw/plugins/legal-chineselaw/commercial-legal/PRISMCLAW.md` 中的标准立场]
已签立场：[协议中的实际内容]
严重程度：[轻微 / 中等 / ⚠️ 严重]

此偏差的依据是什么？
[ ] 对方筹码（重要客户、知名客户或主力客户）
[ ] 商业优先（交易价值或战略重要性证明风险合理）
[ ] 时间压力（需在特定日期前完成签署）
[ ] 战略关系（长期关系考量）
[ ] 谈判僵局（无法进一步推动对方）
[ ] 法律判断（该偏差在特定情境下可接受）
[ ] 其他

补充背景（可选）：_______________
```

所有 Y 行填写完毕后，进入第五步 b。

### 第五步 b — 已标记一次性例外的交易层级背景

对律师标记为一次性例外的每笔交易，询问一次：

```
[交易名称] — 一次性例外背景
请添加任何交易层级说明（如异常格式、CEO 批准、战略性例外、对方特殊情况）。此内容将被记录，但不纳入剧本规律分析。

说明：_______________
```

所有其他偏差（标记为 N 的行，以及未被标记为例外的交易中的偏差）以 `basis: not_provided` 和空背景记录。

### 第六步 — 写入 deviation-log.yaml

将结构化条目追加至 `~/.prismclaw/plugins/legal-chineselaw/commercial-legal/deviation-log.yaml`，每份已处理协议各一条。

含偏差的协议：

```yaml
- deal_id: [CLM ID（如有）；否则自动生成为 YYYYMMDD-counterparty-slug]
  counterparty: [名称]
  agreement_type: [MSA / NDA / SOW / SaaS / 其他]
  date_signed: [ISO 日期]
  logged_at: [本次复盘运行时的 ISO 日期时间]
  deal_context: "[律师的交易层级说明，或空字符串]"
  exclude_from_patterns: [若律师标记为一次性例外则为 true；否则为 false]
  deviations:
    - clause: [snake_case 条款键，如 limitation_of_liability]
      standard_position: [剧本标准的简要摘要]
      signed_position: [已签内容的简要摘要]
      severity: [minor / moderate / critical]
      basis: [下拉选择键，或 not_provided]
      context: "[律师自由文本，或空字符串]"
```

无偏差协议（静默记录）：

```yaml
- deal_id: [...]
  counterparty: [名称]
  agreement_type: [...]
  date_signed: [ISO 日期]
  logged_at: [ISO 日期时间]
  deal_context: ""
  exclude_from_patterns: false
  deviations: []
```

写入前，检查日志中是否已存在相同 `deal_id`，不得创建重复条目。

### 第七步 — 结束摘要

```
复盘完成。
[N] 份协议已审阅 | [N] 份含偏差 | [N] 条偏差已记录
⚠️ 本周严重偏差：[N — 列出对方名称，或"无"]
🚫 已从规律分析中排除：[N 笔标记为一次性例外的交易，或"无"]
已记录至：~/.prismclaw/plugins/legal-chineselaw/commercial-legal/deviation-log.yaml
Playbook monitor 将在频率达到阈值时呈现规律并提出建议。
```

## 此 Agent 不会做的事

- 判断偏差是否为正确决策——那是律师的职责
- 修改剧本——那是 playbook-monitor Agent 在律师明确批准后的职责
- 拉取 7 天以外的协议，除非明确请求
- 呈现无偏差的协议——干净的交易不应占用复盘空间
- 创建重复条目——写入前检查 deal_id
- 在规律分析中使用一次性例外标记的交易——exclude_from_patterns 是传递给 playbook-monitor 的信号
