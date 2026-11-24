# PrismClaw 压缩 Prompt 组装逻辑 · 重构方案

> 文档类型：重构设计 + 实施记录（PR0 已落地，详见 §8 与顶部状态块）
> 日期：2026-11-24
> 前置调研：`PrismClaw压缩指令策略分析.md`（现状盘点、prompt 文本改进与目标全文）
> 评审依据：`PrismClaw压缩Prompt组装重构方案_架构评审结论.md`
> 方法：逐文件读取真实调用链 + 运行时实测（conda `pyclaw`），结论带代码位置佐证。

> **当前状态**：PR0 八步已于 2026-11-24 全部实施完成并逐步独立复核（commit `d5dd635` → `169d5cd`）；随后追加两次收尾：`9a9184d`（step 9 全案审计清理）与 step 10（修复 Step 1 引入的 carry-forward 契约回归，见 §8.6）。**PR0 主干末个提交 `9a9184d`**；其后 `bdf7e3a` 是前端文档整理，与 PR0 无关。compression 子集 `161 passed, 1 skipped / 162 collected`，全量 backend `1197 passed, 1 skipped`；ruff 对本轮改动源码全 clean；golden 三层逐字节一致（sha `b48e1a84`）。§8 为实施 runbook。prompt 文本修订已纳入本方案（§2.5 / 步骤 1）；per-result frontmatter 修复（520→167）拆出单独评估；组装器 PR1 暂缓。
>
> **测试运行注意**：本沙箱下 `pytest` 默认 basetemp 会因 tmp_path 拦截报 152 errors（环境误报，非代码问题），实跑需加 `--basetemp=/tmp/pytest_basetemp_prismclaw`；全量 backend 约需 2 分钟，前台默认 120s 会被 SIGTERM，应后台运行并把输出落盘后读取。

---

## 0. 目标与边界

**一句话目标**：先修真 bug——用 §2.5 的目标全文替换默认生效的 `compact_summary.md`（补齐缺失的 W8/W12 与领域适配改进），并用测试守住它不再漂移；再谈结构——在确有第二条组装路径时才建单一组装入口，两条 LLM 调用签名作为既定契约由边缘适配器吸收。

**明确边界**：

- PR0 只做 **prompt 真相源修复 + 文本修订 + 就地清理 + 一致性守卫**，不动压缩触发策略（circuit breaker / 预测阈值 / cooldown）、不动语义保真校验、不动预算编排。
- PR1 的组装器为条件性后续，触发条件见 §3.2。
- 沿用重构偏好：**不留 if/else 兼容分支、不为测试保留生产兼容路径、删除孤儿与双写、重构后批判性自查**。
- prompt 文本修订与机制修复**同批执行**：目标全文见 §2.5，来源为策略文档的领域适配改进（章节标题领域中立、第 6 段改用户关键原话、第 10 段事实层级、空段声明）+ 实测发现的 W8/W12 两段。

---

## 1. 现状：三条组装路径 + 四条注入/后处理路径

### 1.1 参与文件与职责

| 文件 | 现在承担 | 问题 |
|---|---|---|
| `engine/compression/_compact_prompt.py`（282 行） | 模板解析（3 种形状）、preamble 常量与注入、配置形状猜测、analysis 剥离（定义在 :143-170） | 组装职责与文本常量混在一起；`_resolve_no_tools_preamble_enabled` 遍历 3×2=6 个属性名，其中只有 `preamble_no_tools` 是真实字段 |
| `engine/compression/summary_engine.py`（283 行） | `build_summarizer_payload` → 替换 `effective_payload[0]` → 注入 preamble → chunk staging → 双签名 fallback | 组装逻辑与调度耦合；`except TypeError` 吞掉函数内部真实异常 |
| `engine/compression/_result_llm_summary.py`（154 行） | 独立加载 `per_result_summary.md`（不剥离 frontmatter）、独立常量、拼字符串 | 与会话级重复的加载模式；模型实际收到 520 字含 HTML 注释头 |
| `engine/compression/compaction.py:172-194` | `build_summarizer_payload` 构造 `[system, user]`；`SUMMARIZER_SYSTEM_PROMPT` 占位 | 与 `HARDENED_SUMMARIZER_SYSTEM_PROMPT` 两套 system 文本；占位随即被 `summary_engine.py:94` 的 `effective_payload[0]={...}` 覆盖——注意覆盖的是索引 0（system 位），user 在索引 1，重构时不可把 user 改为单条 |
| `schema/config/config.py:125-150` | `COMPACT_SUMMARY_DEFAULT_TEMPLATE` 代码内副本（931 字） | **实测：默认路径不生效**（生产 `template: null` 时优先读 md），线上实际看 544 字版 |
| `schema/config/config.py:19-27` | `try/except ImportError` 导入标识符文案，失败时用一句简版 fallback | 唯一消费者是 `:149` 模板常量；删常量后整段成孤儿，须一并删除 |
| `engine/compression/_preservation_rules.py` | `IDENTIFIER_PRESERVATION_INSTRUCTIONS` 单一定义 | 范本，保留；`hardening.py:9,14` 仍用于 HARDENED fallback，不可随模板常量同删 |
| `engine/compression/hardening.py:9,14` | 引用标识符文案构造 `HARDENED_SUMMARIZER_SYSTEM_PROMPT` | 活引用，保留 |
| `history_compaction.py:39-67` | re-export 7 个 `_compact_prompt` 符号 | 传给 `summary_engine.py:16-22` 生产消费方；**全测试零引用**（实测），删原符号须先收敛生产侧 import |
| `result_compaction.py:36-41` | re-export `_load_per_result_prompt` 等 | 被 `# noqa: F401` 掩盖；删函数时同步移除 |
| `schema/config/__init__.py:9,39` | re-export `COMPACT_SUMMARY_DEFAULT_TEMPLATE` | 删常量时必须同步，否则 `from prismclaw.schema.config import ...` 全失败 |

### 1.2 组装与注入路径

A–C 为 prompt 组装路径；D–G 为注入与后处理路径，不属本次修改对象。

