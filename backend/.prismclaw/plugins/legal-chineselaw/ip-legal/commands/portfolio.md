---
name: portfolio
description: '追踪 IP 投资组合——注册、续期、维护费和使用声明。用于查看即将到期的项目、添加或更新资产、记录维护申请，或审计登记册中的空缺、失效和商业使用问题。接收来自专利申请和清查工作的交接。

  '
argument_hint: '[--report [--days N] | --add | --update | --audit]'
category: 知识产权
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
-->


# /portfolio

显示即将到期的项目、添加资产、记录申请，并审计登记册。

## 说明

1. **遵循下面的工作流程**，并读取 `~/.prismclaw/plugins/legal-chineselaw/ip-legal/portfolio.yaml`。

2. **默认（无参数）：** 等同于 `--report`——显示接下来 90 天内按紧急程度分组的截止日期（🔴 已失效/宽限期内、⏰ 窗口内到期、🟡 即将到期、🌐 代理管理、❓ 未知）。

3. **`--report [--days N]`：** 模式 2。使用 `--days` 更改窗口（通常为 30 / 60 / 90 / 180）。始终按 PRISMCLAW.md → 输出 要求在前面添加工作产品标题。始终以核实说明结尾。

4. **`--add`：** 模式 3。交互式逐步添加新资产——类型、司法管辖区、编号、日期、所有者、业务负责人。如果该司法管辖区没有内置规则，则捕获自定义规则。

5. **`--update`：** 模式 4。记录维护申请或费用支付已完成、与 IP 管理系统同步，或更改资产状态。在将任何截止日期设置为 `filed` 之前，强制执行重要行动门控。

6. **`--audit`：** 模式 5。更广泛的健康检查——截止日期卫生、注册空缺、§8 即将到期标记上的商业使用问题、所有者不一致、到期时间线、未监控的标记。

7. **如果登记册为空且 IP 管理系统已连接：** 提供模式 1——从记录系统中拉取投资组合并初始化登记册。

8. **护栏提醒：** 计算出的截止日期仅供参考。每个输出结尾都有一行，要求在申请或支付之前根据 USPTO TSDR、WIPO 或相关登记处进行核实。已录入但错误的截止日期会产生虚假信心；不要让用户将此视为记录系统，除非 IP 管理系统已同步集成。

## 示例

```
/ip-legal:portfolio
```

```
/ip-legal:portfolio --report --days 180
```

```
/ip-legal:portfolio --add
```

```
/ip-legal:portfolio --update
```

```
/ip-legal:portfolio --audit
```

---

## 连接后效果更好

此 skill 根据你告诉它的内容追踪截止日期。连接以下内容后效果会好得多：

- **通过 MCP 连接的 IP 管理系统 (IPMS)** — Anaqua、Clarivate IPfolio、AppColl、Patrix、Alt Legal、FoundationIP。已连接的 IPMS 可以在一个地方提供完整的档案、维护费用时间表和来往通信，而不是让登记册依赖律师记得粘贴的内容。询问你的 IPMS 供应商是否有 MCP 连接器，或参见仓库根目录中的 `CONNECTORS.md` 了解如何添加。
- **通过客户编号直接连接 USPTO** — 一次性拉取你整个投资组合的状态、截止日期和通信，而不是一次一个申请。目前不作为 MCP 提供；在 `CONNECTORS.md` 的愿望清单中。

如果两者都没有，粘贴你的档案或上传电子表格，我将从那里开始追踪。

## 目的

未按时续期的商标注册可能被撤销。未支付维护费的专利会失效。过期的域名可能在一小时内被抢注。所有这些都是可以避免的，而且都取决于一件事：正确的截止日期在某人的日历上，与正确的注册编号相关联，在正确的司法管辖区。

此 skill 维护该日历。

## 重要：截止日期参考说明

> 此 skill 应用的截止日期规则反映了 skill 构建日期时公开可用的要求。IP 局要求、宽限期、费用结构和维护时间表会发生变化。**在采取行动之前，始终根据 USPTO TSDR / Patent Center、WIPO Madrid Monitor / Patentscope、EUIPO eSearch、UKIPO 在线记录或相关国家登记处确认计算出的截止日期。** 如果你使用 Anaqua、CPA Global、Clarivate、Alt Legal 或其他 IP 管理系统，其档案对你的资产具有权威性——使用此追踪器来组织和呈现其数据，而不是替代它。
>
> 已录入但错误的截止日期比未录入的截止日期更糟糕：它会产生虚假信心。"近期没有截止日期"的输出在你依赖它之前尤其值得再看一眼。

