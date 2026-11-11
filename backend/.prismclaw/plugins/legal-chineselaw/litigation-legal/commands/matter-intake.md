---
name: matter-intake
description: 受理新事项——涵盖识别、冲突、来源、风险分流、重要性、外部律师、所有者、法律保留和关键日期的统一问题；编写 matter.md 和 history.md
  并向 _log.yaml 附加结构化行。当用户说"new matter"、"intake this matter"或想将新事项带入组合时使用。
argument_hint: '[可选事项名称]'
category: 争议诉讼
---

<!--
This file is a Chinese translation of the original by Anthropic PBC.
Original: https://github.com/anthropics/legal-chineselaw
Licensed under Apache License 2.0
中国法律语境适配：已针对中华人民共和国大陆法律体系进行改造。
案号格式：（YYYY）XX民初/民终/民申XXXX号
"opposing counsel"→"对方代理律师/对方律所"
"court filing"→"立案/庭审/开庭"
仲裁机构：贸仲（CIETAC）、上海国仲、北仲、广仲等
货币：人民币（CNY）
工具集成：Slack→企业协作/钉钉，Google Drive→企业协作云盘，Westlaw→元典/北大法宝/威科先行
-->


# /matter-intake

1. 加载 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` → 风险校准（用于分流）、landscape（用于上下文、冲突方法）、stakeholders（用于通知谁）。
2. 遵循以下工作流和参考。
3. 运行统一 intake：识别、冲突检查、来源、风险分流、重要性、外部律师、内部所有者、证据保全通知、关键日期、初始姿态。
4. 从事项名称生成 slug（小写、连字符、年份）。
5. 创建 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/matters/[slug]/matter.md` — 完整叙述 intake。
6. 创建 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/matters/[slug]/history.md` — 以 intake 为第一个条目进行种子化。
7. 向 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/matters/_log.yaml` 附加结构化行。
8. 与用户确认："这是我将要写的行——有任何编辑吗？"

---

# 事项 Intake

## 目的

每个新事项都经过相同的 intake，以便组合保持可比性。`_log.yaml` 中的统一行让 status skill 可以汇总。`matter.md` 中的叙述捕获了行无法捕获的内容。在此处种子化的历史文件成为事件记录。

## 加载上下文

- `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` — 风险校准（分流阈值、重要性、和解阶梯）、landscape（stakeholders、外部律师名册）。
- `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/matters/_log.yaml` — 确认 slug 唯一性。

## Intake

### 1. 识别

- 事项名称（如通常引用，例如"XX公司诉我公司合同纠纷2026"）
- 对方当事人（对方公司/个人名称）
- 对方代理律师/律所（如已知）
- 事项类型：`contract | employment | ip | regulatory | investigation | product | other`
- 我方角色：`plaintiff（原告）| defendant（被告）| claimant（申请人）| respondent（被申请人）| investigated（被调查方）`
  - 如果执业档案的 `## Side` 是 `plaintiff`、`defense` 或"both — default X"变体，从该默认值预填充角色并确认。如果 `## Side` 是"varies by matter"，冷启动询问。永远不要默默假设执业档案未设定的姿态。
  - 角色驱动下游 skills：原告姿态事项将风险分流路由到案件价值/或有经济学；被告姿态事项路由到敞口/储备/保险 tender。
- **管辖法院（省份+级别）：** 例如"广东省深圳市中级人民法院"、"北京知识产权法院"；或仲裁机构如"贸仲（CIETAC）"、"上海国仲"
- **案号（如已立案）：** 格式为 `（YYYY）XX民初/民终/民申XXXX号`

### 2. 冲突检查

在继续之前，按照 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` → Conflicts clearance 运行冲突步骤。

- **状态：** `cleared | pending | not-run | waived`
- **方法：** 匹配 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` 声明的内容（`corporate-legal | outside-counsel | system-check | informal | other`）。
- **由谁清理：** 名称 / 团队 / 律所
- **清理日期：** YYYY-MM-DD
- **检查对象：** 运行的具体名称/实体的简要列表（对方当事人、已知关联方、对方代理律师（如已知）、关键证人）。薄是可以的；"否"不可以。
- **备注：** 标记但已清除的任何内容。

按状态的行为：