```
A 会话级摘要（默认生效路径）
  SummaryEngine._one_stage()
    → build_summarizer_payload(messages)          # compaction.py:172
    → resolve_compact_prompt(config)               # config=None → 读 md（544 字）
    → effective_payload[0] = {system: prompt}      # 覆盖
    → inject_no_tools_preamble()                   # 首条 user message，保 system prefix-cache
    → summarize_fn(payload, model=...)             # 契约 1
    except TypeError → summarize_fn(payload)       # 隐式兼容（PR0 删除）
    → strip_analysis_block()                       # 最终一次

B 分块摘要（staging）
  → 与 A 共用 _one_stage，每块调用一次
  → 每块摘要不剥离，直接进 merge_payload；最终文本才剥离一次（summary_engine.py:165-166）

C 结果级摘要
  run_per_result_fork_agent()
    → _load_per_result_prompt()                    # 直接 read_text，不剥离 frontmatter
    → f"{prompt}\n\nTool: ...\n\nResult:\n..."
    → summarize_fn(prompt: str)                    # 契约 2（deps.py:385 独立契约）

D 预算告警注入     _soft_budget.py:83-193 → 注入 system message（system 前缀会变，不在本次范围）
E Fact Sheet 前导   history_compaction.py:671-718 → 以 user message 注入，顺序被契约测试锁定
F 归档无配置路径   archive.py:105-109 → 不传 config → HARDENED fallback
G 标识符段追加     history_compactor.py:154-172 → LLM 摘要后追加"已保留的技术标识符"
```

### 1.3 关键问题（有代码证据）

1. **默认 md 正文缺约束且领域不适配（真正的 bug）**：md 剥离后 544 字，缺 W8/W12 两段全文句；931 字的代码版只在 `template` 被显式设为 inline 时才生效，生产 `template: null` → **代码版是从不生效的死代码**。此外 11 段标题是编程助手口吻（"关键技术概念"/"关键文件与产出"），与医疗/法律/办公/科研四类负载不匹配。运维覆盖路径从未被使用，不是问题根源。
2. **配置形状猜测**：`_resolve_prompt_template` 用"含不含换行"判断 inline/路径；`_resolve_no_tools_preamble_enabled` 遍历 3 名 × 2 层。注意 **`preamble_no_tools` 是真实 schema 字段**（`config.py:298`，生产 `agent.yaml:61=true`），另两个名字是虚构的；删除时须保留对真实字段的一次直接读取。
3. **双签名 fallback**：`summary_engine.py:127,141` 的 `except TypeError` 会吞掉 provider 内部真实 TypeError 并用无参签名重试。**仅对 `_one_stage` 这一处成立**；两个业务入口的签名差异是既定契约（契约 1/契约 2），不是历史误用。
4. **两套 system 文本 + 覆盖中间态**：`HARDENED_...`（fallback，archive 路径用）与 `SUMMARIZER_SYSTEM_PROMPT`（占位）。`build_summarizer_payload` 先造占位 `[system, user]`，`summary_engine.py:94` 再覆盖索引 0（system 位）。**关键事实**：user 在索引 1，若按错误方案把 build 改成只返回 user 单条，`[0]=` 覆盖会吃掉 user 消息。正确改法是让 build 接收 `system_prompt` 参数一次构造到位，同时删占位常量与覆盖行。
5. **段数表述漂移**：`9-section` / 中文 `9 章节` / `10-section` 散落多处，实际 11 段。需用完整正则捕全（见 §5）。

---

## 2. 目标架构

### 2.1 分层

```
【PR0 · 原地修改，不建目录】
  prompts/compact_summary.md       用 §2.5 目标全文整体替换正文
  schema/config/config.py          删模板常量 + :19-27 孤儿导入 + :224 description + 过时注释
  schema/config/__init__.py        同步删 re-export
  _compact_prompt.py               就地简化形状猜测（保留真实字段读取）
  summary_engine.py                删 except TypeError fallback
  compaction.py                    删占位常量；build_summarizer_payload 接收 system_prompt 直接构造完整 payload
  全仓注释                         段数统一为 11

【PR1 · 暂缓，触发条件见 §3.2】
  engine/compression/prompt_assembly/
    ├── __init__.py       导出 build_summarizer_request / get_prompt_source
    ├── source.py         PromptSource：唯一装载点（paths + 测试隔离）
    ├── fragments.py      preamble / 保留规则 / 输出格式片段
    ├── spec.py           SummarizerSpec（scope、payload、fallback 决策）
    ├── builder.py        唯一组装入口
    └── adapters.py       messages / str 两个边缘适配器

【不属于本次】prompts/budget_warning.md —— 归 soft_budget，职责不同
```

### 2.2 核心接口（PR1 草案）

```python
# spec.py
@dataclass(frozen=True)
class SummarizerSpec:
    scope: Literal["history", "result"]   # budget_warning 归 soft_budget，不纳入
    payload: list[dict[str, Any]] | str
    enabled_preamble: bool                 # 必填，由 config.history.preamble_no_tools 显式赋值
    strip_analysis: bool                   # 必填，输出标记（非执行点）
    template_enabled: bool = True          # 承载 enabled=False / config=None 的 fallback 决策
    system_prompt: str | None = None       # HARDENED fallback 文本，单独标注、不算第二份正文
    model: str | None = None

# 按 scope 的默认值：result 路径现状不注入 preamble、不剥离 analysis
DEFAULT_FLAGS = {
    "history": dict(enabled_preamble=True, strip_analysis=True),
    "result":  dict(enabled_preamble=False, strip_analysis=False),
}

@dataclass(frozen=True)
class AssembledRequest:
    messages: list[dict[str, Any]]
    strip_analysis: bool
    prompt_source: str                     # file / inline / fallback

def build_summarizer_request(spec, *, source) -> AssembledRequest: ...
def to_messages_request(req) -> list[dict[str, Any]]: ...
def to_text_request(req) -> str: ...       # result 路径专用，仅此一步适配
```

**关键设计点**：

