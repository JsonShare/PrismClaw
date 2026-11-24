# PrismClaw 压缩 Prompt 组装重构方案 · 架构评审结论

> 文档类型：多专家交叉评审（只读审查，未改生产代码）
> 日期：2026-11-24
> 评审对象：`PrismClaw压缩Prompt组装重构方案.md`
> 评审团：refactor-critic（接口/迁移）、test-quality（测试/回归）、minimalist（过度工程/YAGNI）、domain-auditor（领域/事实准确性）
> 方法：四位专家各自独立读真实代码 + 运行时实测；team-lead 交叉验证去重、裁决分歧。所有结论带代码证据。

---

## 0. 一句话结论

**方案的痛点诊断是对的，但药方开错了规模，且存在 5 个会让实施直接失败的阻断缺陷。**
四方收敛后的建议：**砍掉 6 文件重构，只保留 PR1 内核（模板单一真相源 + 一致性守卫），并按本文 §3 修正 5 个阻断**后再动工。这与方案 §7 自己那句"保守可只做 PR1"形成有趣呼应——评审团用证据把"保守选项"变成了"唯一务实选项"。

---

## 1. 四方共识：必改的阻断项（去重后）

### B1 · 第 3 步改 md 与"字节一致性"守卫直接互斥，测试必红
**四票全中**（refactor-critic #1 / test-quality B2 / minimalist #4 / domain-auditor F6），且四方都做了运行时实测。

- 证据：`compact_summary.md` strip 后 **544 字**，代码版 `COMPACT_SUMMARY_DEFAULT_TEMPLATE`（`config.py:125-150`）**931 字**；`_compact_prompt.py:196-204` 在 `template=None` 时**优先读 md**，生产 `configs/agent.yaml:67` 正是 `template: null` → 线上一直看 544 字版。
- 第 3 步要把 md 改成 931 字 = 改线上 system prompt 正文，字节必变；而 §4 `test_payload_byte_identity_after_refactor` / §5 / §6 又要求"字节一致/不改正文"。
- 修正：拆成两条基线——①"组装机制字节一致"用**第 3 步之后**在旧路径上导出的 golden fixture；②"文本修正"用单独的"md 与规范文本逐字相等 + 允许变更清单"守卫。

### B2 · 删函数/常量打断 re-export 链，契约测试在 collection 阶段就崩
**三票**（test-quality B1 / domain-auditor F2 / refactor-critic #3+#4）。

- 证据：`history_compaction.py:39-67` re-export 了 7 个 `_compact_prompt` 符号；`result_compaction.py:36-41` re-export `_load_per_result_prompt`；`schema/config/__init__.py:9,39` re-export `COMPACT_SUMMARY_DEFAULT_TEMPLATE`。消费方 `summary_engine.py:16-22`、`test_archive_strip.py:15`、`test_material_policy.py:26` 等均从这些中层包导入。
- 按 §3 step 7/8 直接删 → ImportError；方案 §4"契约测试不动断言"、§7"契约测试兜底"、§5"grep 零引用"三条承诺同时击穿。
- 修正：§3 新增一步"先收敛 re-export：调用方改深路径直连 → 再删原符号"；把 `pytest --collect-only` 列为步骤 7/8 的独立验收条件。

### B3 · "一个调用签名"目标未达成，且建立在误诊之上
**三票**（minimalist #3 / refactor-critic #2 / domain-auditor F8）。

- 证据：两条注入链互不相干——history 是 `(payload: list[dict], *, model=None)`（`runner_assembly.py:604-636`），result 是 `(prompt: str)`（`runner_assembly.py:657-676` → `deps.py:385`）。方案自己保留两个 adapter（§2.2），10 步迁移无一步碰 `deps.py` / `runner_assembly.py` 的构造点。
- minimalist 补刀：`str → list[dict] → str` 是三层包装换一行 f-string 的等价物，是抽象税；messages 与 str 是**本质差异**，不是历史误用。
- 修正：目标诚实降级为"**一个组装入口 + 两个边缘适配器**"；不要声称统一了 LLM 边界签名。

### B4 · per-result 路径 frontmatter 会被意外剥离，模型可见字节 520→167
**三票**（test-quality B2 / domain-auditor F7 / refactor-critic #10）。

- 证据：`_result_llm_summary.py:46-58` 的 `_load_per_result_prompt()` 直接 `read_text()`，**不剥离** frontmatter；实测 `per_result_summary.md` 原始 **520 字**（含 HTML 注释头）、剥离后 **167 字**。方案 §2.3 `PromptSource.get()` 统一 `strip_frontmatter`，切换后模型可见内容少 353 字。
- 方案既没说这是有意修 bug 还是意外回归，也没有 result scope 的独立基线。
- 修正：先决定"保留 bug（520）还是修 bug（167）"；若是后者，列为独立的一次"有意的模型可见内容变更"，result scope 单独设 golden，禁止复用 history 的一致性断言。

