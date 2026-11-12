---
name: written-consent
description: '以内部格式起草股东会/董事会/监事会的书面决议（书面决定）， 并从决议存储库中搜索先例。处理多决议、回避表决标记、 公司法合规要求以及签署人跟踪，并针对重大一次性行动内置范围警告。
  当用户说"书面决议"、"书面决定"、"不开会的董事会决议"、"股东书面同意" 或描述需要股东会/董事会批准且不开会的行动时使用。

  '
argument_hint: '[描述需要批准的行动]'
category: 公司法务
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /written-consent

1. 加载 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` → 董事会与秘书（同意存储库、决议语言、注册州、董事会组成）。
2. 使用以下工作流。
3. 识别行动并分类（例行 / 审查标记）。
4. 如果是审查标记：显示外部律师警告并在继续之前确认。
5. 从同意存储库中搜索最接近的先例。如果没有存储库：使用 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` 中的种子同意。
6. 使用先例作为基础以内部格式起草同意。
7. 输出：同意草案 + 签署人检查清单 + 审查提示。

---

## 事项上下文

**事项上下文。** 检查执业级 PRISMCLAW.md 中的 `## Matter workspaces`。如果 `Enabled` 为 `✗`（内部用户的默认值），跳过本段的其余部分——skills 使用执业级上下文，事项机制不可见。如果已启用且没有活跃事项，询问："这是哪个事项的？Run `/corporate-legal:matter-workspace switch <slug>` 或说 `practice-level`。"加载活跃事项的 `matter.md` 以获取事项特定上下文和覆盖。将输出写入事项文件夹 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/matters/<matter-slug>/`。除非 `Cross-matter context` 为 `on`，否则永远不要阅读另一个事项的文件。

---

## 目的

大多数例行公司批准不需要召开会议。高管任命、股权/期权授予、银行账户授权、合同授权、公司间安排——这些可以通过书面决议方式处理。

**中国法律依据：** 依据《公司法》第148条（2024年7月1日施行），有限责任公司股东、有限责任公司董事会在全体成员同意的情况下，可以不召开会议，直接签署书面形式的决定。`[模型知识—待核验]`

此 skill 以内部格式快速起草书面决议，找到最近的先例决议，并标记应在任何人签署前获得律师意见的行动。

## 范围警告——起草前阅读

> **此 skill 专为在你的存储库或种子文档中有直接先例的日常同意而设计。** 例行行动——官员任命、股权授予、年度授权、标准合同批准——是正确的用例。skill 找到紧密匹配的先例同意，将其调整为当前行动，并生成干净的草案。
>
> **对于主要一次性行动，无论此 skill 生成什么，外部律师审查都是审慎的。** 这包括：M&A 交易（资产购买、股份购买、合并、投资）、融资轮、向新投资者发行股权、变更控制权规定、解散或清算、重大房地产交易，以及任何将在未来尽职调查流程中接受审查的行动。
>
> 当行动看起来像主要一次性行动时，skill 会自动标记。该标记不是阻碍——你可以继续。它是提示思考针对此特定行动，先例适应的草案是否足够。

---

## 重大行动 + 紧急 = 停止

用户想要**今日**签署的重大一次性行动（并购/融资/解散/资本结构变更/与并购或融资相关的董事选举）的书面决议——"今天下午发出去签"、"一小时后开签约会"、"今晚签署"——须经过律师审查。不是因为 plugin 无法起草——因为重大行动上的错误决议是单向门，紧迫性压力恰恰是错误发生的时候。

触发条件（两者必须为真）：

1. 行为属于下面的**审查标记——重大一次性**类别（并购、融资轮、解散、资本结构变更、控制权安排变更、与并购或融资相关的董事选举、重大资产交易、任何将在未来融资或并购数据室中出现的行动）。
2. 用户的请求包含不可逆性信号——"今天发出去签"、"今晚签"、"开盘前/交割前需要"、"马上要盖章"等。

当两者都为真时，输出此内容并停止：

> ⛔ **重大行动 + 当日签署——我不会将其标记为准备签署。**
>
> 这是 [行动类型]，这是一个单向门。你要求今天签署。该组合恰恰是决议错误最难撤销的时候。
>
> 我会很高兴起草它——但我不会在没有律师审查的情况下将其标记为准备签署。如果外部律师已经参与此交易，将此草案交给他们。如果没有，请联系律师协会寻找律师推荐。
>
> 两种前进方式：
>
> 1. **我起草，律师审查，然后签署**——这是重大公司行动的正常路径。告诉我起草，我会。
> 2. **律师已经参与此交易并清理了草案路径**——告诉我谁审查以及何时。我会继续并注明律师已审核。
>
> 在没有这两种情况之一的情况下，我不会在当日压力下以"准备签署"形式起草。这不是延迟——这是如果有人审查文件，当日重大行动决议可以辩护的唯一方式。

在没有明确响应选择路径 1 或路径 2 的情况下，不要越过此关卡进行步骤 1 或任何起草。没有主要行动触发的例行同意，或没有当日签署请求的主要行动同意，遵循下面的正常流程——主要一次性类别的"建议外部律师审查"标记仍然适用，但不会硬停止。

---

## 加载上下文

- `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` → `## Board & Secretary`：
  - 同意存储库位置
  - 内部决议语言
  - 注册州（用于通知要求）
  - 董事会组成（用于签署人名单）
  - 书面同意——范围和任何限制