- 目标是"**一个组装入口 + 两个边缘适配器**"，不统一 LLM 边界签名——契约 1/契约 2 是两条既定注入链，由 adapters 吸收。
- `template_enabled` + `system_prompt` 显式承载 fallback，避免 archive 路径 system prompt 被静默改写。
- `strip_analysis` 只是输出标记，执行点固定在 `summarize_in_stages` 返回之后、**仅一次**（§2.4）。

### 2.3 模板源：单一真相源

```python
# source.py
class PromptSource:
    """唯一装载点。md 文件是真相源；生产 inline 覆盖经 paths 显式传入。"""

    def __init__(self, prompt_dir: Path,
                 paths: Mapping[str, Path] | None = None):   # per-scope 覆盖
        ...

    def get(self, scope: str) -> str:
        path = self._paths.get(scope, self._prompt_dir / f"{scope}.md")
        text = path.read_text(encoding="utf-8")
        return strip_frontmatter(text)      # 复用 _strip_md_frontmatter：if/elif 二选一，HTML 注释优先，只剥开头一个块
```

- **测试注入隔离**：`inline=` 从生产 `PromptSource` 构造函数移除；测试用 `tests/` 内的 `TestPromptSource` 子类或 monkeypatch 路径常量，避免"为测试保留的生产分支"。`TestPromptSource` 不从 `prompt_assembly/__init__.py` 导出；加测试断言生产 bootstrap 构造的 source `prompt_source == "file"`。
- **per-result 的 frontmatter 语义**：结果级路径不纳入本次，保持现状（520 字含注释头，不剥离）。若 PR1 纳入，其 frontmatter 处理须单独评估（剥离后为 167 字），禁止复用 history 的字节基线。
- **fail-loud 触发时机**：风险描述为"压缩时抛错并由 circuit breaker 处理"，并加启动期 md 存在性轻量校验；archive 的宽泛 `except Exception`（`archive.py:113-118`）会吞异常，需在 PR1 单独收窄。

### 2.4 契约归属矩阵

| 契约 | 归属层 | builder 是否触碰 |
|---|---|---|
| 11 段结构 / 英文作废 marker / 标识符保留文案 | prompts md + fragments（文本契约，代码不校验段数总数） | 只装载，不改正文 |
| `<analysis>` 剥离（含未闭合标签防御 W5） | builder 下游，**最终一次**；`config=None → True` 安全默认 | 执行点固定在 `summarize_in_stages` 返回后 |
| 第 7 段强制前传（`hardening.py:231`） | 制品后处理，发生在 SummaryEngine 返回**之后** | **不碰** |
| W12 作废前传（`hardening.py:233`） | 同上 | **不碰** |
| 标识符提取与校验（`semantic_fidelity.py`，两套独立实现） | 运行时校验 | **不碰**；本次只统一文案片段 |

> 标识符文案（`_preservation_rules.py`）与标识符提取逻辑（两套实现）是两层，不要因"单一真相源"措辞而误合并。

### 2.5 目标 prompt 全文（写入 `compact_summary.md` 正文）

以下为 PR0 步骤 1 的目标正文，替换 `compact_summary.md` 从 `# 关键：禁止使用任何工具。只输出文本。` 起的内容。HTML 注释头**保留但同步改写**（它是运维说明，`_strip_md_frontmatter` 会剥离，不进 prompt 正文）：去掉"9 段摘要"、去掉"未设置时回退 COMPACT_SUMMARY_DEFAULT_TEMPLATE"这两句已失真表述。相对现行 544 字版的改动：第 2/3/6 段标题、`<analysis>` 第 4 条、规则块 6 条（含 W8/W12）；编号、marker、标识符块不动。

````markdown
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
````

**一致性说明**：

- 目标全文的标识符行写「IP 地址」，`_preservation_rules.py:15` 的文案写「IP」。两者是不同消费者（主路径 prompt vs HARDENED fallback 文案片段），**不是双写副本**，措辞差异可接受；本次不改 `_preservation_rules.py`（§2.4）。
- 第 11 段标题内的英文 marker 必须与 `hardening.py:233` 的集合保持一致（voided / superseded / cancelled / retracted / replaced）——这是 W12 前传生效的前提，替换时逐字核对。
- 每个章节必须出现、空段写「（无）」：`hardening.py` 的空段判定目前**不**含中文「（无）」，但章节保留、正文短，不影响 carry-forward；若要精确判定属后续代码改动，本次不做。

---

## 3. 迁移步骤

### 3.1 PR0 · 内核修复（八步）

按依赖顺序，每步可独立验证：

