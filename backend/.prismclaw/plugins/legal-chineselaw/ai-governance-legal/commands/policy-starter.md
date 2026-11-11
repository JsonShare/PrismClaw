---
name: policy-starter
description: 从发布的模型政策起草律所 AI 使用政策，适配你的执业档案——这是一个研究和综合工具，其输出是律师审查和采用的草案，而不是完成的政策。当用户说"起草
  AI 政策"、"我们需要 AI 政策"、"构建 AI 使用政策"、"我们律所需要 GenAI 政策"或类似请求以生成首个内部 AI 政策草稿时使用。
argument_hint: '[optional — scope hint, e.g. ''firm-wide'', ''legal team only'', ''update
  existing'']'
category: AI治理
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /policy-starter

1. 阅读 `~/.prismclaw/plugins/legal-chineselaw/ai-governance-legal/PRISMCLAW.md`。如果执业档案未填充，停止并指导运行 `/ai-governance-legal:cold-start-interview`。
2. 使用以下框架。
3. 运行范围访谈——政策需要覆盖哪些章节，谁是受众，部署上下文是什么。不要跳到起草。
4. 网络搜索与部署上下文相关的最新发布模型政策和指导（ABA、州律师协会、ILTA、CLOC、NIST、同行律所/同行公司政策、当前州 AI 法律、欧盟 AI 法案、适用部门监管机构）。
5. 起草选定的章节，源自模型政策，在每个选择点上使用 `[review]` 标记，在每个章节底部使用 `[review]` 未决问题。
6. 使用草案标题输出（"DRAFT FOR INTERNAL LEGAL REVIEW — NOT FOR DISTRIBUTION"）、来源块、审阅者备注和采用检查清单。
7. 以下一步决策树结束。

```
/ai-governance-legal:policy-starter
/ai-governance-legal:policy-starter "we need an AI policy for our 30-lawyer firm"
/ai-governance-legal:policy-starter "update our existing policy for the 2026 state AI laws"
```

---

## Matter context

**事项上下文。** 检查执业级 PRISMCLAW.md 中的 `## Matter workspaces`。如果 `Enabled` 为 `✗`（内部用户的默认值），跳过本段的其余部分——skills 使用执业级上下文，事项机制不可见。如果已启用且没有活跃事项，询问："这是哪个事项的？Run `/ai-governance-legal:matter-workspace switch <slug>` 或说 `practice-level`。"加载活跃事项的 `matter.md` 以获取事项特定上下文和覆盖。将输出写入事项文件夹 `~/.prismclaw/plugins/legal-chineselaw/ai-governance-legal/matters/<matter-slug>/`。永远不要阅读另一个事项的文件，除非 `Cross-matter context` 为 `on`。

---

## Purpose

很多律所和内部团队还没有书面 AI 使用政策，或者运行 2024 年版本的，没有提及州 AI 法律、欧盟 AI 法案实施法案、2025 年 COPPA 修正案，或者他们最终对 Copilot 和 PrismClaw for Work 做了什么。此 skill 产生**草案**政策带给决策者——GC、管理合伙人、执行委员会、董事会、IT 负责人、HR 负责人——不是要分发的完成政策。

此 skill 的纪律：

1. **从发布的模型政策来源，而不是发明。** 搜索和阅读 ABA AI 工具包、州律师协会指导、ILTA 模型政策、CLOC 模板以及公开的同行律所/同行公司政策。引用每个来源所说的并调整它——不要凭空生成政策语言。
2. **在起草之前对范围进行决策树。** 试图覆盖一切的政策什么也不覆盖。询问用户政策需要哪些章节。让他们选择。然后为每个选择的章节构建，在每个选择点上使用 `[review]` 标记。
3. **标记每个判断调用。** 输出是律师审查和采用的草案；每个阈值、每个命名工具、每个披露触发器、每个执法后果都是 `[review]` 行。
4. **标题显示受众范围。** 此输出可能超出法律范围——HR、IT、所有员工。标题相应调整。

此 skill 不会最终确定、分发、发布或甚至对困难调用推荐特定立场。它产生草案并显示选择。

## Read `~/.prismclaw/plugins/legal-chineselaw/ai-governance-legal/PRISMCLAW.md` first

在起草之前，始终阅读执业档案。驱动草案的章节：

- `## Company profile` —— AI 角色（Builder / Deployer / 两者）、监管足迹、外部承诺、执业环境
- `## Use case registry` ——已批准什么、有条件什么或红线
- `## AI policy commitments` ——先前或当前政策已经说什么
- `## Vendor AI governance` ——团队已经从供应商要求什么
- `## Governance team and escalation` ——谁批准、谁升级
- `## Who's using this` ——角色（律师/非律师）控制标题和"采用此"框架

如果 `## AI policy commitments` 已填充，这是更新，而不是新草案——将现有政策视为基础并提议变更。如果为空，这是首个草案。

## Scope interview (do this BEFORE drafting)

询问用户政策应该覆盖哪些章节。作为检查清单呈现——用户选择，你构建。不要预先决定。

