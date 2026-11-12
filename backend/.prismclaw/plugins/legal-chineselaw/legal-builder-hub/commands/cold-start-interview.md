---
name: builder-cold-start-interview
description: '执业档案访谈，推荐并安装中国法 skill 入门包（法律元力 7个 legal-skillpack-* 优先）。 这是整个生态系统的冷启动——它询问您是哪种律师并推荐首先安装什么。
  在全新安装时、当用户说"让我开始"或"我应该安装什么"时使用， 或在添加/移除 MCP 连接器后重新运行集成可用性检查时使用。

  '
argument_hint: '[--redo] [--check-integrations]'
category: 技能管理
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /builder-cold-start-interview

1. 检查 `~/.prismclaw/assets/plugins/legal-chineselaw/legal-builder-hub/PRISMCLAW.md`。
2. 运行第 0 部分（角色 + 集成检查），然后是五个问题（执业类型、行业、团队、工具使用习惯），按照以下工作流。
3. 将档案匹配到注册表 skills。推荐入门包。
4. 展示每个推荐 skill 的 SKILL.md 摘要。用户选择。
5. 安装选定的 skills。写入 `~/.prismclaw/assets/plugins/legal-chineselaw/legal-builder-hub/PRISMCLAW.md`（按需创建父目录），包含 `## 谁在使用这个`、`## 可用集成`、档案 + 已安装列表。

**`--check-integrations`：** 仅重新运行第 0 部分的集成可用性检查。更新 `~/.prismclaw/assets/plugins/legal-chineselaw/legal-builder-hub/PRISMCLAW.md` 中的 `## 可用集成` 表格，不触碰角色或执业档案。在添加或移除 MCP 连接器后使用。

在探测时：仅在 MCP 工具调用实际成功时报告 ✓。已配置但未测试的连接器应标记为 ⚪，附一行确认方法。永不仅基于 `.mcp.json` 声明报告 ✓——那会误导用户认为某些东西已连接而实际上没有。

---

## 冷启动检查

读取 `~/.prismclaw/assets/plugins/legal-chineselaw/legal-builder-hub/PRISMCLAW.md`：
- **不存在** → 开始访谈。
- **包含 `<!-- SETUP PAUSED AT: -->`** → 问候用户并提供从该部分恢复。
- **包含 `[PLACEHOLDER]` 标记但没有暂停注释** → 模板从未完成；提供从头开始或从占位符开始的地方恢复。
- **已填充（无占位符，无暂停注释）** → 已配置；除非 `--redo`，否则跳过。

模板结构位于 `${PRISMCLAW_PLUGIN_ROOT}/PRISMCLAW.md`——将其用作章节脚手架。将完成的执业档案写入配置路径，按需创建父目录。

## 检查共享公司档案

查找 `~/.prismclaw/assets/plugins/legal-chineselaw/company-profile.md`。

- **如果存在：** 读取它。显示一行确认："您是 [姓名]，[执业设置]，在 [公司]，[行业]，在 [司法管辖区] 运营。对吗？（或者说 'update' 更改共享档案。）" 如果确认，跳过公司问题——直接进入插件特定的问题。
- **如果不存在：** 您将是该用户设置的第一个插件。在导向和分叉后，询问公司问题并将其写入共享档案（按照插件根目录中 `references/company-profile-template.md` 的模板），然后继续插件特定的问题。告诉用户："我已保存您的公司档案——其他法律插件将读取它并跳过这些问题。"

属于共享档案的公司问题（如果已存在则不应重新询问）：执业设置、公司名称、行业、销售内容、规模、司法管辖区、监管机构、风险偏好、升级联系人。插件特定的问题（剧本立场、审查框架、内部风格、监督模式等）保持在每个插件。

## 目的

此插件是中国法律 skill 的应用商店入口。冷启动访谈是入职推荐引擎——询问您做什么（以中国法律执业类型为主），推荐来自法律元力（yuanli.ailaw.cn）的中国法 skillpack，安装您选择的。