| 步 | 动作 | 验证 | 删除项 |
|---|---|---|---|
| 1 | 用 §2.5 目标全文整体替换 `prompts/compact_summary.md` 正文；**同步改写 HTML 注释头**（去掉"9 段摘要"表述、去掉"未设置时用 COMPACT_SUMMARY_DEFAULT_TEMPLATE 回退"这句已失真的说明——当前 `template: null` 实为直接读 md）；逐字核对第 11 段英文 marker 与 `_VOIDED_MARKERS` 一致 | 守卫测试 `test_compact_md_matches_canonical_text` 转绿；注释头无 9 段/常量回退表述 | — |
| 2 | 导出机制基线：用 `resolve_compact_prompt(CompactionConfig())`（实测稳定返回 544 字 md 正文）+ 固定 messages，导出 system prompt 与注入 preamble 后的 user message 两层字节，落盘 `tests/fixtures/*_golden.bin`（含 sha256）。**时点必须在步骤 1 之后、步骤 3 之前**——取在步骤 1 前会因计划内文本变更而红，取在步骤 3 后则旧代码已删无法捕获 | golden 文件入库；system sha256 = `bf977216b851455f`（现行 544 字版） | — |
| 3 | **先改 `_compact_prompt.py`**：删 `:10` 的 `COMPACT_SUMMARY_DEFAULT_TEMPLATE` import、去掉 `_resolve_prompt_template` 的 `default` 形参（`:173-224`）——**缺失行为决策：去掉回退，`read_text` 的 `FileNotFoundError` 自然上抛**（零新增代码，不引入 `PromptNotFoundError` 类）；再删 `config.py:125-150` 常量 + `:19-27` 孤儿导入/fallback + `:224` description 里的常量名 + `:243-246` 过时注释 + `schema/config/__init__.py:9,39` 导出。**顺序不可颠倒**——先删常量会让 `_compact_prompt.py:256` 的 `default=` 实参 ImportError，整个包导入崩溃 | `pytest --collect-only` 全绿（无 ImportError）；`grep -rEn COMPACT_SUMMARY_DEFAULT_TEMPLATE backend/src` 零命中 | 代码内模板副本 + 孤儿导入 + default 形参 |
| 4 | 就地简化 `_resolve_no_tools_preamble_enabled`（只留直读 **`config.history.preamble_no_tools`**，保留 `config is None`/`history` 缺失时安全默认 True；**禁止写成顶层 `config.preamble_no_tools`**——会永远取 None 并静默无视 `agent.yaml:61=false`）。**`_resolve_prompt_template` 的三形状判断 PR0 不动**：None→读 md / 多行→inline / 单行→路径是真实运维契约（`config.py:220-226` 对外字段，已决策保留），去掉多行 inline 支持属 breaking change，留到 PR1 用 `PromptSource(paths=...)` 等价承接 | 相关单测通过；配置 `preamble_no_tools: false` 时 preamble 确实不注入；inline 多行模板仍可作为 `template` 生效 | 虚构属性名遍历（仅 preamble 的 3×2 遍历） |
| 5 | 删 `summary_engine.py:127-141` 的 `except TypeError` fallback | spy 测试：调用一次且带 `model=`；内部 TypeError 原样上抛 | 隐式兼容分支 |
| 6 | 删 `SUMMARIZER_SYSTEM_PROMPT`（`compaction.py:147-151`）；`build_summarizer_payload(messages, *, system_prompt)` 改为接收最终 system prompt 并直接构造完整 `[system, user]` 两条；`summary_engine.py` 删除 `effective_payload[0] = {...}` 覆盖，改为 `build_summarizer_payload(payload, system_prompt=compact_prompt or HARDENED_SUMMARIZER_SYSTEM_PROMPT)` | 无 NameError；无孤儿函数；payload 结构与旧版逐字节一致（对 §3.3 机制 golden） | 占位常量 + `[0]=` 覆盖中间态 |
| 7 | 统一段数表述为 11（完整正则见 §5：**加 `-i`** 以捕大写 `9-Section`，并含 `config.py:238`）；排除正当的"第 9/10/11 节"与测试设计文档章节 | grep 零残留 | 过期注释 |
| 8 | 批判性自查（§5） | 全绿 | — |

**不能省的一步**：第 2 步必须在第 1 步之后、第 3 步之前。基线取在第 1 步之前会因计划内文本修正而红；取在第 3 步之后则旧代码已删、无法捕获。

### 3.2 PR1 · 条件性后续

**启动条件（满足全部才做）**：

1. §2.2/§2.3 的接口草案已落定（含 fallback 决策、scope 边界、运维覆盖能力）；
2. 确实出现第 4 条 prompt 组装路径，使"各自组装"的成本超过建组装器的成本；
3. archive 的宽泛 except 已收窄，fail-loud 在归档路径生效。

**步骤**：建 `prompt_assembly/` 六文件 → 调用方改深路径直连、收敛 re-export → 切换调用方 → 删除旧路径。每步独立提交，`pytest --collect-only` 与契约测试为独立验收条件。

### 3.3 基线策略

| 基线 | 生成时点 | 守卫什么 |
|---|---|---|
| 机制一致 golden | PR0 步骤 1 之后（文本已换） | 组装机制不变：system prompt + 注入后 user message 逐字节相等。**需新建 `backend/tests/fixtures/` 目录**（当前不存在） |
| 文本修正允许清单 | PR0 步骤 1 | 544 字现行版 → §2.5 目标全文是唯一合法 diff；含 W8/W12、领域标题、事实层级规则，其他变化一律失败 |

---

## 4. 测试策略

新增 `backend/tests/engine/compression/test_prompt_template_consistency.py`（PR0）：

| 用例 | 断言 |
|---|---|
| `test_compact_md_matches_canonical_text` | `compact_summary.md` 剥离注释头后与 §2.5 目标全文逐字相等；另断言 W8/W12 关键词、英文 marker 集合存在。**11 段断言必须用行锚定写法**（实测：`^\d+\. ` 在现行正文返回 14、目标正文返回 15，因为 `<analysis>` 内 3/4 条编号被计入；正文叙述句又含行内 `<analysis>`/`<summary>` 字样，非锚定的 `re.search` 也会从叙述处开始匹配）：<br>`m = re.search(r"^<summary>[ \t]*\r?\n(.*?)^</summary>[ \t]*$", body, re.M \| re.S); assert m; numbers = [int(n) for n in re.findall(r"^(\d+)\. ", m.group(1), re.M)]; assert numbers == list(range(1, 12))`。断言完整序列优于只断言 `len==11`（后者挡不住跳号/重复） |
| `test_no_in_code_template_duplicate` | AST 扫描 `src/**/*.py` 代码节点，确认 config 模块不再导出模板常量，且 `:19-27` 孤儿导入已删（排除字符串/注释，避免误报） |
| `test_missing_template_fails_loud` | 模板路径不存在时 `resolve_compact_prompt` 抛 `FileNotFoundError`（**决策：(b) 零新增异常类**，`read_text` 自然上抛）；断言不返回任何过期常量文本 |
| `test_preamble_reads_schema_field` | preamble 开关直接读 `config.history.preamble_no_tools` |
| `test_payload_shape_unchanged_by_cleanup` | 结构清理前后 user message 逐字不变（对 §3.3 机制 golden） |
| `test_single_typeerror_not_swallowed` | summarizer 内部抛 TypeError 时原样上抛，`call_count == 1` 且带 `model=` |

> 规范文本的存放：§2.5 是设计文档中的目标；测试内另 checked-in 一份 `tests/fixtures/compact_summary.canonical.md` 作为 golden（"故意的第二份"，只用于逐字节比对，不是运行时真相源）。**需新建 `backend/tests/fixtures/` 目录**——当前 backend/tests 下无任何 md 文件与 fixtures 目录（实测）。运行时真相源始终是 `prompts/compact_summary.md`。