## 司法管辖区和类型假设

维护机制因司法管辖区和资产类型而异：

- **美国商标：** 在注册 5 至 6 周年期间提交 §8 使用声明（马德里指定为 §71），然后在 10 年时及其后每 10 年提交合并的 §8/§9 续期。§15 不可抗性在连续使用 5 年后可获得。§8 和 §9 有 6 个月附加费宽限期；但使用本身没有宽限期。
- **马德里国际商标：** 10 年注册期，在 WIPO 续期；各被指定国可能有当地使用或申报要求（例如美国 §71）。
- **EUIPO 商标：** 10 年续期；6 个月附加费宽限期。
- **美国实用新型专利：** 维护费在授权后 3.5、7.5 和 11.5 年到期。附加费宽限窗口为 6 个月；之后，如果失效是无意的，可以通过请愿申请复活。
- **美国外观设计专利：** 无维护费——2015 年 5 月 13 日或之后提交的申请为 15 年期限（之前为 14 年）。期间无需采取任何行动。
- **EPO / 国家专利：** 年金通常从申请日或进入国家阶段每年到期。国家规则各异——请按司法管辖区确认。
- **美国版权：** 1978 年或之后创作的作品无需维护。1978 年前的作品可能有续期义务；如果资产早于 1964 年，请标记供律师审查（在现代投资组合中很少见）。
- **域名：** 按注册商每年或多年续期；通常 30 天宽限期，然后是赎回期（约 30 天，费用高），然后是丢弃。

如果投资组合包含上面未列出的司法管辖区的资产，请在登记册的 `custom_rules` 块中捕获维护机制，报告将以 `agent_managed` 方式显示——请与外国代理人确认状态，而不是计算此 skill 不了解的日期。

---

## 登记册

位于 `~/.prismclaw/plugins/legal-chineselaw/ip-legal/portfolio.yaml`。结构：

```yaml
# IP 投资组合登记册
# 生成日期：[date]
# 最后更新：[date]
# 免责声明：计算出的截止日期仅供参考——在采取行动之前，请与 USPTO/WIPO/
# 相关登记处或记录在案的 IP 管理系统确认。

metadata:
  company: "[Company Name]"
  generated: "[date]"
  last_updated: "[date]"
  last_audit: "[date or null]"
  source_system: "[Anaqua / CPA Global / manual / none]"

custom_rules:   # 手动捕获的非内置司法管辖区
  []

assets:
  - id: "TM-US-001"
    type: "trademark"                          # trademark / patent / copyright / design / domain
    jurisdiction: "US"
    mark_or_title: "[Mark or title]"
    owner: "[Record owner — registered entity name]"
    status: "registered"                       # pending / registered / lapsed / abandoned / cancelled
    application_number: "[number or null]"
    registration_number: "[number or null]"
    classes: ["9", "42"]                       # TM 的 Nice 分类；专利的 CPC/IPC；否则为 null
    filing_date: "[YYYY-MM-DD or null]"
    registration_date: "[YYYY-MM-DD or null]"
    priority_date: "[YYYY-MM-DD or null]"
    grant_date: "[YYYY-MM-DD or null]"         # 专利
    next_deadlines:                            # 计算值；在 --report 和 --audit 时刷新
      - type: "§8 Declaration of Use"
        due_date: "[YYYY-MM-DD]"
        grace_end: "[YYYY-MM-DD or null]"
        basis: "5th-6th anniversary of registration"
        action: "File §8 Declaration of Use (or excusable nonuse)"
        status: "upcoming"                     # upcoming / due_soon / overdue / grace / filed
    use_in_commerce: true                      # 仅 TM——驱动 §8 分析
    agent_managed: false                       # 外国代理人/外部律师管理时为 true
    local_agent: null
    docket_id: "[IP 管理系统 ID 或 null]"
    outside_counsel: "[律所或 null]"
    business_owner: "[email 或团队]"
    notes: ""

  - id: "PAT-US-001"
    type: "patent"
    jurisdiction: "US"
    mark_or_title: "[Invention title]"
    owner: "[Owner]"
    status: "granted"
    application_number: "[number]"
    registration_number: "[patent number]"
    filing_date: "[YYYY-MM-DD]"
    grant_date: "[YYYY-MM-DD]"
    priority_date: "[YYYY-MM-DD or null]"
    expiration_date: "[YYYY-MM-DD]"            # 自最早非临时申请日起 20 年
    next_deadlines:
      - type: "3.5-year maintenance fee"
        due_date: "[YYYY-MM-DD]"
        grace_end: "[YYYY-MM-DD]"
        basis: "3.5 years from grant"
        action: "Pay maintenance fee (small/micro entity if applicable)"
        status: "upcoming"
    claims_count: 20
    entity_size: "large"                       # large / small / micro（影响 USPTO 费用）
    docket_id: null
    outside_counsel: null
    business_owner: null
    notes: ""
```