与其他冷启动不同，这个很短。五个问题，一个推荐，完成。

## 安装范围检查

在导向之前，如果您注意到工作目录在项目内（不是用户的主目录），标记它。说一次：

> **注意——看起来此插件可能是项目范围的，这意味着我只能读取 [当前目录] 中的文件。如果您希望我从其他地方读取文档（Downloads、Documents、Dropbox），请改为用户范围安装——参见 QUICKSTART.md。您可以继续使用项目范围，但需要将文件移到此文件夹中。**

在继续之前要求用户确认：使用项目范围继续，或暂停以用户范围重新安装。如果工作目录就是用户的主目录，静默跳过此检查。

## 访谈开始之前

首先显示此导言（3-4 行简短内容，仅此而已）：

> **`legal-builder-hub` 用于查找、安装和管理社区贡献的法律 skills。** 在寻找执业领域工作流？直接安装 `legal-*` 插件之一；运行 `/legal-builder-hub:registry-browser` 查看有什么可用的。
>
> **2 分钟**让您获得角色和执业领域——加上注册表监控列表、更新频率和默认宽松的 allowlist 的工作默认值。**15 分钟**添加与您的执业匹配的校准入门包、写入 `allowlist.yaml` 的可信来源策略（注册表、发布者、从您的部署上下文播种的许可证）、更新通知偏好，以及用于推荐的行业/团队规模信号。
>
> 快速还是完整？（随时可以通过 `/legal-builder-hub:cold-start-interview --full` 升级。）

## 用户选择快速或完整后

用户选择后，引导他们。用您自己的语言覆盖：

- **此插件维护什么：** 您的执业档案（可信来源、更新偏好、部署上下文）、一个控制安装的 `allowlist.yaml`，以及一个安装日志。
- **此设置做什么：** 帮助用户发现、安装和评估社区法律 skills——一个执业档案驱动的入门包加上一个设计质量检查，然后才接触他们的工作流。学习执业档案和更新偏好并将它们写入一个纯文本文件，插件每次都会读取。一切都可以稍后更改。
- **数据来源：** 设置仅从用户的回答构建新的执业档案。它不读取个人 PrismClaw 历史、其他对话或主目录 PRISMCLAW.md。如果在此对话中早些时候出现了相关内容（例如，用户提到了他们的律所或团队），在纳入之前先询问。除非用户输入或批准，否则不会添加任何内容到配置中。

**为什么这很重要。** 中心的入门包推荐和自动更新程序的过滤都读取此访谈写入的档案。通用档案获得通用入门包——合理有用但与用户实际执业不匹配的 skills。告诉中心用户是哪种律师以及他们最常做什么是"这里有其他律师构建的所有 skills"和"这是与您的工作匹配的集合"之间的区别。回答越具体，推荐就越感觉像用户自己的。

### 快速开始或完整设置——分叉

用户在导言中选择了快速或完整。分叉：

**快速开始路径：** 仅询问角色和执业领域。用 `[DEFAULT]` 标记写入其他所有内容的配置。关闭时说："完成。您现在可以开始浏览和安装了。我为注册表监控列表和更新频率使用了合理的默认值。随时运行 `/legal-builder-hub:cold-start-interview --full` 进行完整访谈，或 `/legal-builder-hub:cold-start-interview --redo <section>` 重做某个部分。"

**完整设置路径：** 以下现有访谈流程。

## 访谈节奏

- **假设答案存在于某处。** 当问题询问可能已写好的信息——公司描述、剧本、升级矩阵、风格指南、手册、司法管辖区列表、事项组合——在要求用户凭记忆输入之前先提示链接或粘贴。"粘贴链接或文档，或者给我简短版本"是超过一句话的任何内容的默认询问。让用户重新输入已经写好的内容的访谈官没有完成访谈官的首要工作。

虽然此访谈很短，但五个问题各不相同——执业领域和行业是点击选择，但"您最常做的事情"需要一个真正的答案。当问题需要的不仅是快速点击时：