PR1 阶段追加：

| 用例 | 断言 |
|---|---|
| `test_builder_scope_defaults` | result scope `enabled_preamble=False, strip_analysis=False`，无 preamble 前缀 |
| `test_builder_fallback_preserved` | `template_enabled=False` / config=None 时 system 为 HARDENED，不被 md 覆盖 |
| `test_staging_strip_once` | chunk>1 时每块不剥离、最终文本剥离一次 |
| `test_collect_only_after_export_cleanup` | 删 re-export 后 `pytest --collect-only` 全绿（含契约测试、archive、material 测试） |
| `test_production_source_never_inline` | 生产 bootstrap 构造的 source `prompt_source == "file"` |

保留并继续跑：`test_history_compaction_contract.py`（323 行）——与 prompt 组装正交，其 import 列表与删除清单无交集（实测全测试对删除符号零引用）。PR0 阶段的守卫是**每步跑 `pytest --collect-only`**（见 §8），不依赖任何 PR1 测试；`test_collect_only_after_export_cleanup` 属 PR1，不在 PR0 范围。

---

## 5. 批判性自查清单

- [ ] `grep -rEn` 确认 `COMPACT_SUMMARY_DEFAULT_TEMPLATE` 在 `backend/src + backend/tests` **零引用**；`_resolve_prompt_template` / `_resolve_no_tools_preamble_enabled` 保留函数本身、但虚构属性名遍历已删（`P2_2_PER_RESULT_PROMPT` 属已拆出的 per-result 路径，本次不检查、不删除）
- [ ] `config.py:19-27` 的 import/fallback 已删；`hardening.py:9,14` 对 `_preservation_rules.py` 的引用仍在
- [ ] `_resolve_prompt_template` 已无 `default` 形参；模板缺失抛 `FileNotFoundError`，无静默回退、无新增异常类
- [ ] `pytest --collect-only` 全绿——删函数/常量后所有剩余 import 仍可解析
- [ ] `except TypeError` 双签名 fallback 已删除；两个业务入口签名保持不变，由 adapters 吸收
- [ ] 保留了对真实字段 `preamble_no_tools` 的一次直接读取；虚构的两个属性名已删
- [ ] `compact_summary.md` 正文与 §2.5 目标全文逐字相等；HARDENED fallback 文本单独标注，不算第二份正文
- [ ] 第 11 段英文 marker 与 `hardening.py:233` 集合一致（W12 前传前提）
- [ ] `prompts/budget_warning.md` 未被纳入组装器
- [ ] 段数 grep 用完整正则（加 `-i` 捕大写 S）：`grep -rEni '9-section|9 章节|9 段|10-section|9章节|9段' backend/src backend/tests`；排除正当的"第 9/10/11 节"引用
- [ ] 压缩制品契约测试（第 7 段、W12 marker）仍绿
- [ ] 运行时实测：system prompt 与注入后 user message 两层字节分别对 §3.3 机制 golden 逐字节比对；文本 diff 仅限 §3.3 允许清单

---

## 6. 风险与不做的事

| 风险 | 处置 |
|---|---|
| 模板文件缺失 | 触发点是**压缩时**而非启动期：`FileNotFoundError` 由 circuit breaker 处理；加启动期轻量存在性校验。（不新增"已安装包读取"测试——复查实测 wheel 已含三个 md，该测试守的是伪前提） |
| archive 路径吞掉模板缺失异常 | 缺失行为为 `FileNotFoundError`，`archive.py:113-118` 宽泛 except 会吞掉它并在 PR0 阶段静默降级；该路径在 PR1 收窄 except，PR0 不触碰 |
| 运维 inline 覆盖字段的存废 | **保留**：实测确认是活逻辑——`summary_engine.py:93` → `resolve_compact_prompt`（`_compact_prompt.py:227`）→ `_resolve_prompt_template`（`:173-224`）消费；`config.py:220-226` 为对外 schema 契约；`agent.yaml:67` 与 `agent.example.yaml:70` 显式声明 `template: null`；无 env 旁路。PR1 的 `PromptSource` 须提供等价 inline / 路径覆盖能力。**注意区分**：代码内常量 `COMPACT_SUMMARY_DEFAULT_TEMPLATE` 才是冗余（默认路径优先读 md，常量仅 md 缺失时回退），在 PR0 步骤 3 删除 |
| prompt 文本修订改变模型可见内容 | 这是**有意的修复**（544 字 → §2.5 目标全文：补 W8/W12 + 领域适配 + 事实层级）；用文本修正允许清单约束唯一合法 diff，其他变化一律失败。不调整触发阈值或注入位置，避免变量混改无法归因 |
| per-result 520→167 | 已拆出单独评估，本次不改变模型可见内容 |
| 一次改太多难定位 | PR0 八步逐步提交；PR1 不在此次范围 |

**明确不做**：

- 不统一 LLM 边界签名（契约 1/契约 2 是既定契约，由 adapters 吸收）
- 不改触发阈值、circuit breaker、预测性压缩、注入位置（KV cache 复用先记账，待指标验证）
- 不改 `hardening.py` 的 section 解析与 carry-forward 契约；不在 PR0 收窄 archive except
- 不引入插件化模板引擎或外部依赖
- 不把 budget_warning 塞进组装器
- 不合并标识符文案与两套提取逻辑；不改 `_preservation_rules.py`
- 不把中文「（无）」加入 `hardening.py` 空段判定（属后续代码改动）

**待清理的已知残留**（并入 PR0 步骤 3/7）：`config.py:121-123,214,223,238,296` 仍有"9 章节"/"9-section"表述（`context_bundle.py:104` 的 `10-section`、`_compact_prompt.py:146,230` 与 `hardening.py:217,220,237` 的大写 `9-Section` 亦在列）；`config.py:243-246` 注释"Pydantic reads default values from COMPACT_SUMMARY_DEFAULT_TEMPLATE"是错的（字段 default=None，Pydantic 不会从常量读默认值），删常量时须一并删除；`compact_summary.md` HTML 注释头仍称"9 段摘要"，随正文替换一并更新。


---

## 7. 实施前复查裁决（2026-11-24 二次复核）