### B5 · `config=None` / `enabled=False` 的 fallback 在新 spec 中无处安放
**三票**（refactor-critic #5 / domain-auditor F3 / test-quality I4 部分）。

- 证据：`resolve_compact_prompt` 在 `enabled=False` 或 config=None 时返回 None（`_compact_prompt.py:242-253`），`summary_engine.py:96` 回退 `HARDENED_SUMMARIZER_SYSTEM_PROMPT`；`archive.py:105-109` 构造 SummaryEngine **不传 config**（`test_archive_strip.py:61-77` 锁定此行为）。而 `SummarizerSpec` 只有 preamble/strip/model 三个字段，无字段表达"禁用→简短 fallback"。
- 若 builder 一律从 md 装载，archive 路径 system prompt 从 HARDENED 变成 md 模板（正文变化）；且 archive 的宽泛 `except Exception`（`archive.py:113-118`）会吞掉 `PromptNotFoundError`，fail-loud 在这条路径失效。
- 修正：spec 增加 `template_enabled: bool` 或 `system_prompt: str | None` 显式承载 fallback 决策；§5"代码里没有第二份正文"改为"三个 md 是各自 default 的唯一来源，fallback 文本单独标注"。

---

## 2. 重要项（共识度较低但需处理）

| 编号 | 问题 | 证据摘要 | 修正 |
|---|---|---|---|
| I1 | budget_warning scope 三处自相矛盾 | §2.2 纳入 `Literal`、§3 step 2"3 个 md 统一" vs §6"不做"、§5"未被误纳入"；`budget_warning.md` 需 `{ratio:.0%}` 渲染但 spec 无 `template_vars`（refactor-critic #6 / domain-auditor F3） | 从 `Literal` 移除，step 2 改为"装载 2 个 scope"，source docstring 写明归 soft_budget |
| I2 | 运维覆盖映射接口不支持 | `PromptSource.__init__(prompt_dir, inline)` 无 per-scope 路径参数，§6 却声称可映射 `cfg.history.prompt.template`（绝对路径，见 `_compact_prompt.py:238`）（refactor-critic #7 / domain-auditor D3） | 增加 `paths: Mapping[str, Path]`；或明确废弃该覆盖并声明 breaking change |
| I3 | 领域契约未落到 builder 的具体层 | 11 段结构（仅文本，代码不校验总数）、第 7 段前传（`hardening.py:231`）、英文 marker（`:233`）、analysis 剥离（`:143-170`）、标识符文案（`_preservation_rules.py`）与提取逻辑（两套独立实现）均未映射；第 7 段前传发生在 SummaryEngine 返回**之后**（domain-auditor D1/D5） | 增加"契约归属矩阵"：文本契约→source/fragments；生成后处理→builder 下游且保持"最终一次"；制品契约→留在 hardening/semantic_fidelity，builder 不碰 |
| I4 | staging 多块路径的 analysis 剥离时序未定义 | 每块摘要不剥离直接进 merge_payload，最终文本才在 `summary_engine.py:165-166` 剥离一次；§3 step 7"移入 builder 下游"没说每个 stage 还是最终（test-quality I4 / domain-auditor D4） | 明确 strip 只是输出标记，执行点固定在 `summarize_in_stages` 返回后、仅一次；补多块路径断言 |
| I5 | fail-loud 触发点与设计不符 | §6 说"启动失败"，但 `get()` 在 `build_summarizer_request()` 时才读盘；现有降级路径 `summary_engine.py:67-68`、`_result_llm_summary.py:115-118` 会在缺 md 时静默降级（test-quality I2） | 要么增加启动期装配校验，要么把风险描述改为"压缩时抛错并由 circuit breaker 处理" |
| I6 | `prompts/*.md` 随包发布无守卫 | `pyproject.toml:80-86` hatch 配置无 md 的 include 规则；7 个新测试全在源码树跑（test-quality I3） | CI 增加基于已安装包（`importlib.resources.files`）的读取测试 |

---

## 3. 事实纠错（方案描述与真实代码不符）

