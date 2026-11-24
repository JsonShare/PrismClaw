# PrismClaw 上下文压缩指令 · 现状分析与设计策略

> 文档类型：调研 + 设计建议（不改生产代码）
> 日期：2026-11-24
> 调研方法：逐文件读取 `backend/src/prismclaw/engine/compression/` 真实源码、配置定义、契约测试；对照蚂蚁阿福医生版 v1.0.232 内置 `dsh-compaction-basic` 的压缩 prompt（已从安装包逆向提取）。
> 适用范围：PrismClaw 企业级私有化多 Agent 基座（医疗 / 法律 / 办公 / 科研四类场景共享同一套压缩系统）。

---

## 0. 结论速览

1. **PrismClaw 的压缩系统比蚂蚁阿福那版成熟得多。** 蚂蚁是「单段指令 + 8 段固定结构 + KV cache 复用」的轻量方案；PrismClaw 已做到「LLM 摘要 + 分块 staging + 确定性 carry-forward + PreservationContract 语义保真 + circuit breaker + 预测性触发 + 三级预算编排」。本次对照的目的不是"补缺失"，而是**找出 prompt 文本层还能改进的点**。

2. **现有 prompt 已经很讲究**，有 4 个蚂蚁版没有的设计：`<analysis>` 私下推理块、禁止工具调用的前置注入（user message，不扰动 system prefix-cache）、第 10 段「用户陈述的事实与约束」逐字继承、第 11 段「已失效/作废需求」防止旧需求复活。这两块恰好对应蚂蚁版的"Critical Context"和"滚动合并"，但 PrismClaw 做得更硬——用代码确定性前传兜底，不依赖模型自觉。

3. **真正值得改的只有 3 点**（详见 §3）：
   - 章节标题是编程助手口吻（"关键技术概念""关键文件与产出"），与四类业务场景不匹配；
   - "用户的所有消息"整段复制语义模糊，既烧 token 又易与第 10 段重复；
   - 缺少「事实层级」显式约束——记录原文 / 助手推断 / 用户确认未分开，医疗与法律场景有确定性漂移风险。

4. **不可动的硬契约**（改 prompt 必须同时满足）：第 7 段编号不变、作废 marker 必须用英文、11 段编号连续、`<analysis>` 块不承载需保留信息、标识符逐字保留、禁止工具前置必须待在 user message。

---

## 1. 现状盘点（基于真实代码）

### 1.1 压缩 prompt 位置与加载链路

| 项 | 实际位置 | 说明 |
|---|---|---|
| prompt 文件（单一真相源） | `backend/src/prismclaw/engine/compression/prompts/compact_summary.md` | 2251 B，HTML 注释头是运维说明，会被剥离 |
| 代码内默认模板 | `backend/src/prismclaw/schema/config/config.py:125-150` `COMPACT_SUMMARY_DEFAULT_TEMPLATE` | **实测已与文件漂移**：代码版 931 字，文件版 544 字，代码版多出 W8/W12 两段约束；文件缺失时回退，避免热路径磁盘 IO（详见 §8） |
| 模板解析器 | `engine/compression/_compact_prompt.py:187-226` `_resolve_prompt_template()` | 三种输入：`None` → 读文件；多行 → 内联；单行 → 当路径 |
| 禁止工具前置 | `_compact_prompt.py:38-50` `NO_TOOLS_PREAMBLE` + `inject_no_tools_preamble()` | 注入到 **user message**（注释明确：避免扰动 system prefix-cache 键） |
| `<analysis>` 剥离 | `_compact_prompt.py:254-282` `strip_analysis_block()` | 未闭合标签也整段丢弃（SDD W5 防御） |
| 轻量结果摘要 | `prompts/per_result_summary.md` + `_result_llm_summary.py` | 单条工具结果三段式：目标 / 关键发现 / 下一步 |
| 预算告警 prompt | `prompts/budget_warning.md` | — |
| 配置项 | `schema/config/config.py` `CompactPromptConfig` | `template` / `strip_analysis_block`(默认 True) / `enabled`(默认 True) |

调用链（真实）：