- **提问并等待。** 明确说："这个需要输入答案——我会等。" 在用户回应之前不要进入下一个问题。
- **如果跳过了任何内容：** "暂时跳过，我会在您的档案中标记——您可以稍后用 `--redo` 填充。" 然后继续，但跟踪跳过。
- **在写入档案和推荐入门包之前：** 如果任何答案被跳过或保留为占位符，列出它们并询问："想要现在填充这些吗，还是保留为占位符？您的入门包推荐仅与档案一样好。" 然后等待。
- **永不**在有静默间隙的情况下写入档案——每个占位符都应该是用户确认的刻意跳过。
- **批量大小——计算子部分。** "每轮不超过 2-3 个问题"意味着 2-3 个*可回答的提示*，计算子部分。一个有 5 个子部分的问题是 5 个问题。测试标准：用户可以在不滚动的情况下回答吗？如果不能，就太多了。尽可能优先使用结构化的点击选择问题——它们不需要滚动或输入。
- **暂停和恢复。** 预先告诉用户："如果您需要停止，说 'pause'（或 'stop'，或 '让我稍后再来'），我会保存您的进度。稍后再次运行 `/legal-builder-hub:cold-start-interview`，我会从您停下的地方继续。" 当用户暂停时，将部分配置写入 `~/.prismclaw/assets/plugins/legal-chineselaw/legal-builder-hub/PRISMCLAW.md`，顶部附有 `<!-- SETUP PAUSED AT: [section name] — run /legal-builder-hub:cold-start-interview to resume -->` 注释，未回答的字段上有 `[PENDING]` 标记（与 `[PLACEHOLDER]` 不同）。当设置重新运行并发现暂停的配置时，问候用户："欢迎回来。您在 [section] 暂停了。您之前的答案已保存。从我们停下的地方继续，还是重新开始？" 不要重新询问已回答的问题。

**在设置中验证用户陈述的法律事实。** 当用户用特定的规则引用、法规编号、案例名称、截止日期、阈值、司法管辖区或注册号回答访谈问题时——并且这是您可以合理性检查的内容——在写入配置之前进行验证。如果用户说的与您的理解或他们粘贴的内容冲突，提出来："您说阈值是 X；我的理解是 Y——您能确认哪个写入档案吗？`[premise flagged — verify]`" 写入 PRISMCLAW.md 的错误事实会传播到每个未来输出；在这里捕捉它是产品中最高杠杆的时刻之一。

## 访谈

### 开场

> 我将帮助您查找和安装社区法律 skills——其他律师构建和分享的内容。首先，您是哪种律师？我会推荐一个入门包。

### 第 0 部分：谁在使用这个，以及连接了什么

在执业档案之前的两个快速问题。这些问题塑造插件的工作方式，而不是它能做什么。

#### 谁在使用这个？

> 谁每天使用此插件？（这会传递到您安装的每个插件的角色信号——具有非律师模式的 skills 从这里读取而不是重新询问，并且 `recommend` 和 `qa` 输出在适当的时候为非律师读者构建结构。）
>
> 1. **律师或法律专业人士** — 律师、律师助理、在律师监督下工作的法律运营人员。
> 2. **有律师渠道的非律师** — 创始人、业务负责人、合同经理、人力资源、采购；您有内部或外部律师可以咨询。
> 3. **没有定期律师渠道的非律师** — 您自己处理这些。

如果答案是 2 或 3，说一次：

> 此插件发现和安装中国法 skills。您安装的 skills 将根据您的角色有自己的护栏——我会将您在这里的回答传递给后续插件，这样您就不必每个插件都回答一次。

如果答案是 3，添加：

> 如果您需要寻找律师：联系您所在地区的律师协会（各省市律师协会均提供律师查询和推荐服务，是最快的起点）。许多律师提供免费或低成本的初次咨询。对于小企业，当地法学院法律援助诊所和各地法律援助中心可以指引方向。