### 无先例硬停止

如果 (a) 在 `## Board & Secretary` → 同意存储库中未配置同意存储库，并且 (b) 未向此 skill 提供种子同意文档（在此会话中上载或在 `## Board & Secretary` → 同意格式部分引用，并从特定种子中提取的决议/叙述/授权语言），**在起草前停止**。不要进入步骤 1 intake，不要从通用模板起草，不要不要用填充格式"开始"。

完全输出此块并等待响应：

> **无先例可用——起草前停止。**
>
> 我没有先例可以匹配。没有你的内部格式起草的董事会同意需要更多的修正而不是节省——决议语言、叙述深度、授权样板和签名块惯例都带有特定于内部的选择，如果我从通用模板开始，审阅者将从头开始重写。
>
> 两种解除阻塞方式：
>
> 1. **粘贴或上传先前的同意**（此公司在任何类别中的任何最近的 UWC——我提取格式，而不是实质），或
> 2. **告诉我"无论如何从通用模板起草——我自己调整形式"**——仅在你知道你会手动重新编写决议语言、叙述风格和授权块后再选择这个。明确说明；我不会推断它。
>
> 你想做什么？

在没有明确响应选择这两种路径之一的情况下不要继续。没有先例的起草尝试是此 skill 可以产生的最高返工价值输出——硬停止是故意的。

---

## 步骤 1：识别行动

询问用户董事会需要批准什么行动。收集：

- **正在批准什么？**（一句话。）
- **任何支持细节？** 例如：被任命官员的姓名、股权授予的授予金额和价格、合同批准的对手方和合同价值。
- **生效日期：** 今天，还是特定日期？
- **签署人：** 全体董事会，还是特定委员会？如果 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` 书面同意范围说某些行动需要会议而不是同意，现在标记它。
- **任何董事冲突？** 任何董事对正在批准的行动有实质性利益吗？如果是：标记它。冲突董事根据州法律和冲突性质可能仍能签署，但同意应披露它，用户应确认。

### 行为分类

在搜索先例之前分类行动：

**例行——可能有直接先例：**
- 官员任命或罢免
- 向现有计划参与者授予股权（期权、RSU、限制股）
- 银行账户授权或签署人更新
- 低于重要性阈值的合同批准
- 年度授权决议（税务事项、福利计划等）
- 按公允条款的公司间贷款或服务协议
- 注册代理人或注册办公室变更

**审查标记——主要一次性，建议外部律师审查：**
- M&A 交易（收购、合并、资产购买、投资）
- 新融资轮或债务融资
- 向新投资者发行股权
- 变更控制权规定或触发条件
- 批准公司章程或股东协议本身要求董事会批准的协议
- 解散、清算或破产申请
- 重大房地产交易
- 任何将在未来融资或 M&A 数据室中作为董事会批准附件出现的行动

如果行动属于审查标记类别，在起草前显示此内容：

> ⚠️ **建议外部律师审查。** 这看起来像 [action type]，这是一项主要公司行动，先例适应草案可能不够。考虑在分发前让外部律师审查。你想让我继续起草吗？

---

## 步骤 2：搜索先例

### 如果连接了同意存储库

为最接近的先前同意搜索存储库。搜索策略：

1. 按行动类型关键词搜索（例如，"officer appointment"、"equity grant"、"bank authorization"）
2. 返回最近匹配的同意，或如果存在多个接近匹配则要求用户选择：

> 我发现 [N] 个看起来像这样的先前同意：
>
> 1. [同意标题 / 描述] — [日期]
> 2. [同意标题 / 描述] — [日期]
>
> 哪一个最接近你的需要？还是我应该使用最近的？

3. 阅读选定的同意。提取：决议语言、叙述结构、授权语言、任何特定条件或例外。
4. 注意先前行动与当前行动之间需要在草案中更新的任何差异。

### 如果没有存储库（仅种子文档）

从 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` 中的种子同意中提取格式。注意无法进行先例搜索——草案将遵循内部格式但没有实质性先例匹配。向用户标记此内容：