- `cleared` → 继续。
- `pending` → 继续 intake；在 `matter.md` 和日志行中突出标记冲突未解决；在每次 `/matter-update` 和 `/portfolio-status` 中再次浮现，直到解决。
- `waived` → 罕见；需要冲突放弃理由（编写放弃不在本 skill 范围内——捕获放弃存在、谁签署了它、它在哪里）。
- `not-run` → **停止。这是一个关卡。** 在冲突姿态解决之前，skill 不会创建 `matter.md`、`history.md` 或 `_log.yaml` 条目。三个可接受的路径：

  **路径 1 — 现在运行冲突。** 暂停此 intake。按照 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` Conflicts clearance 清理。带着 `status: cleared` 或 `status: waived` 附带理由返回。

  **路径 2 — 标记待定并附上所有者 + 到期日。** 仅当 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` Conflicts clearance 声明并行 intake 可接受时允许。

  **路径 3 — 绕过并附有记录的理由。** 仅当用户明确确认绕过时。在 `conflicts.override` 中记录：

  ```yaml
  conflicts:
    status: not-run               # 保持原样
    override:
      by: [用户名称]
      date: [YYYY-MM-DD]
      rationale: [绕过冲突的原因——永久记录；不会自动过期]
  ```

  **不要默默进行。** "我稍后会做"不是可接受的回答。

此步骤不是关于 skill 决定冲突是否存在——那是用户/律所的判断。它是关于确保检查发生并且记录反映它。

### 3. 来源

这是如何到达的？
- `demand-letter（收到催告函）| complaint-served（起诉状送达）| subpoena（调查令）| regulator-inquiry（监管查询）| internal-report（内部报告）| pre-suit-threat（诉前威胁）`
- *种子文档机会：* "如果你有启动文档（起诉状、催告函、调查令），附加或分享路径。它 sharpen 了 intake。"

### 4. 风险分流——对照内部校准

- 严重性：high | medium | low（参考 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` 严重性带——金额以人民币计）
- 可能性：high | medium | low（参考 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` 可能性带）
- 结果风险评级（按照矩阵）：high | medium | low | critical
- 损害敞口范围（最佳估计，以人民币计）
- 非金钱敞口（禁令？整改令？媒体曝光？先例？）

如果 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` 中的风险校准薄，不要伪造精确度。使用用户的直觉并记录薄度。

### 5. 重要性

对照 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` 中的内部阈值：
- `reserved | disclosed | monitored | none`
- 如果 `reserved`：储备金额以及是否已通知财务
- 如果 `disclosed`（适用A股上市公司）：公告位置（上交所/深交所）

### 6. 外部律师

- 律所名称
- 主要合伙人/律师（及执业证号）
- **主要律师电子邮件/企业协作/钉钉**（由 `/oc-status` 用于起草状态请求）
- 委托协议状态：`signed | pending | none`
- 预算授权：金额（人民币）和批准人
- *种子文档机会：* "委托协议路径（如已签署）。"

如果风险为中等或更高且未分配外部律师——标记它。

### 7. 内部所有者