#### 连接了什么？

> 此插件可以与企业协作/钉钉（用于新 skill/更新通知）和元典（用于法律引用质检）配合使用。让我检查哪些连接器已配置——需要它们的功能将正常工作，没有它们的功能将优雅地回退到手动操作，而不是静默失败。

**检查实际连接了什么，而不是配置了什么。** `.mcp.json` 中列出的连接器是*可用的*。实际响应的连接器是*已连接的*。这些是不同的，混淆它们会破坏信任。对于此插件使用的每个连接器：

- 如果您可以测试连接（调用简单的 MCP 工具如列表或搜索），仅在成功响应时报告 ✓。
- 如果您无法测试（无法从这里探测），报告 ⚪ "已配置但未验证——打开您的 MCP 设置确认"，附一行操作方法。
- 永不仅基于配置报告 ✓。

对于显示为未连接的连接器，告诉用户如何连接。示例措辞："企业协作未连接。在 PrismClaw 中：将 Enterprise Collaboration MCP 添加到您的配置。此插件没有它也能工作——更新通知会在下次运行 `/legal-builder-hub:registry-browser` 或 `/legal-builder-hub:auto-updater` 时显示，而不是主动推送——但连接它可以使通知实时化。"

然后以此格式报告发现：

> - ✓ [集成] — 已连接（已测试）
> - ⚪ [集成] — 已配置但未验证。打开您的 MCP 设置确认。
> - ✗ [集成] — 未找到。[功能] 将回退到 [手动替代方案]。[如何连接。]

您不需要这些。核心功能——浏览、安装、QA、更新——仅凭文件访问即可工作。

将第 0 部分答案写入插件配置的 `## 谁在使用这个` 和 `## 可用集成`。此插件写入 `## 谁在使用这个`，以便之后安装的其他插件可以从这里读取角色，而不是重新询问。

在五个问题之前："您是否已有社区 skill 注册表监控列表，或团队使用的 allowlist/blocklist？粘贴内容、分享文件路径，或者说 'no'，我会添加默认值。如果您分享一个，我会读取它并将这些注册表加上您的 allowlist 添加到档案中，而不是让您重新输入。（这会传递给 /legal-builder-hub:skill-installer——安装器在获取任何内容之前读取 `allowlist.yaml`，并在限制模式下阻止不在列表中的来源。）"

**部署上下文。** 在 allowlist 问题之后、写入文件之前，询问：

> "您打算如何使用安装的 skills——仅自己使用、在律所内共享，还是嵌入到您交付给他人的产品或服务中？(Personal / Firm-internal / Product-embedding。)（这会传递给 `allowlist.yaml`——部署上下文播种 `licenses:` 列表，/legal-builder-hub:skill-installer 拒绝获取许可证不在该列表中的 skill。）这设置了您的许可证默认值。大多数开源许可证适合个人使用。律所内部增加了文件级 copyleft（LGPL、MPL——当您不分发时没问题）。产品嵌入是最严格的：强 copyleft（GPL、AGPL）创建需要在交付前进行法律审查的义务，所以这些会被标记而不是设为默认。"

在档案的 `## 我信任的来源` 中记录答案为 `Deployment context: [personal | firm-internal | product-embedding]`。以下 allowlist 的 `licenses:` 播种读取它。

**将 allowlist 写入 `allowlist.yaml`，而不仅是档案。** 安装器的门槛读取 `~/.prismclaw/assets/plugins/legal-chineselaw/legal-builder-hub/allowlist.yaml`，而不是 PRISMCLAW.md。如果您仅在档案中记录答案，安装器看到空 allowlist 并回退到宽松模式，无论用户说了什么——静默击败了标题结构防御。在此问题之后：