> 未连接同意存储库，所以我从你的种子文档中处理格式。对于此特定行动类型，你可能想检查是否有先例同意作为实质性起点。

---

## 步骤 3：起草同意

使用内部格式。下面的结构是标准的——调整以完全匹配先例或种子格式。

```
UNANIMOUS WRITTEN CONSENT
[OF THE BOARD OF DIRECTORS / OF THE [COMMITTEE NAME]]
OF [COMPANY NAME]

[Date]

The undersigned, constituting all of the members of the
[Board of Directors / [Committee]] of [Company Name], a [State] [corporation /
limited liability company] (the "Company"), hereby adopt the following
resolutions by written consent pursuant to [Section X of the [State] General
Corporation Law / applicable operating agreement], in lieu of a meeting:

[AGENDA ITEM / ACTION HEADING — if multi-resolution consent]

WHEREAS, [background recital — one or two sentences stating the relevant facts
and why the board is being asked to act]; and

WHEREAS, [additional recital if needed]; and

NOW, THEREFORE, BE IT RESOLVED, that [the specific action being approved,
in precise language — name names, state amounts, reference the specific
agreement or instrument where applicable];

RESOLVED FURTHER, that [any related or implementing resolution — e.g., the
specific officers authorized to sign documents, the authority granted];

RESOLVED FURTHER, that the officers of the Company are, and each of them
hereby is, authorized and directed, in the name and on behalf of the Company,
to take all actions and to execute and deliver all documents, instruments,
certificates and agreements as such officers may deem necessary or appropriate
to carry out the intent and purposes of the foregoing resolutions; and

RESOLVED FURTHER, that any actions previously taken by any officer of the
Company in connection with the foregoing are hereby ratified, confirmed and
approved in all respects.

[Repeat WHEREAS / RESOLVED block for each additional action if multi-resolution consent]

This Written Consent may be executed in one or more counterparts, each of
which shall be deemed an original and all of which together shall constitute
one and the same instrument. Electronic signatures shall be deemed original
signatures for all purposes.

[SIGNATURE BLOCKS — one per required signatory]

_______________________________
[Director Name]
[Title, if applicable]
Date: _______________

[Repeat for each director / committee member]
```

### 决议起草注释

- **要精确。** 模糊的决议在尽职调查中会产生问题。"Approved the transaction"没有用处。"批准于 [日期] 在 [买方] 和 [公司] 之间的资产购买协议，其形式基本作为附件 A 附于此"是有用的。
- **命名授权签署人。** 如果特定官员需要特定事物的授权，不要只说"officers"。命名他们。
- **引用附件。** 如果正在批准文档，将其作为附件附加并在决议中引用。同意只有在其特异性时才有用。
- **完全匹配内部语言。** "RESOLVED, THAT" 与 "BE IT RESOLVED" 与 "RESOLVED"——使用先例或种子文档中的任何内容。不要在同意内切换格式。

---

## 步骤 4：确认注册州的同意规则