从 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/PRISMCLAW.md` landscape——涉及哪些内部 stakeholder？
- 业务负责人
- HR 合作伙伴（如劳动争议）
- 通讯/公关联系人（如声誉风险）
- CISO（如数据或网络安全）
- 其他

### 8. 证据保全通知

- 已发布？如果是：日期、范围、保管人（姓名列表）。
- 下次刷新日期（默认：发布后六个月；按事项调整）。
- 如果否且这是活跃诉讼或合理预期：紧急标记；提议在 intake 完成后运行 `/litigation-legal:legal-hold [slug] --issue`。
- *种子文档机会：* "证据保全通知（如已发布）。"

### 9. 关键日期

- **立案日期**（如已立案）
- **答辩截止日期**（通常：送达后15天）
- **举证期限截止日期**
- **庭前会议日期**（如已定）
- **开庭日期**（如已定）
- **诉讼时效截止**（如适用——民法典第188条，普通时效3年）
- 任何监管截止日期

### 10. 初始姿态

一段式理论：
- 我们的故事是什么？
- 他们的故事是什么？
- 转折事实是什么？
- 初始姿态：`fight（坚决应诉/起诉）| settle（寻求和解）| investigate（调查后决定）| wait（观望）`

## 编写输出

### Slug

小写、连字符、年末。示例：`xx-gongsi-hetong-2026`、`laodong-zhangsan-2026`、`zhizhuo-chaoquan-2026`。

在写入前确认 slug 在 `_log.yaml` 中唯一。

### `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/matters/[slug]/matter.md`

```markdown
[工作产品标头——根据 plugin 配置 ## Outputs — 因角色而异；见 `## Who's using this`]

# [事项名称]

**Slug：** [slug]
**开启：** [YYYY-MM-DD]
**我方角色：** [plaintiff（原告）/defendant（被告）/etc.]
**状态：** [status]

---

## 识别

[对方当事人、对方代理律师/律所、管辖法院（省份+级别）、案号（如有）、事项类型、来源]

## 冲突

**状态：** [cleared / pending / not-run / waived]
**方法：** [corporate-legal / outside-counsel / system-check / informal / other]
**由谁清理：** [name]
**清理日期：** [YYYY-MM-DD]
**检查对象：** [运行的实体]
**备注：** [任何标记的清理项、放弃参考（如适用）]

## 风险分流

**严重性：** [band] — [为什么，参考内部严重性定义]
**可能性：** [band] — [为什么]
**风险评级：** [high/medium/low/critical]
**敞口：** [人民币金额范围 + 非金钱]

## 重要性

[reserved/disclosed/monitored/none — 附储备金额、披露位置（适用上市公司），或如为"none"的理由]

## 外部律师

[律所名称、主要律师及执业证号、委托协议状态、预算]

## 内部所有者

[stakeholder 及每个为何涉及]

## 证据保全通知

[状态、日期、范围]

## 关键日期

[列表——含立案日、答辩期、举证期限、开庭日等]

## 初始理论

[一段：我们的故事、他们的故事、转折事实、初始姿态] `[SME VERIFY — intake 处的理论是工作假设；在任何依赖此框架的提交或实质性沟通之前与外部律师确认]`

## 未决问题

[任何尚未知道但重要的事项——例如，"保险 tender 待定"、"委托协议尚未签署"]

---

## 种子文档

| 文档 | 路径/指针 |
|---|---|
| [例如，起诉状] | [路径或"not yet shared"] |
```

### `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/matters/[slug]/history.md`

将历史文件种子化为以 intake 为条目零：

```markdown
# 历史：[事项名称]

仅追加事件日志。最近的最前。

---

## [YYYY-MM-DD] — 事项开启

[来源、谁带入、初始分流摘要、分配的外部律师、证据保全通知发布是/否。]
```

### 附加到 `~/.prismclaw/plugins/legal-chineselaw/litigation-legal/matters/_log.yaml`

按照 schema 添加一行。示例：

```yaml
- id: xx-gongsi-hetong-2026
  name: "XX公司诉我公司合同纠纷"
  type: contract
  role: defendant
  counterparty: "XX科技有限公司"
  counterparty_counsel: "北京某律师事务所 张律师"  # 对方代理律师/律所
  jurisdiction: "广东省深圳市中级人民法院"  # 管辖法院（省份+级别）
  case_number: "（2026）粤03民初XXXX号"  # 案号格式
  # status 从来源派生：
  #   source: pre-suit-threat | demand-letter           → status: threatened
  #   source: complaint-served | subpoena | regulator-inquiry → status: active
  #   source: internal-report                           → status: threatened（默认）或如正式流程已开始则为 active
  status: active
  stage: pleadings  # 阶段：pleadings | discovery | pre-trial | trial | appeal | enforcement
  source: complaint-served
  outside_counsel:
    firm: "XX律师事务所"
    lead: "李律师"
    license_no: "XXXXXXXXXXXXXXXX"  # 律师执业证号
    email: "li@example.com"
    notify: "notify-id"  # 企业协作/钉钉联系方式
    engagement: signed
  conflicts:
    status: cleared
    method: corporate-legal
    cleared_by: "王某"
    cleared_date: 2026-04-20
    override:                   # 仅在路径 3 绕过时填充
      by: null
      date: null
      rationale: null
  risk: high
  materiality: reserved
  exposure_range: "200万元–500万元人民币"  # 以人民币计
  internal_owners:
    business_lead: "张某"
    hr_partner: null
    comms_contact: null
  legal_hold:
    issued: true
    issued_date: 2026-02-15
    scope: "与XX项目相关的2023年至今的全部文件"
    custodians: ["张某", "李某", "王某"]
    last_refresh: 2026-02-15
    next_refresh: 2026-08-15
    released: null
  related_matters: []
  opened: 2026-04-20
  next_deadline: 2026-05-15  # 下次关键截止日期（举证期限/开庭）
  last_updated: 2026-04-20
  path: matters/xx-gongsi-hetong-2026/
```

## 写入前确认

向用户展示行和 matter.md 内容：

> 这是我将要写入的内容。在我提交之前标记任何错误或薄的地方。

## 关闭时使用下一步决策树

根据 PRISMCLAW.md `## Outputs` 以下一步决策树结束。根据此 skill 刚刚生成的自定义选项——五个默认分支（起草 X、升级、获取更多事实、观察等待、其他）是起点，而非锁定。树就是输出；律师选择。

## 此 skill 不做什么

- **自己运行冲突检查。** 它记录结果、状态、方法和检查的实体。实际清理发生在内部执业档案声明的任何系统（或判断）中。如果用户说"已清理"，skill 按面值接受并捕获元数据。
- 决定初始理论。它捕获用户所说的；它不发明一个。
- 发布证据保全通知。如果缺少则标记。用户发布它。


> 注意：本回复由 AI 辅助生成，仅提供一般性参考，不构成法律意见。具体法律问题，请咨询持有执业证的律师或律师事务所。