`next_deadlines` 的状态值：
- `upcoming` — 超过 90 天
- `due_soon` — 90 天内到期，尚未申请
- `overdue` — 超过主要到期日，在宽限窗口内（如有）
- `grace` — 在宽限期内（明确标记——产生附加费）
- `lapsed` — 超过宽限期且无行动；资产实际上已丧失，除非可复活
- `filed` — 本周期行动已完成

---

## 模式 1：初始化

在没有登记册时运行，或使用 `--rebuild`。

### 第 1 步：确定来源

读取 `~/.prismclaw/plugins/legal-chineselaw/ip-legal/PRISMCLAW.md`：
- **IP 管理系统已连接**（Anaqua、CPA Global 等）：通过其集成拉取投资组合。IP 系统是权威来源；此登记册镜像它，不添加系统没有的截止日期。
- **无 IP 管理系统，但有电子表格/导出文件：** 请用户分享导出文件。导入现有内容；将缺少注册或授权日期的任何资产标记为 `unknown`，用于截止日期计算。
- **没有任何内容：** 交互式逐步处理资产——类型、司法管辖区、编号、关键日期、所有者。

### 第 2 步：为每个资产计算截止日期

应用本文件顶部的规则。用两三个最近即将到期的项目填充 `next_deadlines`——更远的截止日期（几十年后的 10 年续期）在报告期间按需计算，而不是推测性地存储。

**对于 skill 无法确信安排的资产：**
- 未知的司法管辖区规则 → 在 `custom_rules` 下添加存根，并将该资产标记为 `agent_managed: true`，附带待与外国代理人确认的 TODO。
- 计算所需的日期缺失（专利无授权日期、TM 无注册日期）→ 将 `next_deadlines` 设为空并在 `notes` 中添加说明，并在初始化摘要中将该资产列为 `unknown`。

### 第 3 步：写入登记册

在配置路径生成 `portfolio.yaml`。显示摘要：

```
投资组合登记册已初始化。

资产：[N]
  商标：[N]   ([N 已注册] / [N 待审中])
  专利：[N]   ([N 已授权] / [N 待审中])
  版权：[N]
  外观设计：[N]
  域名：[N]

已计算截止日期：[N]
代理管理/待确认司法管辖区：[N] — 请与外国代理人确认
未知（缺少关键日期）：[N] — 在依赖报告之前填写

运行 /ip-legal:portfolio --report 查看即将到期的项目。
```

---

## 模式 2：报告

```
/ip-legal:portfolio --report [--days 30|60|90|180]
```

默认窗口：90 天。在生成报告之前刷新每个资产的计算截止日期——不要仅依赖存储的日期。

输出（按 `~/.prismclaw/plugins/legal-chineselaw/ip-legal/PRISMCLAW.md` → 输出 在前面添加工作产品标题）：

```
IP 投资组合截止日期报告 — [date]
[公司名称] — 窗口：接下来 [N] 天

🔴 已失效 / 宽限期内 ([N])
  [资产 ID] / [司法管辖区] / [类型] / [标记或标题]
    [行动] — 原始到期日 [date]，宽限期结束 [date]
    状态：[grace / lapsed]

⏰ [N] 天内到期 ([N])
  [资产 ID] / [司法管辖区] / [类型] / [标记或标题]
    [行动] — 到期日 [date]
    依据：[例如，"注册 5 至 6 周年"]
    [代理人：律所 / 档案：id — 如有]

🟡 即将到期（超过 30 天，在 [N] 天内）
  [列表]

🌐 代理管理 ([N])
  [资产 ID] / [司法管辖区] — 由 [本地代理人] 管理；请直接确认
  [资产 ID] / [司法管辖区] — 无本地代理人记录；使用 --update 添加

❓ 未知 ([N])
  [资产 ID] — 缺少 [字段]；无法计算截止日期
  在依赖此报告之前，请通过 [IP 管理系统 / USPTO TSDR / 相关登记处] 确认。

摘要
  追踪的总资产数：[N]
  窗口内的截止日期：[N]
  上次审计：[date]
```