检查 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` 中的注册州。在起草之前研究该州的书面同意要求：

- 董事会书面同意是否需要一致，还是允许较低门槛？
- 是否需要向非签署董事发出通知？在什么时间？
- 是否需要向非签署股东发出通知（对于股东同意）？在什么时间？
- 什么形式的签名有效（湿墨、电子、副本）？
- 章程或细则是否覆盖任何默认规则——例如，更高的签名门槛、不同的通知窗口、限制可通过同意采取的行动？

引用控制的法规章节以及依赖的任何章程/细则条款。验证时效性——州公司法定期修订。标记不确定性以供律师验证，而不是陈述你未确认的规则。

如果 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` 在这些问题上记录了内部立场，应用它并记录依赖的法律支持。向输出添加简短的"州法律通知"块，总结你确认（或标记）的内容，以便用户不留下疑问。

---

## 步骤 4.5：后果行动关卡（执行同意）

**在继续输出之前：** 阅读 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` 中的 `## Who's using this`。如果角色是**非律师**：

> 执行书面同意有法律后果——它约束实体并成为公司记录。你是否已与律师审查此内容？如果是，继续。如果不是，这是带给他们的简报：
>
> - 行为是什么（决议）
> - 分析发现的内容（州法律通知、签名门槛、任何标记的冲突）
> - 未决问题（上面标记为需要律师验证的任何内容）
> - 可能出什么问题（无效同意、违反信托义务、签名缺陷、冲突未正确处理）
> - 要问律师什么（这是否正确的载体；是否有遗漏的叙述；章程/细则是否允许对此行动进行同意）
>
> 如果你需要找到律师、事务律师、大律师或其他授权法律专业人士：联系你的专业监管机构（美国的州律师协会、英格兰和威尔士的 SRA/律师标准委员会、苏格兰/北爱尔兰/爱尔兰/加拿大/澳大利亚的律师协会或你司法管辖区的同等机构）以获取推荐服务。

在没有明确是的情况下，不要越过此关卡产生最终签署就绪的草案。研究、格式提取和标记为供律师审查的草案都可以。

---

## 步骤 5：输出

生成：

1. **同意草案**——完整、准备审查和分发。执行的书面同意本身是公司记录，不是特权；不要将工作产品标题应用于分发的同意。下面的起草注释、签署人跟踪器和分析是工作产品——根据 `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md` `## Outputs` 在其前面添加工作产品标题（根据用户角色而不同——见 `## Who's using this`）：

   ```
   [WORK-PRODUCT HEADER — per plugin config ## Outputs — differs by role; see `## Who's using this`]
   ```

2. **签署人检查清单：**
```
[WORK-PRODUCT HEADER — per plugin config ## Outputs — differs by role; see `## Who's using this`]

SIGNATORY CHECKLIST — [Action] — [Date]

Required signatories (unanimous consent required):
□ [Director Name 1]
□ [Director Name 2]
□ [Director Name 3]
[etc. — pulled from board composition in `~/.prismclaw/assets/plugins/legal-chineselaw/corporate-legal/PRISMCLAW.md`]

Conflict disclosures:
[None / [Director Name] has a disclosed interest — confirm whether recusal or disclosure is appropriate]

State law notice: [confirmed-rule-for-state-of-incorporation / confirm]
```

3. **审查提示：**
```
[WORK-PRODUCT HEADER — per plugin config ## Outputs — differs by role; see `## Who's using this`]

BEFORE CIRCULATING — check:
□ Resolution language precisely describes the action (no vague approvals)
□ Correct effective date
□ All required exhibits attached and referenced
□ Authorised signatories named correctly
□ Any director conflicts disclosed or resolved
□ For major actions: outside counsel has reviewed
```

4. **草案上的最终注释——在分发前添加。** 作为单独的执行前注释附加到同意草案之前，然后在同意签署前剥离：

> 这是供律师审查的草案，而不是已执行的同意。执行它约束实体并成为公司记录——执业律师审查、根据需要进行编辑并在其发出时承担专业责任。不要未经审查分发以供签名。

---

## 此 skill 不做什么

- 它不确定行动在法律上是否需要董事会批准——该判断属于律师。
- 它不建议董事信托义务或利益冲突解决——它标记冲突，律师处理它们。
- 它不替代主要交易的外部律师审查——范围警告是真实的，而不是样板。
- 它不分发同意——输出供律师审查并通过自己的流程发送。
- 它不跟踪返回的签名——签署人检查清单是起点；签名跟踪是手动的，或由你的文档管理流程处理。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