本节记录对 PR0 方案的独立复查结论：两位专家分别核对 8 条代码前提与 5 条测试前提，全部基于 grep / 运行时实测，不采信方案自述。**结论：方案在修正 §3.1 步骤 6 后仍不可直接开工，上述正文已按本节裁决逐条修订。**

### 7.1 硬阻塞（已修订进正文）

| # | 问题 | 证据 | 处置 |
|---|---|---|---|
| 1 | 步骤 3 单独执行必然 ImportError | `COMPACT_SUMMARY_DEFAULT_TEMPLATE` 全仓 8 处引用，含 `_compact_prompt.py:10` import 与 `:256` 的 `default=` 实参；方案只删 config/`__init__` 两处 | 步骤 3 改为**先改 `_compact_prompt.py` 再删常量**（已修订） |
| 2 | `PromptNotFoundError` 全仓不存在，测试策略与实施脱节 | grep 零命中；现行 `_resolve_prompt_template:204` 是 `return default` 静默回退 | **已定案：选 (b)**——不新增异常类，去掉回退让 `FileNotFoundError` 自然上抛；步骤 3 与 §4 测试均已按 (b) 修订 |
| 3 | 步骤 6 错误表述（只构造 user 单条会被 `[0]=` 覆盖吃掉） | `compaction.py:191-194` 返回 `[system,user]`，`summary_engine.py:94` 覆盖索引 0 | 已在本轮早前修正为"传 `system_prompt` 参数一次构造到位" |

### 7.2 事实纠错（已修订进正文）

1. `strip_frontmatter` 是 `if/elif` 二选一（HTML 优先、只剥一个块），不是"同时处理两种"（§2.3 已改）。
2. 步骤 1 注释头处理自相矛盾，且注释头 `:16-18` 的"常量回退"说法当前就已失真（步骤 1 已改为同步改写注释头）。
3. `preamble_no_tools` 真实路径是 `config.history.preamble_no_tools`，写成顶层会永远 None（步骤 4 已改）。
4. 步骤 7 正则漏大写 `9-Section`（`:125`/`:217`）与 `config.py:238`（§5/§6 已改，正则加 `-i`）。
5. §1.1 "re-export 传给多个测试"失实——全测试零引用（§1.1 已改）。
6. PR1 `test_installed_package_reads_prompt` 是**伪前提**——实测 `pip wheel` 产出已含三个 md（md 在 `src/prismclaw` 包内，hatchling 默认打包），该测试已删。
7. section 计数正则陷阱：`^\d+\. ` 在现行正文返回 14、目标正文返回 15（`<analysis>` 内 3/4 条编号被计入）；非锚定 `<summary>` 匹配又会吞进叙述句。§4 已写明行锚定 + 1..11 完整序列断言。

### 7.3 已证实成立（可放心执行）

- 基线抓取：`resolve_compact_prompt(CompactionConfig())` 稳定返回 544 字 md 正文，与文件剥离后逐字相等（sha256 `bf977216b851455f`）。
- 步骤 5 安全：两个生产注入点（`runner_assembly.py:604-635`、`archive.py:93`）均为 `(payload, *, model=None)`；`except TypeError` 零测试覆盖；emit/estimate 的 TypeError 被内层 `except Exception` 先吞，不受影响。
- 步骤 6 字节一致：改成传参后 payload 逐字节相同（SHA `a81fc6c4443318c4`）；`inject_no_tools_preamble` 按 role 遍历、不依赖索引。
- 契约测试符号正交；`build_summarizer_payload` 全测试零调用；基线 `152 tests collected in 0.89s`，退出码 0。
- 工作区基线干净（HEAD `c4a506a`），可从零开始重构。


---

## 8. 实施步骤设计（PR0 Runbook）

本章把 §3.1 的八步转成可逐步执行、逐步复核的操作手册。**只描述如何实施，本次不执行**。所有命令在仓库根目录运行，Python 一律用 `conda run -n pyclaw python`。

### 8.1 执行协议（不可跳过）

1. **实施与复核分离**：每步由实施 worker 改代码；改完由另一个全新 worker 独立复核（fresh eyes，不复用实施 worker 的上下文）。复核通过才进下一步。
2. **原子提交**：每步一个独立 commit，message 格式 `refactor(compression): step N - <动作>`。禁止把多步揉进一个提交。
3. **门禁**：每步必须通过 §8.2 对应验证命令才能提交；任何一步失败，在该 commit 内修复或回滚重来，不带病进入下一步。
4. **基线锚点**：HEAD `c4a506a`、工作区干净、`152 tests collected in 0.89s`（退出码 0）、现行 system sha256 `bf977216b851455f`。每步 collect 数量应保持不变或按 §8.3 计划递增。
5. **复核者要证伪，不要盖章**：验证命令必须能失败（例如故意传错字段看 test 是否真红）；只跑一遍"看起来绿"不算复核。

### 8.2 逐步 runbook

**Step 0 · 基线确认（只读）**

- 前置：无。
- 命令：`git status --short`（compression 相关应为空）；`conda run -n pyclaw python -m pytest backend/tests/engine/compression/ -q --collect-only`（应 `152 collected`）。
- 门禁：两项都符合基线锚点。

**Step 1 · 替换 prompt 正文 + 改写注释头**

- 文件：`backend/src/prismclaw/engine/compression/prompts/compact_summary.md`（唯一文件）。
- 改动：正文从 `# 关键：禁止使用任何工具。只输出文本。` 起整体替换为 §2.5 全文；HTML 注释头同步改写（去"9 段摘要"、去"回退 COMPACT_SUMMARY_DEFAULT_TEMPLATE"表述）。
- 验证：`python` 读文件剥离注释头后，逐字比对 §2.5；用 §4 的行锚定断言检查 `<summary>` 内编号恰为 `1..11`；检查 W8/W12 与 5 个英文 marker 存在；`git diff --stat` 只含这一个 md。
- 门禁：全部通过；collect 仍 152（此时测试尚未新增，验证用临时脚本，不落盘）。
- 回滚：`git checkout -- <该 md>`。

**Step 2 · 导出机制基线 golden**