```
LLM 调用前
  → history_compaction.maybe_pretrim_history() / try_compaction()
  → HistoryCompactor.compact()
      1. dedupe_duplicate_user_messages()
      2. build_compaction() 定 cut_point
      3. strip_tool_result_details() / filter_oversized_messages()
      4. inject_no_tools_preamble()          ← user message 前置
      5. SummaryEngine.summarize_messages()  ← resolve_compact_prompt() 取模板
      6. strip_analysis_block()              ← 丢弃 <analysis>
      7. extract_identifiers() + PreservationContract / SemanticCapsule
      8. validate_semantic_capsule()
      9. carry_forward_missing_sections()    ← 确定性兜底
  → artifact: history_compaction_summary
  → assemble_coordinator._history_compaction_block() 注入下回合上下文
```

### 1.2 现有 prompt 的 11 段结构

```
<analysis>   私下推理：按时间顺序逐条分析、留意纠正与意图变化、找出标识符
<summary>
 1. 主要请求与意图
 2. 关键技术概念
 3. 关键文件与产出
 4. 错误与修复
 5. 问题解决过程
 6. 用户的所有消息
 7. 待办任务                    ← carry-forward 强制前传
 8. 当前工作
 9. 可选的下一步
10. 用户陈述的事实与约束        ← 逐字保留，必须完整继承
11. 已失效/作废的需求           ← 作废事实 → 替代内容，必须完整继承
</summary>
逐字保留标识符：UUID、哈希、ID、主机名、IP、端口、URL、文件名、路径、模型名、会话 ID、提交 SHA、错误码
```

### 1.3 代码层硬约束（改 prompt 前必须知道）

| 约束 | 实现位置 | 后果 |
|---|---|---|
| 第 7 段编号锁定 | `hardening.py:231` `_CARRY_FORWARD_SECTION_NUMBERS = frozenset({7})` | 改编号 → 待办在多轮压缩中丢失 |
| 作废 marker 必须英文 | `hardening.py:233` `_VOIDED_MARKERS = {"voided","superseded","cancelled","retracted","replaced"}` | 只写中文"已作废" → W12 前传失效，旧需求复活 |
| section 头格式 | `hardening.py:219-221` `^\s*\*{0,2}\s*(\d+)\.\s+(.+?)\s*\*{0,2}\s*$` | 必须 `数字. 标题`，可带 `**` 加粗 |
| 空段判定 | `hardening.py:266-282` `_section_is_substantive()` | `none / n/a / nothing / - none` 及 `no pending/current/active/outstanding` 前缀视为空 |
| analysis 不入库 | `_compact_prompt.py:254-282` | 把要保留的信息写进 `<analysis>` = 丢失 |
| 语义保真 | `semantic_fidelity.py` `build_preservation_contract()` / `validate_semantic_capsule()` | 标识符与 state 不变量逐项校验，缺失记 warning |
| 触发阈值 | `orchestrator.py` 预测 75% / 紧急 90% / 被动 100%；`history_compaction.py` cooldown | prompt 再好也改变不了触发时机 |

---

## 2. 与蚂蚁阿福压缩 prompt 的逐项对照

蚂蚁版原文来自 `dsh-compaction-basic/lib/index.js` 的 `COMPACTION_INSTRUCTION` + `CHECKPOINT_PREAMBLE`，8 段结构：Primary Request / Key Technical Concepts / Files and Code / Errors and Fixes / Pending Jobs / Current Work / Next Step / Critical Context。