1. 在配置路径写入 `allowlist.yaml`，遵循 `${PRISMCLAW_PLUGIN_ROOT}/references/skill-installer/allowlist.md` 中的 schema：
   - `mode:` — 模板默认为 `restrictive`（失败关闭）。为个人/小团队提供 `permissive`（他们没有 IT 策划的发布者列表，所以限制模式会拒绝一切）。中型/大型律所、内部或政府保持 `restrictive`（他们有需要严格门槛的安全策略）。始终确认："我正在将 allowlist 设置为 [mode]。Restrictive 拒绝未知来源直到您添加它们——最安全，但您需要批准每个新发布者。Permissive 标记未知来源并在安装前询问您——更方便，不那么严格。您想要哪个？" 永不在没有明确用户同意的情况下写入 permissive。
   - `registries:` — 用户提供的加上默认值。
   - `publishers:` — 用户命名的或拥有可信注册表的 GitHub 所有者/组织。
   - `connectors:` — 除非用户提供列表否则为空；在限制模式下，提示："限制模式需要连接器 allowlist——粘贴批准的 MCP 服务器 URL，或者我将其留空，声明任何连接器的 skills 将被拒绝。"
   - `licenses:` — 根据上面的部署上下文答案播种：
     - **Personal** → `MIT`、`Apache-2.0`、`BSD-2-Clause`、`BSD-3-Clause`、`ISC`、`CC0-1.0`。
     - **Firm-internal** → 与 Personal 相同加上 `LGPL-2.1-only`、`LGPL-3.0-only`、`MPL-2.0`。
     - **Product-embedding** → 与 Personal 相同。还在 `allowlist.yaml` 顶部写入注释：`## 交付前需要许可证审查——不在此列表中的任何内容需要法律签署。` 强 copyleft（GPL、AGPL）在此默认中故意排除；添加这些需要刻意编辑。
2. 还在档案的 `## 我信任的来源` 部分中总结，以便人类可以看到策略。
3. 告诉用户它在哪里："您的 allowlist 位于 `~/.prismclaw/assets/plugins/legal-chineselaw/legal-builder-hub/allowlist.yaml`。安装器在获取任何内容之前读取它。"

如果用户上传了注册表/allowlist 文件：读取它，提取注册表 URL 和 allowlist/blocklist 条目，确认您找到了什么，按照 schema 写入 `allowlist.yaml`，并在档案中总结。

**新鲜度提醒。** 在 allowlist 问题之后（部署上下文已设置）和五个问题之前，询问：

> "当社区 skill 捆绑参考材料——法规、法律、程序模板——应该信任多长时间后我再提醒您验证它是否仍然最新？（6 个月是监管内容的常见默认值。12 个月用于程序/风格内容。如果您在快速变化的领域工作，设置更紧。）"

接受单个数字（应用于监管；以下类别的默认值用于其他）或按类别的答案。验证每个答案的形状为 `N days`、`N months` 或 `N years`，其中 `N` 为正整数 ≤ 120——拒绝自由形式散文并重新询问。

将答案写入档案中的 `## 新鲜度提醒` 部分（在 `## 我信任的来源` 之后、`## 已安装的入门包` 之前插入）：

```markdown
## 新鲜度提醒

| 内容类别 | 提醒前的最大年龄 | 理由 |
|---|---|---|
| regulatory | 6 个月 | 监管机构频繁更新；执法优先级变化 |
| procedural | 12 个月 | 法院规则和程序变化较慢 |
| stylistic | 24 个月 | 内部风格、格式模板 |
| unknown | 3 个月 | 未声明新鲜度的 skill 被谨慎对待 |

当 skill 的 `last_verified` + `freshness_window` 已过，或用户的阈值（上方）已过——以更紧者为准——skill-installer 在运行前显示警告。
```

如果用户给了更紧的数字，用它们替换默认值。如果用户说"使用默认值"，按原样写入表格。

**如果用户没有上传注册表列表：** 在五个问题之后，提供："想要我将您的监控注册表和更新偏好写成一个独立的策略笔记，您可以与团队分享吗？与我要保存到档案的内容相同，格式化以便队友或新构建者可以看到您信任哪些来源以及您希望如何处理更新。"

### 五个问题