- 改动：新建 `backend/tests/fixtures/`；落盘 system prompt 字节、注入 preamble 后 user message 字节两份 golden + sha256 清单。抓取方式用 `resolve_compact_prompt(CompactionConfig())`（实测稳定返回剥离后 md 正文）+ 固定 messages。
- 时点：**必须在 Step 1 之后、Step 3 之前**。
- 验证：golden 文件可回读、sha256 匹配；system 部分对应 Step 1 后的新正文（不是旧的 544 字）。
- 门禁：golden 入库；**代码零改动**（本步只加测试数据）。
- 回滚：删除新增 fixture 文件与目录。

**Step 3 · 先改解析器再删常量（硬阻塞，顺序不可颠倒）**

- 3a（先做）：`_compact_prompt.py` 删 `:10` 的 `COMPACT_SUMMARY_DEFAULT_TEMPLATE` import；`_resolve_prompt_template` 去掉 `default` 形参，md 缺失时让 `read_text` 的 `FileNotFoundError` 自然上抛；`:254` 调用点去掉 `default=` 实参；docstring 的"三种输入形状/回退 default"改为"两种形状 + 缺失抛错"。
- 3a 后立即跑 `--collect-only`，必须绿——这是防止 ImportError 的关键关卡。
- 3b（后做）：`config.py` 删 `:19-27` 孤儿导入/fallback、`:125-150` 常量、`:224` description 里的常量名、`:243-246` 过时注释；`schema/config/__init__.py` 删 `:9,:39` 导出。
- 验证：`grep -rEn COMPACT_SUMMARY_DEFAULT_TEMPLATE backend/src backend/tests` 零命中；`--collect-only` 绿；`python -c "import prismclaw.schema.config; import prismclaw.engine.compression._compact_prompt"` 成功。
- 门禁：三项全过。回滚按 3b→3a 逆序 `git checkout`。

**Step 4 · 简化 preamble 配置读取**

- 文件：`_compact_prompt.py` 的 `_resolve_no_tools_preamble_enabled`（`:96-122`）。
- 改动：删掉 3 名 × 2 层的属性名遍历，只保留直读 `config.history.preamble_no_tools`；保留 `config is None` 与 `history` 缺失时默认 True。**不要动 `_resolve_prompt_template` 的形状判断**（真实运维契约，见 §3.1 步骤 4）。
- 验证：构造 `config.history.preamble_no_tools=False` 的桩，确认 `inject_no_tools_preamble` 未被调用；构造 `None` 与缺 `history` 的桩，确认为 True；inline 多行模板仍能作为 `template` 生效。
- 门禁：三项全过；collect 152。

**Step 5 · 删双签名 fallback**

- 文件：`summary_engine.py:104-141`。
- 改动：删除外层 `try/except TypeError` 及其无参重试，直接 `result = await summarize_fn(effective_payload, model=model)`；保留 emit/estimate 各自的内层 `except Exception`（它们是静默指标，不属于兼容分支）。
- 验证：spy summarizer 断言 `call_count == 1` 且 `kwargs["model"]` 正确；另令 summarizer 内部抛 TypeError，断言原样上抛；契约测试绿。
- 门禁：通过；collect 152。

**Step 6 · payload 构造一次到位**

- 文件：`compaction.py`、`summary_engine.py`。
- 改动：删 `SUMMARIZER_SYSTEM_PROMPT`（`:147-151`）；`build_summarizer_payload(messages, *, system_prompt)` 用参数构造 `[system, user]`；`summary_engine.py:92-97` 的"建 payload → `[0]=` 覆盖"合并为一次 `build_summarizer_payload(payload, system_prompt=compact_prompt or HARDENED_SUMMARIZER_SYSTEM_PROMPT)`。
- 验证：对 Step 2 的机制 golden 逐字节比对（实测应一致，SHA `a81fc6c4443318c4`）；`grep SUMMARIZER_SYSTEM_PROMPT backend/src` 仅剩 `HARDENED_` 变体；collect 152。
- 门禁：字节一致 + collect 绿。这是全案最容易改坏的一步，复核者必须独立重算 SHA，不采信实施者结论。

**Step 7 · 段数表述统一**

- 改动：`grep -rEni '9-section|9 章节|9 段|10-section|9章节|9段' backend/src backend/tests`，逐个改为 11 或去数字；排除"第 9/10/11 节"正当引用与测试设计文档章节。
- 验证：同正则复跑零误报；列出改动清单与 §6"待清理残留"逐项对账。
- 门禁：清单对账一致；collect 152。

**Step 8 · 全量复核**

- 新增 §4 的 `test_prompt_template_consistency.py`（6 个用例）并落盘 canonical fixture。
- 跑 `pytest backend/tests/engine/compression/ -q`，应 152 + 6 = **158 passed**（若基线含既有失败，以 Step 0 实测为准记录差值）。
- 逐条核对 §5 自查清单与 §7 复查裁决；`git diff` 全文审查，确认无 if/else 兼容分支、无新增异常类、无"为测试保留"的生产代码。
- 门禁：全部勾选；输出最终 diff 统计。

### 8.3 测试落地时机（TDD 节奏）

| 步骤 | 新增/修改测试 | 目的 |
|---|---|---|
| Step 1 | 临时验证脚本（不落盘） | 证正文与 §2.5 逐字相等、11 段合法 |
| Step 2 | 落盘 golden 文件（非测试代码） | 锁定机制基线 |
| Step 3 | `test_no_in_code_template_duplicate`、`test_missing_template_fails_loud` | 守常量删除与缺失抛错 |
| Step 4 | `test_preamble_reads_schema_field` | 守真实字段读取 |
| Step 5 | `test_single_typeerror_not_swallowed` | 守 TypeError 不再被吞 |
| Step 6 | `test_payload_shape_unchanged_by_cleanup` | 对 golden 守字节一致 |
| Step 8 | `test_compact_md_matches_canonical_text` | 守正文不漂移（最后落盘，避免 Step 1 时测试不存在却声称"转绿"） |

> 注意：§3.1 步骤 1 的验证列写"守卫测试转绿"，实际该测试在 Step 8 才落盘——Step 1 的验证应以临时脚本为准，此处以本章时序为准。