| 维度 | 蚂蚁阿福（DSH Harness） | PrismClaw | 判断 |
|---|---|---|---|
| 注入位置 | 摘要指令作为**最后一条 user message** 追加在重放前缀后，复用供应商 KV cache | 模板作为 summarizer system prompt；禁止工具前置在 user message | **蚂蚁更优**：整个压缩请求都是真前缀，缓存命中更彻底。但 PrismClaw 的 system prompt 是当前 persona，改动涉及 prefix-cache 键，属于工程改动而非 prompt 改动 |
| 推理块 | 无 | `<analysis>` 私下推理，输出后剥离 | **PrismClaw 更优**：给模型思考空间而不污染存储 |
| 禁止工具 | description 里写"do not call any tool" | 独立 `NO_TOOLS_PREAMBLE` 注入 user message + 工具调用拒绝指标 | **PrismClaw 更优**：专门防御 Sonnet 4.6+ 自适应思考 |
| 事实继承 | 已有 `<compacted-summary>` 时"保留真事实、删过期、合并新信息"——靠模型自觉 | 第 10/11 段要求逐字继承 + `carry_forward_missing_sections()` 代码确定性前传 | **PrismClaw 更优**：不依赖模型 |
| 作废需求 | 无独立章节 | 第 11 段 + 英文 marker + W12 兜底 | **PrismClaw 独有**，且是真实痛点 |
| 章节领域适配 | 编程助手口吻（Files and Code / Errors and Fixes） | 同样编程助手口吻（关键技术概念 / 关键文件与产出） | **双方同病**：都不适配 PrismClaw 的医疗/法律/办公/科研四类场景 |
| 事实层级 | 要求"区分事实、推断与外部证据"分散在 persona，压缩 prompt 无此约束 | 无 | **双方缺失**：这是本次建议的核心改进 |
| 空段处理 | 明确写 `(none)`，不得删章节 | 未明确，靠代码空段判定兜底 | 蚂蚁表述更清楚，可借鉴 |
| 结构化程度 | 8 段短小、bullet 优先 | 11 段 + analysis + 标识符块，更长 | 蚂蚁更省 token；PrismClaw 更全 |

**一句话**：PrismClaw 在工程机制上全面领先，蚂蚁版唯一值得借鉴的是「注入位置复用 KV cache」和「空段写 (none)、不删章节」的显式表述。本次策略不追求照搬，而是取这两点 + 补事实层级。

---

## 3. 改进方案（5 项，按优先级）

### 3.1 P1 · 更新章节标题为领域中立措辞

**问题**：第 2 段「关键技术概念」、第 3 段「关键文件与产出」是编程助手口吻。PrismClaw 实际负载是病历整理、合同审查、文献调研、会议纪要——这些任务的"关键概念"不是技术栈，"产出"也不一定是文件。模型看到编程导向标题会倾向按代码任务剪裁事实。

**方案**：只改标题文字，**编号与顺序完全不动**（守住 §1.3 契约）：

```
1. 主要请求与意图                    （不变）
2. 关键领域概念与术语                （原：关键技术概念）
3. 关键资料、文件与产出              （原：关键文件与产出）
4. 错误与修复                        （不变）
5. 问题解决过程                      （不变）
6. 用户的所有消息                    （见 3.2）
7. 待办任务                          （不变，编号锁定）
8. 当前工作                          （不变）
9. 可选的下一步                      （不变）
10. 用户陈述的事实与约束             （不变）
11. 已失效/作废的需求                （不变，英文 marker 不变）
```

**代价**：`COMPACT_SUMMARY_DEFAULT_TEMPLATE` 与 `compact_summary.md` 两处必须同步改（R4 单份化原则：文件是真相源，代码是回退）。但**两处现已漂移**（见 §8），所以灰度时必须明确走哪条加载路径——`history.prompt.template=None` 走文件、`template` 指向文件也走文件，只有文件缺失才落回代码版。旧会话历史里按旧标题生成的摘要仍能被 `extract_sections()` 按编号解析，无迁移问题。

### 3.2 P1 · 第 6 段改为「用户关键原话」并明确与第 10 段分工

**问题**：「用户的所有消息」字面意思是全量复制，但摘要本身有 `target_chars` 预算，模型只能截断——截断规则不明，容易随缘丢失关键纠正。且它与第 10 段「用户陈述的事实与约束」职责重叠：前者说"所有消息"，后者说"逐字保留"，模型不知道该往哪放。

**方案**：第 6 段改标题为「用户关键原话（按时间顺序）」，并在模板里补一句分工规则：