> **AI 政策应该覆盖什么？选择你希望在草案中的章节：**
> 1. **范围**——政策适用于谁（所有员工、特定角色、承包商），涵盖什么工具（仅 GenAI、所有 AI、特定供应商），什么数据在范围之外。
> 2. **允许和禁止的使用**——批准的类别、红线、"先问"情况。
> 3. **批准和审查**——谁批准新工具、谁批准新用例、如何提交审查请求、SLA 是什么。
> 4. **披露**——给客户（律所）、给法院、给对手方、给员工、给 AI 功能的最终用户。
> 5. **数据处理**——什么机密/客户/特权数据可以去哪里、数据驻留、供应商保留条款、训练数据姿态。
> 6. **培训和认证**——谁必须接受培训、什么节奏、不完成的后果。
> 7. **事件和报告**——什么算作 AI 事件、如何报告、谁处理。
> 8. **执法**——违反政策时会发生什么、链接到纪律框架。
> 9. **审查节奏和所有权**——政策多久更新一次、谁拥有更新、变更如何沟通。
> 10. **术语表**——定义的术语（GenAI、批准工具、高风险使用、后果决策、机密数据等）。
>
> 从未有政策的律所/内部法律团队的默认入门包：1、2、3、4、5、9。v1 跳过其余部分。

用户选择后，询问第二个问题：

> **起草前还有两个输入：**
> - **受众**——谁在阅读此内容？（所有员工 / 仅法律团队 / 律师加员工 / 还需要面向客户的版本）这驱动语气和术语表。
> - **部署上下文**——(a) 律所，(b) 公司的内部法律（政策涵盖法律还是全公司？），(c) 法律援助/诊所，(d) 政府。这驱动我搜索哪些模型政策。

## Source the model policies

在起草之前，运行网络搜索最新的发布模型 AI 政策和指导。

**从执业档案的 `## Regulatory footprint` 推导模型政策来源。** 不要为全球用户硬编码美国来源。

| 司法管辖区 | 模型政策来源 |
|---|---|
| US | ABA Formal Opinion 512、州律师协会指导（CA、FL、NY、TX 都有发布 AI 指导）、ILTA 模型政策、CLOC 模板、同行律所发布的 AI 政策 |
| UK | Solicitors Regulation Authority 风险展望、Law Society AI 原则、ICO AI 指导、Bar Council 指导 |
| EU | EU AI 法案合规框架（第 4 条 AI 素养、第 17 条质量管理）、国家 DPA AI 指导（CNIL、DSB、Garante、AEPD）、EDPB 指导、欧盟机构的 AI 政策 |
| Australia | Law Council of Australia AI 指导、OAIC AI 指导、州法律协会指导、Australian AI Ethics Framework |
| Singapore | PDPC Model AI Governance Framework、MinLaw 指导、MAS AI 公平原则（金融服务） |
| Canada | Law Society of Ontario/BC/Alberta AI 指导、OPC AI 指导、TBS 关于自动化决策的指令 |
| 多司法管辖区 | 使用所有适用的，并注意它们分歧的地方（例如，EU 要求人工监督文档，US 不要求；Australia 专注于自愿道德框架；Singapore 专注于部门监管） |

如果执业档案的足迹为空或 `[PLACEHOLDER]`，询问："您的组织在什么司法管辖区运营？我将从匹配您的监管环境和职业责任框架的模型政策起草，而不是以美国为中心的模板。"

对于草案使用的每个来源，**在输出的顶部以"来源"块中记录它**，包含：名称、URL、访问日期以及草案从中获取的内容。

如果无法运行网络搜索，在审阅者备注中说明："无法运行网络搜索——草案仅从训练知识来源，在采用之前根据引用来源的当前版本进行验证。"验证日志适用。

## The draft

输出遵循一致的结构。**每个选择点都获得 `[review]` 标记。** 用户必须决定；skill 显示选项。

### Header

```
DRAFT FOR INTERNAL LEGAL REVIEW — NOT FOR DISTRIBUTION
Prepared for: [来自执业档案的 firm / company name]
Date: [今天的日期]
Prepared by: ai-governance-legal policy-starter skill, adapted from published model policies
Not for adoption, distribution, posting, or reliance until reviewed, adapted, and approved by [根据执业档案治理团队章节的 attorney / GC / managing partner / executive committee]。
```

当 `## Who's using this` 中的角色为 Non-lawyer 时：在标题下添加第二行——"If you are not a licensed attorney, solicitor, barrister, or other authorised legal professional in your jurisdiction, bring this draft to your attorney contact ([来自执业档案的 name]) before using any of it. This is a starting draft for their review, not a policy you can adopt."

### Sources block (at the top, under the header)

草案从中获取的模型政策/指导/法规表：