以说明行结尾：*"从投资组合登记册计算。在申请或支付之前，请根据 USPTO/WIPO/记录登记处核实每个截止日期。"*

如果报告列出超过约 10 个资产，或用户随时要求：提供仪表板（见 PRISMCLAW.md `## 输出 → 数据密集型输出的仪表板提议`）。为此输出定制提议——按注册状态计数（有效 / 宽限期内 / 已失效 / 待审中）、截止日期时间线，以及带司法管辖区、类型和下一步行动日期的可排序投资组合表格。

---

## 模式 3：添加

```
/ip-legal:portfolio --add
```

交互式添加单个资产。询问：
1. 类型（商标 / 专利 / 版权 / 外观设计 / 域名）
2. 司法管辖区
3. 标记或标题 / 发明名称
4. 所有者（记录所有者——对 §8 申请和转让很重要）
5. 关键日期（按类型：申请、注册、授权、优先权、到期）
6. 编号
7. 分类 / 权利要求数量
8. 来源——这是否在 IP 管理系统中以档案 ID 追踪？
9. 外部律师 / 外国代理人（如有）
10. 业务负责人（这对谁重要——产品线、品牌经理）

捕获后：
- 按本文件顶部的规则计算下一个截止日期。
- 如果司法管辖区规则未内置，走 `custom_rules` 捕获流程（见下文）。
- 追加到 `portfolio.yaml` 中的 `assets:`。

### 自定义规则捕获

当司法管辖区不在内置列表中时：

> 我没有内置 [司法管辖区] / [资产类型] 的维护规则。让我捕获它们，以便我们今后可以追踪这个。
>
> 1. 适用哪些维护事件？（每 N 年续期？每年年金？使用声明？其他？）
> 2. 是什么触发了到期日——申请日、注册日、授权日、国家阶段进入日、还是其他周年纪念日？
> 3. 是否有宽限期？费用是多少？
> 4. 是否有外国代理人或本地代理人管理这个？

存储在 `custom_rules:` 下，并应用于该司法管辖区的未来资产。

---

## 模式 4：更新

```
/ip-legal:portfolio --update
```

### 重要行动门控

**在记录维护申请或费用支付已完成之前：** 读取 `~/.prismclaw/plugins/legal-chineselaw/ip-legal/PRISMCLAW.md` 中的 `## 谁在使用这个`。如果角色是**非律师**：

> 将 §8 声明、§9 续期、专利维护费支付或国际年金记录为"已申请"会产生后果。如果记录错误——错过了到期日、实体规模错误、使用样本错误——截止日期不会移动，资产仍然可能失效。你是否已与实际完成申请的律师或外国代理人（或 USPTO TSDR / WIPO Madrid Monitor / 相关登记处）确认了这一点？如果是，继续。如果否：
>
> - 暂时不要记录为已申请。
> - 以下是带给律师的内容：资产 ID、司法管辖区、截止日期类型、IP 管理系统显示的内容、你认为已申请的内容和时间，以及该信念的来源。
>
> 如果你需要在你的司法管辖区找到有执照的律师、事务律师、大律师或其他授权法律专业人士：你的专业监管机构的推荐服务是最快的起点（美国的州律协，英格兰和威尔士的 SRA/Bar Standards Board，苏格兰/NI/爱尔兰/加拿大/澳大利亚的 Law Society，或你所在司法管辖区的同等机构）。

在没有明确的"是"的情况下，不要将截止日期的 `status` 设置为 `filed` 通过此门控。状态刷新、报告生成和即将到期的截止日期浮出不需要门控。

### 子模式

**手动更新：** "我们于 3 月 4 日为 TM-US-001 提交了 §8，附上了使用样本。" 更新匹配的截止日期：`status: filed`、`filed_date`，并计算其生命周期中的下一个截止日期（§8 的是 10 年后的 §9 续期）。

