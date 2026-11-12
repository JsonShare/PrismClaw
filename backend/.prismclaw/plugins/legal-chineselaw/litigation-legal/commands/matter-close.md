---
name: matter-close
description: 关闭事项——捕获结果、最终敞口和经验教训，然后从活跃投资组合中归档而不删除记录。当用户想要关闭事项、说"[事项] 已完成"或需要记录和解、驳回、判决、撤回或合并结果时使用。
argument_hint: '[slug]'
category: 争议诉讼
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /matter-close

1. 遵循以下工作流和参考。
2. 确认 slug 和当前状态。
3. 捕获结果：解决类型（和解、驳回、对我方有利的判决、对我方不利的判决、撤回、合并）、日期、最终敞口/成本、经验教训。
4. 更新 `_log.yaml`：`status: closed`，添加 `closed: YYYY-MM-DD` 和 `outcome:` 字段。
5. 向 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/[slug]/history.md` 附加最终条目。
6. 事项保留在 `_log.yaml` 和 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/[slug]/` 中——不删除。`/portfolio-status` 将其从活跃汇总中过滤。

---

# 事项关闭

## 目的

事项有终点。结果是投资组合产生的最有价值的数据点——它为未来事项校准风险框架。关闭事项以结构化方式捕获结果，使记录有用，而不仅仅是归档。

## 加载上下文

- `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/_log.yaml` — 查找行
- `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/[slug]/matter.md` — 参考（intake 上下文）
- `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/[slug]/history.md` — 附加目标

**冲突门槛——不可绕过。** 在关闭之前，检查 `_log.yaml` 中的事项 slug。如果事项不在 `_log.yaml` 中，拒绝并路由：

> "我在事项日志中看不到 [matter slug]。没有可关闭的内容——slug 可能错误，或者事项从未通过 `/litigation-legal:matter-intake` 接收。先检查 slug；如果确实从未接收，就没有要更新的行也没有要关闭的文件结构。"

## 输入

Slug（必需）。

## 关闭

### 1. 解决类型

- `settled`（已和解）— 与对方达成，金额、结构性条款
- `dismissed`（已驳回）— 有无偏见、通过何种机制
- `judgment-for-us`（对我方有利的判决）— 在何阶段、上诉敞口
- `judgment-against-us`（对我方不利的判决）— 在何阶段、上诉状态、敞口确定
- `withdrawn`（已撤回）— 由对方撤回、情况
- `consolidated`（已合并）— 合并到另一事项（提供父事项的 slug）
- `other`（其他）— 附说明

### 2. 解决日期

事项实际结束的日期（和解协议签署、命令发出、驳回提交）。

### 3. 最终敞口

- 公司实际成本（和解金额 + 费用 + 禁令/结构性成本）
- vs. intake 时的初始敞口范围（我们当时判断对了吗？）
- 准备金准确性（如已计提）：入账 vs. 实际

### 4. 经验教训

两三句话。我们做对了什么？我们误判了什么？intake 应该更早标记什么？

这是未来律师会重读的部分。诚实。"误判了可能性——原告律所比预期更激进"比"结果有利"更有价值。

### 5. 种子文档提示

和解协议、最终命令、驳回——如有路径。非必需。

## 写入

**在关闭事项之前（重大行为——事项被归档且活跃跟踪终止）：** 阅读 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` 中的 `## 谁在使用这个`。如果角色是非律师：

> 关闭事项有法律后果——它终止活跃跟踪，可能影响任何关联的法律保留（如适用，单独运行 `/legal-hold --release`），并建立公司依赖的最终记录。你是否与律师审查了此内容？如果是，继续。如果否，这是带给他们的简报：
>
> [生成一页摘要：事项、解决类型和条款、最终敞口 vs. 初始、准备金准确性、仍活跃的相关事项或上诉、过早关闭可能出什么问题、要问律师什么。]
>
> 如果你需要在你所在的司法管辖区找到持牌律师、事务律师、大律师或其他授权法律专业人士：你所在专业监管机构的转介服务是最快的起点（美国的州律师协会、英格兰和威尔士的 SRA/律师标准委员会、苏格兰/北爱尔兰/爱尔兰/加拿大/澳大利亚的律师协会，或你所在司法管辖区的同等机构）。

没有明确的是，不要写入关闭字段或附加关闭条目。

### 更新 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/_log.yaml`

```yaml
status: closed
closed: [YYYY-MM-DD]
outcome: [resolution-type]
final_cost: [dollar amount]
last_updated: [today]   # 关闭是最后一次触碰；记录它
```

保留所有现有字段。不要删除行。

### 附加最终条目到 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/[slug]/history.md`

```markdown
## [YYYY-MM-DD] — 事项已关闭：[resolution-type]

**结果：** [叙述——发生了什么，按什么条款]
**最终成本：** [金额 + 结构性条款（如有）]
**vs. 初始敞口：** [与 matter.md intake 范围比较]
**准备金准确性：** [如适用]

**经验教训：**
[2-3 句话——诚实的回顾]

**相关文档：** [和解协议 / 最终命令 / 等，如提供]
```

### 触及 `~/.prismclaw/assets/plugins/legal-chineselaw/litigation-legal/matters/[slug]/matter.md`

在末尾添加关闭块（不要修改前面的部分——它们是历史 intake）：

```markdown
---

## 已关闭 [YYYY-MM-DD]

[一段话的结果摘要。指向最终历史条目以获取详情。]
```

## 确认

在写入前向用户展示完整的关闭条目和 yaml 变更。

## 此 skill 不做什么

- 删除事项。关闭的事项保留在 `_log.yaml` 和磁盘上——它们是投资组合判断的训练集。
- 重新开启。如果关闭的事项回来了（上诉、相关诉讼），开启一个新事项，在 `matter.md` 中引用已关闭的事项。
- 总结用户没有说的经验教训。如果用户跳过经验教训部分，留空而不是发明。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