```
6. 用户关键原话（按时间顺序）
   ...
规则补充：
- 第 6 段只收录改变任务目标、要求、纠正或偏好的原话；寒暄、重复确认、已被后续纠正的旧表述不收录。
- 第 10 段收录仍需作为约束继续生效的事实与约束，逐字保留；第 6 段与第 10 段不得互相替代——
  前者是决策证据链，后者是后续必须遵守的约束。
```

**收益**：减少重复与 token 浪费，同时让"用户原话"的截取有明确判据。

### 3.3 P1 · 新增「事实层级」约束（医疗 / 法律场景刚需）

**问题**：现有 prompt 要求逐字保留事实与标识符，但没要求区分**信息来源层级**。PrismClaw 四类场景里，病历报告原文、助手推断、用户口头确认三者的确定性完全不同。压缩时若被抹平，后续 Agent 会把"疑似"当"确诊"、把"助手建议"当"用户已决定"——医疗与法律场景这是实质风险。

**方案**：不新增章节（避免破坏 11 段契约），而是在第 10 段内部加写法约束：

```
10. 用户陈述的事实与约束（逐字保留；必须从既有摘要中完整继承，任何一条都不得丢失）
    - 每条事实标注来源层级：[资料原文] / [助手推断] / [用户确认]
    - 限定词必须原样保留：疑似、待排、未见记载、建议线下确认、仅供参考 等不得删改
    - 否定词必须原样保留：无、未、阴性、停用、不涉及 等不得删改
```

**为何放在第 10 段**：它是「必须完整继承」的段落，层级标注会随 carry-forward 一起被保留，多轮压缩不退化。放 `<analysis>` 会被剥离，放新章节要改解析器——都不合适。

### 3.4 P2 · 借鉴蚂蚁版：显式空段声明

在 `<summary>` 后补一句：

```
每个章节必须出现；确无内容时该章节正文写「（无）」，不得删除章节标题。
```

> **更正（PR0 落地后）**：上文「不影响 carry-forward 判断」的结论**已被证伪**。新 prompt 同时强制「每个章节必须出现」与「空段写（无）」，使 `carry_forward_missing_sections` 的缺段路径与空段路径同时失效，prior 摘要第 7 段待办会静默丢失（W8/W12）。已在 PR0 step 10 修复：空段声明改为与 prompt 契约一致的中英双语识别，并落盘 4 个跨文件联动守卫测试。详见《PrismClaw压缩Prompt组装重构方案》§8.6。

### 3.5 P3 · 不采纳项：改注入位置复用 KV cache

蚂蚁版把摘要指令放在最后一条 user message，整个压缩请求是真前缀。PrismClaw 若照搬，需要把 summarizer system prompt 改成"重放当前会话的完整 system + tools + 历史"，并承担 system prompt 变更对 prefix-cache 键的影响。这属于**运行时改动**，收益是压缩调用的缓存命中，风险是扰动主链路缓存。建议先用指标验证：对比 `compact.prompt_tokens` 与实际缓存命中率，确认收益后再决定是否动工程。

---

## 4. 建议的 prompt 全文（可直接替换 `compact_summary.md`）

保留全部硬契约：11 段编号、第 7 段锁定、英文作废 marker、`<analysis>` 结构、标识符保留块、禁止工具前言（由代码注入，不在文件内）。

