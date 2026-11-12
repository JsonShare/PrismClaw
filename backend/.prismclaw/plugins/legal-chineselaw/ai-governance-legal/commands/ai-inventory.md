---
name: ai-inventory
description: '中国 AI 治理——每个系统的清单——跟踪每个 AI 系统的角色（构建者/部署者）、 适用法规类别（生成式AI/算法推荐/深度合成/人脸识别/智能体/拟人化互动）、
  备案状态和风险等级（L1-L4）。角色和法规类别按系统逐一评估。 当用户说"AI清单"、"添加AI系统"、"我们有哪些AI系统"、"分类这个AI系统"、 "AI系统登记册"时使用。

  '
argument_hint: '[list | add | edit <id> | classify <id> | show <id>]'
category: AI治理
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /ai-inventory

## 何时运行

用户想要在欧盟 AI法案下管理其 AI 系统清单。此 skill 存在的核心概念是：**角色和层级是按系统的，不是按公司的。**单个组织可以是系统 A 的*提供者*，系统 B 的*部署者*，以及系统 C 的*进口商*。每种组合在 AI 法案下触发不同的义务。清单的存在是为了让您可以找到这些评估——义务本身是在对话中推导的，而不是从表格中得出的。

## 要做什么

1. **阅读配置。**阅读
   `~/.prismclaw/assets/plugins/legal-chineselaw/ai-governance-legal/PRISMCLAW.md`。
   如果它不存在或仍有 `[PLACEHOLDER]` 标记，请引导用户先运行 `/ai-governance-legal:cold-start-interview`。

2. **阅读清单。**清单位于
   `~/.prismclaw/assets/plugins/legal-chineselaw/ai-governance-legal/ai-systems.yaml`。
   如果它不存在，请在第一次运行 `add` 时使用空的 `systems:` 列表创建它。

3. **根据参数分派：**

   - 无参数，或 `list` → 显示清单表（请参阅下面的**列出**）。
   - `add` → 运行**添加**流程。
   - `edit <id>` → 显示当前记录，询问要更改什么，更新一个字段，确认，写入。
   - `classify <id>` → 在现有记录上运行**分类演练**，更新角色、层级、role_basis 和 tier_basis。
   - `show <id>` → 显示完整记录。

4. **在列表上，提供仪表板：**
   "需要完整的仪表板吗？按状态 / 层级 / 欧盟关系 / 所有者筛选。说一声即可。"

5. **每次操作后连接到律师的工作。**
   任何写入后，说：
   > 已记录。当您准备好演练此系统的义务时，直接询问——我将在对话中进行，并标记 AI 法案条款映射需要您验证的地方。我不从表格派生义务，因为映射很复杂且处于变化中。

## 列表格式

以紧凑表格形式呈现：

| ID | 名称 | 负责人 | 状态 | 欧盟关联 | 角色 | 层级 | 下次审查 |
|----|------|-------|--------|----------|------|------|-------------|
| sys-001 | 简历筛选 | HR / Jamie | in_production | yes | deployer | high_risk | 2026-08-01 |
| sys-002 | 邮件起草助手 | IT / Priya | in_production | no | deployer | limited | 2026-12-01 |

在表格下方，按层级显示计数，并附一行说明："N 个系统标记为 30 天内需要审查。"

## 添加流程（访谈）

逐一询问（或接受粘贴）。必填字段为 `name`、`owner`、`description`、`status`、`eu_nexus`。其余字段可以延后——明确说明："你可以稍后使用 `/ai-governance-legal:ai-inventory classify <id>` 回来进行分类。"

1. **名称。** 系统的简短标签。
2. **负责人。** 日常对其负责的人员或团队。
3. **描述。** 一两句话。它做什么，针对什么数据？
4. **状态。** `planned | in_development | in_production | deprecated`。
5. **中国监管关联。** 该系统是否向境内公众提供服务？是否具有舆论属性或社会动员能力？是否处理人脸信息/生物特征？是否提供持续性情感互动？是否具备自主决策和执行的智能体能力？逐项确认以判断适用的中国 AI 法规类别。
6. **继续分类？** 提议现在运行演练，或跳过以后再回来。

分配 ID：`sys-NNN`，其中 NNN 是文件中的下一个整数。

## 分类演练

演练产生 `role`、`role_basis`、`tier`、`tier_basis`。两个依据均标记为 `[verify against current AI Act text]`——不是因为 skill 在回避，而是因为条款映射很复杂且 AI 法案仍在分阶段推行。律师拥有验证的职责。

### 步骤 1：角色

> **谁对此系统做了什么？**

选项，附区分测试：

- **提供者：** 你开发（或委托开发）并在欧盟市场以自己的名称或商标投放或投入使用。
- **部署者：** 你在自己的权力下使用，不是为个人非专业用途。（公司内最常见。）
- **进口商：** 你将欧盟以外的提供者的 AI 系统引入欧盟。
- **分销商：** 你在欧盟市场提供 AI 系统，但不是提供者或进口商。
- **授权代表：** 你代表非欧盟提供者行事，并在欧盟设立。
- **产品制造商：** 你在自己的名称/商标下将通用 AI 系统（或其他 AI 系统）放入产品中。视为该产品的提供者。