| 编号 | 方案说法 | 真实情况 |
|---|---|---|
| F1 | "preamble_no_tools 等三个名字都是为兼容不存在的旧配置的胶水"（§1.3-2） | `preamble_no_tools` 是**真实字段**（`config.py:298`，生产 `agent.yaml:61` 为 true）；另两个名字才是虚构的。批评"3×2 遍历是死代码"成立，但"字段不存在"是事实错误，删除时必须保留对真实字段的一次直接读取（domain-auditor F1 / refactor-critic #11 / minimalist #6） |
| F2 | "占位常量随即被覆盖，属无效中间态"（§1.3-4） | 结论对，但 §3 step 8 删 `SUMMARIZER_SYSTEM_PROMPT` 未交代 `build_summarizer_payload`（`compaction.py:172-194`，仍被 `summary_engine.py:8-10,92` 使用）——直接删会 NameError，且函数可能变孤儿（domain-auditor F4） |
| F3 | "调用方签名是确定的"（§1.3-3） | 只对 history 路径成立；result 路径签名由 `deps.py:385` 独立契约决定（domain-auditor F8） |
| F4 | step 9 验证 `grep 9-section` | 真实漂移落点是中文 `9 章节`（`config.py:121,214,296`）、`10-section`（`context_bundle.py:104`）等；按现命令执行后仍有 5 处残留（domain-auditor F5） |
| F5 | 行号引用 | `strip_analysis_block` 定义在 `_compact_prompt.py:143`（方案 §1.3 写 :238）—— 属实。**但"文件行数 283/155"经 team-lead 实测复核有误**：`wc -l` 结果为 282/283/154（`_compact_prompt.py` / `summary_engine.py` / `_result_llm_summary.py`），与原 v1 文档一致。v2 方案 §8 已据此标注"部分采纳 + 实测复核有误"。教训：评审结论属二手信息，数字须实测后才落文档。 |

---

## 4. 分歧裁决

| 分歧 | minimalist | refactor-critic / domain-auditor | 裁决 |
|---|---|---|---|
| 方案整体是否值得做 | 不值得，缩为 2 文件 + 1 测试 | 先修 5 阻断再实施；domain-auditor"按现状不建议直接实施" | **收敛**：四方交集是"PR1 内核（模板单一真相源 + 一致性守卫）有真收益；6 文件重构 + 统一签名不值得按现方案做"。minimalist 最激进、refactor-critic 最温和，但都认可内核 |
| 漂移发生在哪、影响面 | 真正 bug 是默认路径读 544 字 md 缺 W8/W12；931 字代码版是死代码；"运维覆盖丢约束"从未发生 | 字段真实存在且可 inline 覆盖，但生产配置为 null | **两者都对、侧重不同**：字段在 schema 中存在，但生产从未设置 → 代码版默认回退从不触发。问题应重述为"默认 md 正文缺 W8/W12 + 缺一致性守卫"，而非"两条路径漂移需架构收敛" |

---

## 5. 处置建议（重划 PR）

- **PR0（立即、无争议）**：按 minimalist §最小方案 + 四方认可项——
  1. `prompts/compact_summary.md` 补齐 W8/W12 全文句（注意还有 `IP`→`IP 地址` 这类逐字差异，不能假设只补两段）；
  2. 删 `config.py:125-150` 常量 + `schema/config/__init__.py:9,39` 导出 + 过时注释；
  3. 就地简化 `_resolve_no_tools_preamble_enabled` 与 `_resolve_prompt_template` 的形状猜测（保留对真实字段的读取）；
  4. 删 `except TypeError` fallback（`summary_engine.py:127-141`）与 `SUMMARIZER_SYSTEM_PROMPT`（含 `build_summarizer_payload` 去留交代）；
  5. 统一段数表述为 11（用 F4 的完整 grep 命令）。
  6. 加一致性守卫测试（拆双基线，见 B1）。
- **PR1（暂缓）**：`prompt_assembly/` 目录 + builder + adapters。仅在 B3/B5/I1/I2 全部修正、且确实出现第 4 条组装路径时再启动。
- **不做**：统一 LLM 边界签名（本质是两套既定契约，由 adapters 吸收即可）。

---

## 6. 评审团明确认可的部分（防止修订时误删）

1. 模板双写漂移诊断准确（四方实测一致）—— 值得优先修。
2. `_preservation_rules.py` 作为"单一真相源"范本正确（`hardening.py:7-9` 明确 "Single source of truth — Do NOT redefine here"）。
3. 删双签名 fallback 的批评成立（provider 内部 TypeError 会被误判为签名不匹配并重试）。
4. fail-loud 语义方向正确（缺模板抛错而非回退过期常量），只是触发时机要改。
5. preamble 注入首条 user message 而非 system，符合 prefix-cache 设计意图。
6. `test_history_compaction_contract.py` 与 prompt 组装正交——"不动它断言"的局部判断对，问题只出在传递 import（B2）。
7. 不纳入 budget_warning、staging/分块复用 `_one_stage` 的判断均准确。