1. **执业领域** — 内部还是律所？商业、隐私、产品、雇佣、诉讼、并购，还是其他？（这会传递给 /legal-builder-hub:related-skills-surfacer——执业领域是映射到入门包的主键。）

   **不适合分类框的执业。** 如果用户的执业不匹配选项（国际仲裁、国际公法、仅法庭之友、学术咨询、公益面板、部落法院、军事司法、海事，或其他标准类别假设不涵盖的内容），提供："听起来您的执业不适合我的常用类别。用您自己的话告诉我——您做什么，为谁做，什么司法管辖区和论坛，工作是什么样的——我会从这些构建您的档案，而不是强迫您适应不适合的框。我会跳过或调整不适用的问题。" 然后从自由形式描述构建档案，标记哪些模板字段被填充、调整或因不适用而留空。从强行适应构建的档案比从真实情况构建的稀疏档案更差。

2. **行业** — 科技、医疗、金融、其他、无所谓？（这会传递给 /legal-builder-hub:related-skills-surfacer 和 /legal-builder-hub:registry-browser——行业缩小入门包并过滤注册表结果。）

3. **团队规模** — 个人、小团队（2-5人）、大型法务部？（这会传递给 `allowlist.yaml` 模式默认——个人/小团队获得 permissive，中型/大型/内部/政府获得 restrictive。）

4. **您最常做的事情是什么？** — 合同审查、合规、发布审查、交易支持、书状撰写等。（这会传递给 /legal-builder-hub:related-skills-surfacer——当您在做社区有 skill 的事情时，surfacer 会提醒您。）

5. **工具使用习惯** — 构建者（您编写自己的 skills）、修补者（您编辑已安装的内容）、只要能用就行（您希望开箱即用）？（这会传递给 /legal-builder-hub:related-skills-surfacer——构建者获得原始注册表和 /legal-builder-hub:skills-qa 框架；只要能用就行获得精选的、可用的包。）

### 推荐

将档案映射到中国法注册表 skillpack（来自法律元力）：

| 档案/实践侧重 | 推荐 toolkit_id | 补充来源 |
|---|---|---|
| 商事合同/供应商协议/SaaS | `legal-skillpack-commercial-legal` | lpm-skills（事项接案、范围控制） |
| 公司法务/并购/尽调 | `legal-skillpack-corporate-legal` | lpm-skills（事项规划、预算） |
| 诉讼/仲裁 | `legal-skillpack-litigation-legal` | lpm-skills |
| 监管合规（数据/金融/环保） | `legal-skillpack-regulatory-legal` | — |
| 数据合规/个人信息保护 | `legal-skillpack-regulatory-legal` + 数据合规专项 | — |
| AI 治理合规 | `legal-skillpack-ai-governance-legal` | — |
| 法律研究/检索 | `legal-skillpack-legal-research-cn` | — |
| 法学生/法考备考 | `legal-skillpack-law-student` | — |
| 个人/小团队 | 轻量级——分流 skills 优先于完整审查 skills | 从商事合同开始 |
| 构建者 | 原始注册表和 skills-qa 框架——他们会构建和验证自己的 | — |

**安装流程：**
1. 先通过 `yuanli-cn` 注册表（法律元力）查询上述 toolkit_id
2. 展示每个推荐 skillpack 的描述（来自注册表目录文件或 API 返回）
3. 用户选择要安装哪些
4. 通过 `skill-installer` 安装选定的 skillpack——未经 yes 不安装任何东西

对于每个推荐的 skillpack：展示描述和包含的 skill 清单。让他们选择——未经 yes 不安装任何东西。

## 写入执业档案

简短。档案 + 已安装列表 + 注册表偏好。按照 `${PRISMCLAW_PLUGIN_ROOT}/PRISMCLAW.md` 的模板。

## 写入后

**展示此插件能做什么。** 关闭前，提供：

> **想看看我能帮什么吗？**

如果是，展示此定制列表（不是通用模板——这些是此插件最擅长的具体事项）：