```markdown
# 关键：禁止使用任何工具。只输出文本。

你的任务是详细总结此前的对话，重点关注用户的明确请求、所做的关键决策和产出。

回答必须分成两部分：先在 <analysis> 块中进行私下推理，然后在 <summary> 块中给出结构化摘要，供另一个 AI 继续执行任务。

<analysis>
1. 按时间顺序逐条分析每条消息。
2. 特别留意用户的具体反馈、纠正和意图变化。
3. 找出所有文件路径、函数名、错误码和具体取值。
4. 识别哪些信息是资料原文、哪些是助手推断、哪些是用户明确确认。
</analysis>

<summary>
1. 主要请求与意图
2. 关键领域概念与术语
3. 关键资料、文件与产出
4. 错误与修复
5. 问题解决过程
6. 用户关键原话（按时间顺序）
7. 待办任务
8. 当前工作
9. 可选的下一步
10. 用户陈述的事实与约束（逐字保留；必须从既有摘要中完整继承，任何一条都不得丢失）
11. 已失效/作废的需求（每条一行：作废事实 → 替代它的内容；必须从既有摘要中完整继承——一旦丢失作废条目，它会在后续工作中被误当作当前事实复活；作废条目标注 voided / superseded / cancelled / retracted / replaced）
</summary>

规则：
- 每个章节必须出现；确无内容时该章节正文写「（无）」，不得删除章节标题。
- 第 6 段只收录改变任务目标、要求、纠正或偏好的原话；寒暄、重复确认、已被后续纠正的旧表述不收录。
- 第 10 段收录仍需作为约束继续生效的事实与约束，逐字保留；第 6 段与第 10 段不得互相替代——前者是决策证据链，后者是后续必须遵守的约束。
- 第 10 段每条事实标注来源层级：[资料原文] / [助手推断] / [用户确认]。
- 限定词与否定词原样保留：疑似、待排、未见记载、建议线下确认、仅供参考、无、未、阴性、停用、不涉及——不得删改，不得提高确定性。
- 在摘要中逐字保留标识符：UUID、哈希、ID、主机名、IP 地址、端口、URL、文件名、文件路径、模型名、会话 ID、提交 SHA、错误码。不得重写、缩写或改写这些标记。
- 用户陈述的事实必须在压缩后继续存在（SDD context-governance W8）：用户处境的每项事实都是持久上下文——数字、日期、截止时间、性能阈值（如 P99 < 500ms）、团队规模、角色与风格偏好、过往事件、业务规则、技术专有名词。每项逐字带入摘要第 10 节。既有摘要中的每项用户事实必须出现在新摘要中——压缩链一旦丢弃某项，它就永久丢失。
- 已取代的需求必须作为作废项保留（SDD context-governance W12）：用户改变主意时（更正、撤回、取消、替换），旧值不是当前上下文，但不能删除。列在第 11 节，每条一行：作废事实 → 替代它的内容（取消时写 → 无）。这是禁止清单：其中内容绝不能再次作为当前事实出现。既有摘要中的作废条目必须逐字继承——丢失会在后续生成中复活。某项事实只出现在第 10 节或第 11 节其中一处，不能同时出现。
```

> 与现行版的差异仅为：第 2/3/6 段标题、`<analysis>` 第 4 条、规则块新增 6 条。编号、marker、标识符块全部不动。末尾两段 W8/W12 来自实测发现的代码内默认模板，已补入以免灰度时丢约束（见 §7）。

---

## 5. 实施建议与验证

| 步骤 | 动作 | 验证 |
|---|---|---|
| 1 | 先只改 `prompts/compact_summary.md`（运维可通过 `history.prompt.template` 指向它，无需改代码即可灰度） | 对比同名配置 inline 与 file 两种加载路径，确认 `_strip_md_frontmatter()` 后正文一致 |
| 2 | 跑契约测试 | `pytest backend/tests/engine/compression/test_history_compaction_contract.py -q`（conda 环境 `pyclaw`） |
| 3 | 跑多轮压缩回归，重点看第 7 段与第 11 段 | 断言 `extract_sections()` 仍能解析 1–11 段；作废条目在下一轮摘要中仍存在 |
| 4 | 确认无收益回退 | 模板路径配置回退到 `None` 即恢复代码内默认模板 |
| 5 | 若决定固化，再同步改 `config.py:125-150` 的 `COMPACT_SUMMARY_DEFAULT_TEMPLATE` | 先修 §8 的既有漂移，再做字节级 diff 确保一致（R4 单份化） |

**不建议**在没有度量的情况下同时调整触发阈值或注入位置——压缩质量与缓存收益是两个独立变量，混改无法归因。

---

## 6. 证据文件清单