### 8.4 失败处理与回滚

- 任一步 collect 失败：优先查该步 import / 签名；Step 3 失败按 3b→3a 逆序回滚，绝不带 ImportError 继续。
- Step 6 字节不一致：说明 payload 结构被改坏，回滚并重新核对 system/user 两条的 role 与顺序。
- 禁止用"加一个兼容分支"来让测试变绿——正确做法是回到方案前提查证（§7 已列出全部事实纠错）。

### 8.5 最终完整复核清单（全案完成后）

1. `git diff --stat`：改动文件集合与 §2.1 PR0 清单一致，无额外文件。
2. `grep -rEn COMPACT_SUMMARY_DEFAULT_TEMPLATE backend/`：零命中。
3. `grep -rEn 'SUMMARIZER_SYSTEM_PROMPT' backend/src`：仅 `HARDENED_` 变体。
4. `grep -rEni '9-section|9 章节|9 段|10-section' backend/src backend/tests`：零误报。
5. `pytest backend/tests/engine/compression/ -q`：全绿，数量符合 §8.3。
6. 机制 golden 逐字节比对通过；文本 diff 仅限允许清单。
7. 人工审 §5 自查清单 12 项全部勾选。
8. 人工审 `git diff`：无兼容旧逻辑、无为测试保留的生产分支、无新增 if/else；确认 `_preservation_rules.py` 与 `hardening.py` 未被改动。


### 8.6 PR0 后追加修复（step 9 / step 10）

#### step 9：全案审计清理（`9a9184d`）

删孤儿常量、修失真描述/过时注释、移除死兼容块。**该轮结论「无功能性 bug」有覆盖盲区**：只扫了改动文件内部的孤儿/注释/兼容残留，未做「prompt 文本契约 ↔ 下游解析器」的联动运行时验证，因此漏掉了 step 10 修的回归。

#### step 10：carry-forward 契约回归修复

**根因（PR0 Step 1 引入，非历史遗留）**：§2.5 新 prompt 第 47 行同时规定「每个章节必须出现」与「确无内容时正文写「（无）」」；`hardening.py:_section_is_substantive()` 原本只识别英文空声明。于是新摘要每个章节都在、空段写「（无）」被判为 substantive，`carry_forward_missing_sections` 的 `continue` 使第 7 段结转永不触发。生产调用点 `history_compaction.py:310-328` 已证可达，后果是 prior 摘要的待办任务在链式压缩中静默丢失，W8/W12 结构性保证失效。旧版 prompt 不强制章节全出现，「缺段」路径仍能触发结转，故属本次重构回归。

**运行时证据**（conda `pyclaw` 探针）：`_section_is_substantive("7. 待办任务\n（无）")` 修复前 `True`、写 `None` 为 `False`；新摘要第 7 段为「（无）」时修复前 `carry_forward=False`，英文 `None` 或缺段时 `True`。

**修法**（`hardening.py`，单入口两检查，不新增兼容分支）：

1. 原始 body 保留既有英文空声明判定（精确词形 + `no pending/current/active/outstanding` 前缀），项目符号/括号前缀天然挡住含实质指令的长句；
2. 新增 `_normalize_empty_declaration()` 把「（无）」的包裹变体归一为规范词形，**只做精确比对、不参与前缀匹配**——这是避免误伤的关键。

常量 `_EMPTY_DECLARATION_WORDS / _EMPTY_DECLARATION_PREFIXES / _EMPTY_DECLARATION_WRAPPERS` 集中在模块级；prompt 正文、canonical、golden 均未改（sha `b48e1a84…` 不变）。

**守卫测试**（`test_prompt_template_consistency.py` 新增 4 个）：

| 用例 | 锁定 |
|---|---|
| `test_prompt_empty_section_marker_triggers_carry_forward` | 从 md 正则提取空段写法，断言被判空且触发结转；prompt 改写法而 parser 未跟进即变红 |
| `test_empty_section_detection_does_not_eat_substantive_content` | 中英文空声明 vs 实质内容对照 |
| `test_english_empty_prefix_does_not_swallow_real_content` | 归一剥掉 `- `/括号后，英文前缀不得吞掉带实质指令的长句 |
| `test_legacy_english_empty_phrases_still_detected` | 既有英文空声明长写法不得因本次修复丢失判空能力 |

**独立复核裁决**（两轮 fresh-eyes verifier，均带「改坏→变红→恢复→转绿」证伪）：

- 采纳：首版归一让 `startswith("no pending")` 暴露，把 `- No pending appeal: refile by Friday` 误判为空并注入过时待办；第二版 `fullmatch` 正则删掉了既有前缀能力（8 类纯英文空声明回退）且 `\s`/`\w` 跨行匹配未真正整句锚定。两处均已按上表设计修正并补守卫。
- 不采纳：verifier 建议扩词表覆盖「暂无/无待办/没有/暂缺/（空）/—」等中文变体。prompt 明文规定「（无）」，这些属契约外臆测变体且无生产观测数据；按「先度量再造机制」，未度量就扩词表会扩大误伤面。记录为已知限制。

**已知限制（如实记录，不假装修复）**：

1. 空段声明以 prompt 字面契约为准；`carry_forward_missing_sections` docstring 的「does NOT depend on LLM behaviour」指**结转动作确定性**，「判空」仍依赖 LLM 遵守 prompt 写法。若线上观测到其它空段写法，按度量结果补词表，不预先堆枚举。
2. 旧 `startswith` 对「空声明 + 换行/中文追加实质内容」（如 `no pending\n调整降糖药剂量`）判空，属 PR0 之前既有行为，非本次引入；本次不扩范围修它。
3. `history_compaction.py:322` 仅在 `anchored != summary` 时打日志，未结转场景无告警/埋点；如需观测性另立议题。

**门禁**：compression 子集 `161 passed, 1 skipped / 162 collected`；全量 backend `1197 passed, 1 skipped`；`ruff check` 对改动源码 clean（忽略该测试文件既有 ANN/SIM102 风格项）；golden 三层逐字节一致；旧常量零残留；全案例探针 0 BAD。