| Source | URL | Accessed | What the draft took from it |
|---|---|---|---|
| ABA Formal Op. 512 | [url] | [date] | Disclosure and competence framing |
| ILTA Model AI Policy v.[X] | [url] | [date] | Approval workflow, data handling |
| [State] Bar Op. [X] | [url] | [date] | Disclosure to clients |
| [peer firm] published AI policy | [url] | [date] | Scope language |
| 《生成式人工智能服务管理暂行办法》 | 国家网信办 | 2023-08-15 | 生成式AI服务合规义务 |
| 《互联网信息服务算法推荐管理规定》 | 国家网信办等 | 2022-03-01 | 算法推荐服务备案要求 |
| 《人脸识别技术应用安全管理办法》 | 国家网信办 | 2025-06-01 | 人脸识别应用要求 |
| 《智能体规范应用与创新发展实施意见》 | 国家网信办等 | 2026-05-09 | 智能体治理要求 |

### Executive summary

最多三段。政策做什么，约束谁，读者在生效前必须做什么。

### The sections

仅用户选择的章节，按上述顺序。对于每个：

- **标题和范围**句子。
- **实质规则**，从引用的模型政策调整。每个具体阈值、数字、命名工具、命名供应商或升级联系人都为 `[review]`。示例："机密客户数据不得输入 [general-purpose consumer AI tools] `[review — list tools, or reference the approved-tools list]`。在 [批准的律所许可工具] `[review — list tools]` 中使用此类数据受数据处理部分约束。"
- **来源归因**内联，当规则从特定来源调整时。示例："律师必须在在客户代表中使用 AI 生成的工作产品之前验证其准确性 `[ABA Formal Op. 512]`。"
- **开放问题**在每个章节底部——律师在章节准备好之前需要做的 2-3 个决定。这些与内联 `[review]` 标记不同——这些是"我们在这里还没有立场"的项目，而不是"填充具体内容"的项目。

### Adoption checklist

在草案末尾，政策采用之前必须发生的事情检查清单。不要发明这些——从执业档案治理团队和升级章节提取。典型项目：

- [ ] GC/管理合伙人审查 `[review — name]`
- [ ] IT/安全审查 `[review — name]`
- [ ] HR 审查（用于执法/培训章节）`[review — name]`
- [ ] 董事会/执行委员会批准（如需要）`[review — confirm whether required]`
- [ ] 培训材料起草
- [ ] 公告起草
- [ ] 生效日期设定 `[review]`
- [ ] 审查节奏已安排日历 `[review — annual is typical]`
- [ ] 采用后，将政策添加到执业档案的 `## AI policy commitments` 章节

### Reviewer note

根据执业档案 `## Outputs` 部分的标题上的标准审阅者备注。使用块格式：

> **⚠️ Reviewer note**
> - **来源：**网络搜索 ✓ / 未连接——来自训练知识的引用
> - **已阅读：**执业档案 · [N] 个发布的模型政策
> - **为你的判断标记：** [N] 个内联 `[review]` 项目 · 每章节 [N] 个未决问题
> - **时效性：**搜索自 [date] 以来的发展
> - **依赖前：**这是草案——带给 [来自执业档案的审批者]，采用前不要分发

## Don'ts

- **不要发明政策语言。** 草案中的每条实质规则必须可追溯到引用来源或标记 `[review — adapted, no direct source]`。
- **不要为律师选择困难调用。** "是否应该允许律师助理使用 AI 进行首次起草工作？"是 `[review]`，而不是推荐立场。
- **不要生成完成外观的政策。** 标题、审阅者和整个过程中的 `[review]` 标记是这是草案的信号。不要软化它们。
- **不要跳过范围访谈。** 如果用户说"只是起草一个完整政策"，推回："试图覆盖一切的政策什么也不覆盖。你想要哪些章节？这是检查清单。"一轮谈判可以——两轮也可以。没有范围起草是失败模式。
- **不要生成用户未要求的章节内容。** 如果他们选择了 1、2、3、4、5、9，就做这些。不要因为"真正的政策需要培训"而添加章节 6。
- **不要推荐特定供应商、工具或后果。** 用 `[review]` 标记那些，并说明典型决定是什么，而不是用户的应该是什么。
- **不要承诺法律充分性。** 草案是律师审查的起点，而不是经过测试的政策。

## Handoffs

产生草案后，根据执业档案的决策树结束。最常见的下一步：

1. **调整草案**——用户与律师一起过一遍 `[review]` 标记并解决它们；skill 使用烘焙的决定重新运行。
2. **利益相关者摘要**——为董事会或执行委员会生成一页版本，解释政策做什么和不做什么。
3. **培训材料**——政策采用后，`/ai-governance-legal:aia-generation` 可用于生成每个用例的培训说明。
4. **供应商清理**——政策采用后，应对政策引用的供应商运行 `/ai-governance-legal:vendor-ai-review` 以检查合规性。
5. **针对新法规的差距检查**——与 `/ai-governance-legal:reg-gap-analysis` 配对，在采用之前针对特定法规或指导测试草案。

## Output scope reminder

此 skill 产生的文档到达 HR、IT 和更广泛的业务——不仅仅是法律。保持语言足够简单，非律师能理解。法律精确性在 `[review]` 标记和来源中，而不是术语中。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