```
backend/src/prismclaw/engine/compression/prompts/compact_summary.md       现行 prompt（真相源）
backend/src/prismclaw/engine/compression/prompts/per_result_summary.md    结果摘要 prompt
backend/src/prismclaw/schema/config/config.py:125-150                     代码内默认模板
backend/src/prismclaw/engine/compression/_compact_prompt.py:38-50         禁止工具前置
backend/src/prismclaw/engine/compression/_compact_prompt.py:187-226       模板解析
backend/src/prismclaw/engine/compression/_compact_prompt.py:254-282       analysis 剥离
backend/src/prismclaw/engine/compression/hardening.py:219-221             section 头正则
backend/src/prismclaw/engine/compression/hardening.py:231-233             前传集合 + 作废 marker
backend/src/prismclaw/engine/compression/hardening.py:236-327             section 提取 / 结转
backend/src/prismclaw/engine/compression/history_compaction.py:317-320    前传调用点
backend/src/prismclaw/engine/compression/semantic_fidelity.py             语义保真校验
backend/tests/engine/compression/test_history_compaction_contract.py      契约测试（323 行）
docs/architecture/modules/07-context-compression.md                       架构文档
对照样本：蚂蚁阿福医生版 v1.0.232 安装包内 dsh-compaction-basic/lib/index.js
          （已导出至 research/蚂蚁阿福医生版_完整源码/app.asar.extracted/）
```

---

## 7. 附加审计：prompt 双写的实测漂移

针对"为什么同一 prompt 散落多处"的追问，做了运行时实测（conda `pyclaw`，导入真实模块后比对）。

| 检查项 | 实测结果 | 判断 |
|---|---|---|
| `compact_summary.md` vs `COMPACT_SUMMARY_DEFAULT_TEMPLATE` | 剥离注释头后 **544 字 vs 931 字，不一致** | 真漂移 |
| 差异内容 | 代码版多出两段：W8「用户陈述的事实必须逐字带入第 10 节」、W12「已失效需求必须作为作废项保留」；另有一处 `IP 地址` vs `IP` | 代码版更完整，文件版缺关键约束 |
| `per_result_summary.md` vs `P2_2_PER_RESULT_PROMPT` | 三段式与关键句均一致 | 当前一致，但同为双写，无守卫 |
| `IDENTIFIER_PRESERVATION_INSTRUCTIONS` | `_preservation_rules.py` 定义，`config.py` 与 `hardening.py` 两处引用 | 有单一定义，属合理复用 |
| 段数表述 | 文档/注释写 9 段，实际 11 段，函数名含 `9-section` | 文档漂移 |

**为什么会这样**：`_compact_prompt.py:_resolve_prompt_template()` 的设计是"文件为真相源，代码字面量为回退"，目的是热路径不读盘、文件缺失不崩。这个取舍本身合理，但**没有一致性守卫**——两边各改一次就漂移了，而且现在漂移的方向是"回退版比真相源更完整"，属于最危险的一种：运维按文件覆盖时反而丢约束。

**这是设计问题吗**：分层不是，缺守卫是。压缩系统散落 12 处里，orchestrator / budget / history_compactor / hardening / semantic_fidelity / state_store / prompts / tests 各自职责单一，合并只会制造上帝模块；真正的问题是**同一份 prompt 文本有两个可写副本却无一致性测试**。

**最小修复（按成本排序）**：
1. 加一条测试断言 `strip_md_frontmatter(md文件) == COMPACT_SUMMARY_DEFAULT_TEMPLATE`（现在就会红，先修数据再提交守卫）；
2. 把 W8/W12 两段补回 `compact_summary.md`，统一 `IP` 表述——**以代码版为准**，因为它含关键约束；
3. 文档与注释统一为 11 段，或全部去掉段数表述（函数名 `9-section` 保留无妨，但注释应说明历史名称）；
4. 不要为了"看起来集中"去合并模块。

> §4 的 prompt 全文已把实测发现的 W8/W12 两段补入，可直接作为目标文本；实施时以它同时更新 md 文件与代码内默认模板，并加一致性守卫。

---

## 8. 一句话给你的决策建议

**先做 P1（三个 prompt 文本改动），通过配置替换灰度，不要动代码。** 蚂蚁版在工程机制上没有能直接搬的东西；PrismClaw 的短板只在 prompt 文本的领域适配和事实层级表述——这两处改动零工程风险，且能用现有契约测试兜底。KV cache 复用那个点先记在账上，等有指标再决定。