**从 IP 管理系统同步：** 如果 Anaqua / CPA Global / 类似系统已连接，拉取最新档案并进行核对。标记登记册和记录系统之间的不匹配——记录系统获胜；更新登记册以匹配，并浮出登记册中存在但系统中没有的任何内容。

**状态更改：** "将 TM-US-004 标记为已放弃。" 更新 `status`，清除 `next_deadlines`，记录放弃日期。

---

## 模式 5：审计

```
/ip-legal:portfolio --audit
```

超出本月截止日期的更广泛健康检查：

**截止日期卫生**
- 现在是否有任何截止日期处于 `grace` 状态？（正在进行但正在产生附加费。）
- 是否有任何 `lapsed` 资产未标记为 `abandoned` 或 `cancelled`？要么复活，要么更新状态。
- 是否有任何资产没有计算的 `next_deadlines`？要么数据缺失，要么是 skill 不了解的司法管辖区。

**注册空缺**
- 提交超过 18 个月的商标申请仍处于 `pending`？标记以在局处进行状态检查——可能需要回复某个动作。
- 提交超过 4 年的专利仍处于 `pending`？标记以进行申请跟进检查。

**商业使用（仅 TM）**
- §8 即将到期，但标记为 `use_in_commerce: false` 或不确定？§8 需要使用；在申请之前，标记需要进行使用审计，或者需要可免除的不使用声明。

**所有权卫生**
- 是否有任何资产的 `owner` 不是当前活跃实体（如果实体登记册可用）？标记——可能需要记录转让。
- 跨资产的所有者名称不一致（同一实体，不同名称字符串）？浮出以清理。

**到期时间线**
- 是否有专利在未来 24 个月内到期？即使没有维护截止日期，企业也可能想了解——产品规划、延续策略、许可窗口。

**未监控的资产**
- 是否有已注册的标记不在 PRISMCLAW.md → 品牌保护 的监控列表中？标记为律师决定是否添加的空缺。

输出格式：

```
IP 投资组合审计 — [date]

截止日期卫生
  宽限期内：[N] — 现在采取行动可避免失效
  已失效（未标记为已放弃）：[N] — 确认状态
  缺少下一个截止日期计算：[N] — 填写数据或标记为代理管理

注册空缺
  待审中超过 18 个月的 TM 申请：[列表]
  待审中超过 4 年的专利申请：[列表]

商业使用（TM）
  §8 即将到期的不确定使用标记：[列表]

所有权
  所有者字符串未识别的资产：[N]
  所有者名称不一致：[列表]

到期时间线（24 个月）
  即将到期的专利：[列表]

品牌监控
  不在监控列表中的已注册标记：[列表]

建议行动
  1. [最高优先级]
  2. [等]
```

---

## 集成：ip-renewal-watcher agent

此 plugin 中的 `ip-renewal-watcher` agent 按计划（默认每周）运行此 skill，并将模式 2 报告发布到 PRISMCLAW.md → 续期提醒中命名的频道。如果出现 🔴 项目（宽限期内 / 已失效），agent 会立即发布，不管计划如何。

## 交接

- 接收：来自申请 skill 的新资产记录（当申请提交或标记通过清查时），来自清查 skill 的（当标记被采用且申请排队时），以及来自转让记录的。
- 发送：向律师发出"现在提交 §8"触发——此 skill 不提交任何内容；它告诉律师截止日期和需要准备什么。

## 此 skill 不做的事

- 它不提交任何内容。它浮出的每个行动都由律师或外国代理人执行。
- 它不根据 USPTO TSDR、WIPO 或任何其他登记处验证截止日期。它从你提供的日期计算它们。登记册是工作副本；登记处是真实来源。
- 它不决定是否续期。续期是一个商业决策——标记是否仍在使用、专利是否仍有价值、域名是否仍重要。此 skill 浮出截止日期和成本；企业和律师来决定。
- 它不替代大型投资组合（数百个资产）的 IP 管理系统。Anaqua、CPA Global、Clarivate、Alt Legal 和类似系统具有直接的登记处数据、截止日期自动化和年金支付服务。此 skill 最适合较小的投资组合，或作为浮出记录系统所显示内容的轻量层。
- 它不读取局记录来验证状态。这里显示为"已申请"的 §8 意味着有人告诉它了——而不是 USPTO 已接受它。通过 TSDR 或 IP 管理系统确认接受。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