**双重角色标记。** 如果用户实质性地修改了供应商系统（在自己的数据上微调、更改预期用途、重新品牌），即使他们最初是部署者，也可能成为修改后系统的**提供者**。当他们描述超出配置的任何修改时，指出这一点。`[verify against current AI Act text — Article 25, provider obligations and substantial modification]`

写入角色。用一句话写入 `role_basis`。

### 步骤 2：层级

> **系统做什么，用例是否属于受监管类别？**

按顺序检查：

**A. 第 5 条禁止做法。** `[verify against current AI Act text — Article 5]`

摘要，非最终文本：
- 实质性扭曲行为的潜意识或欺骗性技术
- 利用弱点（年龄、残疾、社会经济状况）实质性扭曲行为
- 公共机构导致不利待遇的社会评分
- 在公共可进入空间用于执法的实时远程生物特征识别（例外极少）
- 推断种族、政治观点、工会成员资格、宗教或哲学信仰、性生活或性取向的生物特征分类
- 在工作场所或教育中进行情绪识别（医疗和安全例外）
- 从互联网或闭路电视抓取面部图像数据库
- 仅基于性格特征的预测性警务

如果匹配 → 层级为 `prohibited`。将用例标记为停止，并路由到治理团队的禁止做法工作流。

**B. 附件 III 高风险领域。** `[verify against current AI Act text — Annex III]`

摘要：
1. 生物特征识别和分类
2. 关键基础设施（数字基础设施、道路交通、水/气/暖/电供应）
3. 教育和职业培训（访问、评估、监考、监控违禁行为）
4. 就业、工人管理、自雇访问——招聘、选拔、晋升、终止、任务分配、监控、绩效
5. 基本私人和公共服务（公共福利、个人信贷评分、人寿/健康保险的风险评估和定价、紧急调度）
6. 执法（风险评估、测谎仪、深度伪造检测、证据可靠性、画像）
7. 移民、庇护、边境管控（风险评估、旅行证件核查、申请审查）
8. 司法行政和民主进程（研究和解释、影响选举）

如果匹配 → 层级为 `high_risk`。记录附件 III 领域和子条款。

**C. GPAI。** `[verify against current AI Act text — Article 51 and surrounding]`

- **GPAI：** 在大规模广泛数据上训练、为通用性设计、能够胜任执行各种不同任务的模型。
- **GPAI + 系统性风险：** 累计计算量 > 10^25 FLOPs，或由委员会指定。

**D. 有限风险。** 与自然人互动的聊天机器人、深度伪造、超出第 5 条范围的情绪识别和生物特征分类系统——透明度义务适用。

**E. 最小风险。** 其他所有内容。

写入层级。用一句话写入 `tier_basis`，引用匹配的条款或附件条目，标记为 `[verify against current AI Act text]`。

### 步骤 3：建议

提供三个下一步选项：
1. "要我演练此系统的义务吗？我会在对话中进行——不从表格派生。"
2. "要运行 `/ai-governance-legal:aia-generation` 以产生完整影响评估吗？"
3. "要设置下次审查日期吗？我会将其添加到清单中。"

## 记录格式

```yaml
systems:
  - id: sys-001
    name: "Resume screening tool"
    owner: "HR / Jamie"
    description: "Filters inbound CVs against job criteria"
    status: in_production          # planned | in_development | in_production | deprecated
    eu_nexus: true                 # deployed, offered, or affects people in the EU/EEA
    role: deployer                 # provider | deployer | importer | distributor | authorized_rep | product_manufacturer
    role_basis: "We license from VendorX and deploy internally [verify against current AI Act text]"
    tier: high_risk                # prohibited | high_risk | limited | minimal | gpai | gpai_systemic
    tier_basis: "Annex III(4)(a) — employment, recruitment selection [verify against current AI Act text]"
    obligations_assessed: false
    obligations_note: "To assess: as deployer of a high-risk system — human oversight, input data quality, monitoring, record-keeping, informing workers, FRIA if public body/service — see Article 26 [verify against current AI Act text]"
    next_review: "2026-08-01"
    review_trigger: "on substantial modification or annually"
    created: "2026-05-11"
    updated: "2026-05-11"
```

## 为何此 skill 不自动派生义务

清单存储角色、层级和每项的依据。它**不**包含硬编码的角色 × 层级 → 义务表。

当用户询问"系统 X 的义务是什么"时，skill 在**对话中**进行分析，标记为 `[verify]`，并在需要时路由到 `/ai-governance-legal:aia-generation` 进行正式影响评估。

这是有意为之的：
- 条款映射很复杂且 AI 法案分阶段推行至 2027 年。
- 关于合规义务的自信且错误的结论会出现在董事会备忘录中。
- 清单是律师使用的登记册。律师拥有义务分析。

## 护栏

- **永远不要静默分类。** 分类演练必须可见；不要从系统描述中自动分类。
- **`[verify]` 标记保留。** 它们不是回避——它们就是重点。不要在输出中剥离它们。
- **标记实质性修改。** 每当系统修改超出配置时，提示用户重新运行 `/ai-inventory classify`——修改可以改变角色。
- **不要从表格声明义务。** 如果被询问，在对话中进行分析，并将需要正式记录的内容路由到 `/aia-generation`。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