> **以下是我在法律 skill 管理方面擅长的：**
>
> - **浏览社区法律 skills** — 例如，"看看其他从业者为您执业领域构建了什么。" 试试：`/legal-builder-hub:registry-browser`
> - **从注册表安装 skill** — 例如，"将社区 skill 添加到您的环境——许可证门控和 allowlist 检查在运行前完成。" 试试：`/legal-builder-hub:skill-installer`
> - **检查更新** — 例如，"查看哪些已安装的 skills 在源注册表中有更新版本。" 试试：`/legal-builder-hub:auto-updater`
> - **获取 skill 推荐** — 例如，"基于其他插件的最近活动，展示值得尝试的 skills。" 试试：`/legal-builder-hub:related-skills-surfacer`
> - **根据设计框架评估 skill** — 例如，"对 skill 运行法律 Skill 设计框架——九个设计参数、三个失败模式、信任表面检查。" 试试：`/legal-builder-hub:skills-qa`
>
> **我建议您的第一个：** 浏览注册表并挑选一个匹配当前项目的 skill——安装它并感受 allowlist 门槛。或者告诉我您手头有什么，我来挑选。

这在一次提供中解决了冷启动问题（督导不知道首先该做什么）和价值主张问题（他们不知道插件能做什么）。使列表具体。如果督导在访谈中已经命名了具体的首个任务，跳过此步骤。


- "这是我安装的内容。想看看注册表中还有什么吗？"
- "related-skills-surfacer 会在您做社区有 skill 的事情时提醒您。想要开启还是关闭？"
- **在第一个需要引用权威的已安装 skill 之前，连接研究工具。** 说："在第一个需要引用权威的已安装 skill 之前：连接研究工具，如果安装的插件需要的话。没有的话，skills 会将每个引用标记为未验证。在 PrismClaw 中：在 skill 提示时授权。"

<!-- COLLATERAL LINKS: when onboarding collateral exists, add here:
     "Want a walkthrough first? [Watch the 3-minute intro](URL) or [read the getting-started guide](URL)." -->

然后用"您可以稍后更改任何内容"的注释关闭：

> 完成。您的配置位于 `~/.prismclaw/assets/plugins/legal-chineselaw/legal-builder-hub/PRISMCLAW.md`——一个您可以直接读取和编辑的纯文本文件。您回答的任何内容都可以更改：
>
> - 直接编辑文件进行快速更改
> - 运行 `/legal-builder-hub:cold-start-interview --redo` 进行完整重新访谈
> - 运行 `/legal-builder-hub:cold-start-interview --check-integrations` 重新检查连接状态
>
> 最常被调整的内容：监控的注册表（添加或移除来源）、更新偏好（通知 vs. 手动），以及执业档案的范围（随着工作变化添加行业或第二执业类型）。您的配置会随着使用插件而改善——如果推荐感觉不对，通常是档案的问题。

## 您的执业档案会学习

写入执业档案后，以此注释关闭：

> **您的执业档案会学习。** 随着您使用插件它会变得更好：
>
> - 当 skill 的输出感觉不对时，通常是某个需要调整的立场。输出会告诉您是哪个。
> - 您随时可以说"更新我的剧本以偏好 X"或"将我的升级阈值改为 Y"，相关的 skill 会写入更改。
> - 运行 `/legal-builder-hub:cold-start-interview --redo <section>` 重新访谈某个部分，或直接编辑配置文件。
>
> 十分钟的设置给您一个可用的档案。一个月的使用给您一个读起来像是您自己写的档案。

## 默认监控的注册表

- **法律元力（yuanli-cn）** (yuanli.ailaw.cn) — 中国法主注册表，7个 legal-skillpack-*；优先级 1
- **lpm-skills** (github.com/legalopsconsulting/lpm-skills) — 法律项目管理，与执业领域无关；优先级 2
- 用户可以通过 `/legal-builder-hub:registry-browser` 添加其他注册表

> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
