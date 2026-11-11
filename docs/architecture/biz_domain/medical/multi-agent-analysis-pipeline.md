# 多智能体分析管线架构

> 版本：v1.0 · 2026-09-08
> 定位：开工级设计——表、接口、文件路径、调用链路、降级策略均写到可编码程度。

## 1. 全景

解析管线（上传 → 文本抽取 → 元数据/结构化 → 患者归属 → 已解析）产出了结构化数据，
但结构化 ≠ 洞察。医生需要的是**可溯源的研究简报**：每个结论都能追到原文或知识库条目，
推测性假设被围栏隔离，审计闸门机械验证后才能落库。

```
上传 ─→ 解析管线（已有）──→ 已解析 ─→ 分析管线（本文）──→ 研究简报
                                                     │
                                                     ▼
                                              审计闸门（机械）
                                                     │
                                               通过 / 打回
```

分析管线不替代解析管线——它消费解析的产出物（结构化数据 + 原文），产出临床洞察。

## 2. 编排模型

### 2.1 为什么不复用核心 SubAgentExecutor

| 维度 | SubAgentExecutor | 医疗分析管线 |
|------|-----------------|-------------|
| 驱动 | 聊天会话 ToolContext | BackgroundTasks 后台任务 |
| 会话 | 绑定 SessionTree | 无会话，report_id 是主键 |
| 工具 | 核心 ToolRegistry | 域内纯函数 + complete_json |
| 并行 | ParallelTaskStore + session 内 | asyncio.gather + 信号量 |
| 上下文 | SubagentContextSnapshot | 分析上下文自建（见 §3） |

**结论：域内自建编排，不复用核心 subagent 运行时。**

核心 subagent 的 `SubagentResultEnvelope`、`EvidenceRef.domain_extensions`、
`SubagentContextSnapshot` 是**数据模型**，可以在域内复用其 schema 定义，
但运行时编排层不依赖核心的 executor / parallel / session 体系。

### 2.2 编排流程

```
主任规划器（1 次 LLM 调用）
    │
    ├─→ Wave 1：知识检索（并行 2 路）
    │     ├─ local_kb_retrieval    本地 KB 文献/指南/药品
    │     └─ patient_archive       该患者历史报告聚合
    │
    ├─→ Wave 2：专家分析（并行 4 路，信号量控制并发 ≤ 2）
    │     ├─ molecular_analysis    分子检测/生物标志物解读
    │     ├─ drug_review           用药审查 + 相互作用
    │     ├─ evidence_grading      证据分级 + 指南匹配
    │     └─ data_trend            检验趋势 + 数值变化
    │
    ├─→ Wave 3：探索性假设（串行，围栏隔离）
    │     └─ hypothesis_exploration 推测性假设（必须标注 [SPECULATIVE]）
    │
    ├─→ 合成器（1 次 LLM 调用，把各路结果拼成简报草稿）
    │
    └─→ 审计闸门（纯程序，0 次 LLM）
          ├─ citation_existence     引用是否指向真实条目
          ├─ quote_verifiability    引文能否在原文中定位
          └─ speculation_fencing    推测是否被围栏隔离
```

### 2.3 并发与容错

```python
# services/analysis_pipeline.py 核心骨架

import asyncio
from asyncio import Semaphore

_SEMAPHORE = Semaphore(2)   # 最多 2 路并行 LLM 调用

async def _run_with_semaphore(coro):
    async with _SEMAPHORE:
        return await coro

async def run_analysis_pipeline(store, brief_id, *, llm, vision):
    # 主任规划
    plan = await director_plan(llm, context)

    # Wave 1 + Wave 2 并行（但通过信号量控制实际并发）
    wave1_tasks = [
        _run_with_semaphore(local_kb_retrieval(llm, kb_store, plan)),
        _run_with_semaphore(patient_archive(store, plan)),
    ]
    wave1_results = await asyncio.gather(*wave1_tasks, return_exceptions=True)

    # Wave 1 失败降级：KB 不可用 → 空结果；归档不可用 → 仅当前报告
    wave1 = _handle_wave_failures(wave1_results, wave="1")

    wave2_tasks = [
        _run_with_semaphore(molecular_analysis(llm, wave1, plan)),
        _run_with_semaphore(drug_review(llm, wave1, plan)),
        _run_with_semaphore(evidence_grading(llm, wave1, plan)),
        _run_with_semaphore(data_trend(llm, wave1, plan)),
    ]
    wave2_results = await asyncio.gather(*wave2_tasks, return_exceptions=True)
    wave2 = _handle_wave_failures(wave2_results, wave="2")

    # Wave 3：探索性假设（串行，围栏隔离）
    wave3 = await hypothesis_exploration(llm, wave1, wave2, plan)

    # 合成简报草稿
    draft = await synthesize_brief(llm, wave1, wave2, wave3, plan)

    # 审计闸门
    audit_result = audit_gate(draft, source_data)
    if not audit_result.passed:
        # 打回：记录失败原因，简报状态 = 待修正
        await _audit_fail(store, brief_id, audit_result)
        return

    # 通过：落库
    await _audit_pass(store, brief_id, draft, audit_result)
```

**降级策略**：
- Wave 1 任一路失败 → 该路结果为空，其余路照常
- Wave 2 任一路失败 → 该领域分析为空，简报中标注"该领域分析暂缺"
- Wave 3 失败 → 不产出探索性假设，不影响简报
- LLM 完全不可用 → 整条分析标记 `失败`，error_message 记录

## 3. 数据模型

### 3.1 新增表：pc_medical_briefs

```sql
CREATE TABLE IF NOT EXISTS pc_medical_briefs (
    id                VARCHAR(64)  PRIMARY KEY,
    patient_id        VARCHAR(64)  NOT NULL,
    report_ids_json   JSONB        DEFAULT '[]',   -- 消费的报告 ID 列表
    status            VARCHAR(16)  DEFAULT '分析中', -- 分析中/已通过/待修正/已废弃
    version           SMALLINT     DEFAULT 1,       -- 重新分析递增
    director_plan_json JSONB       DEFAULT '{}',    -- 主任规划输出
    sections_json     JSONB        DEFAULT '[]',    -- 简报正文（含引用）
    citations_json    JSONB        DEFAULT '[]',    -- 统一引用表
    audit_result_json JSONB        DEFAULT '{}',    -- 审计结果
    error_message     TEXT         DEFAULT '',
    started_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ,
    create_at         TIMESTAMPTZ  DEFAULT NOW(),
    create_by         VARCHAR(64)  DEFAULT 'default',
    update_at         TIMESTAMPTZ  DEFAULT NOW(),
    update_by         VARCHAR(64)  DEFAULT 'default',
    is_deleted        SMALLINT     DEFAULT 0
);

CREATE INDEX IF NOT EXISTS ix_pc_medical_briefs_patient_id
    ON pc_medical_briefs (patient_id);
CREATE INDEX IF NOT EXISTS ix_pc_medical_briefs_status
    ON pc_medical_briefs (status);
CREATE INDEX IF NOT EXISTS ix_pc_medical_briefs_is_deleted
    ON pc_medical_briefs (is_deleted);
```

### 3.2 统一引用结构（citations_json 内的元素）

```json
{
  "citation_id": "cit:a1b2c3",
  "source_type": "report_section",   // report_section | kb_literature | kb_guideline | kb_drug | speculative
  "source_id": "report-uuid",        // 对应表的 id
  "section_heading": "检查所见",      // report_section 时必填
  "quoted_text": "原发灶位于右肺上叶…", // 原文摘录（≤200 字）
  "confidence": "high"               // high | medium | low
}
```

`source_type=speculative` 的条目必须出现在 `sections_json` 中被 `[SPECULATIVE]` 围栏包裹的段落内。

### 3.3 简报段落结构（sections_json 内的元素）

```json
{
  "heading": "分子检测解读",
  "body": "EGFR L858R 突变阳性（cit:a1b2c3）。建议一线奥希替尼（cit:d4e5f6）。",
  "citation_ids": ["cit:a1b2c3", "cit:d4e5f6"],
  "is_speculative": false
}
```

`is_speculative=true` 的段落审计闸门会验证其所有 citation 的 `source_type=speculative`。

## 4. 文件布局

```
src/subpackages.medical/
├── domain/
│   ├── analysis/                      # 新增：纯函数层（0 IO）
│   │   ├── __init__.py
│   │   ├── prompts.py                 # 主任/专家/合成 prompt + tool schema
│   │   ├── synthesis.py              # 简报合成逻辑（纯函数）
│   │   └── audit_rules.py            # 审计规则（纯函数，0 LLM）
│   └── parsing/                       # 已有：解析纯函数层
│       ├── extract.py
│       ├── match.py
│       └── prompts.py
├── services/
│   ├── analysis_pipeline.py           # 新增：分析管线编排（IO 层）
│   ├── parse_pipeline.py              # 已有：解析管线编排
│   └── record_service.py
├── persistence/
│   ├── brief_store.py                 # 新增：pc_medical_briefs CRUD
│   ├── medical_store.py               # 已有
│   └── kb_store.py                    # 已有
├── api/
│   ├── briefs.py                      # 新增：简报 REST API
│   ├── records.py                     # 已有
│   └── ...
├── models/
│   ├── tables.py                      # 新增 briefs 表定义
│   ├── enums.py                       # 新增 BriefStatus 枚举
│   └── view_models.py
├── integration/
│   ├── bundle.py                      # 新增 briefs 路由 + brief_store 注册
│   └── llm_access.py                  # 已有
└── ...
```

**分层纪律**（与解析管线一致）：
- `domain/analysis/`：纯函数，0 IO，0 LLM 直接调用。prompt 构造、输出归一化、审计规则。
- `services/analysis_pipeline.py`：编排层，唯一调用 LLM（通过 `complete_json`）和 Store 的地方。
- `integration/llm_access.py`：域与核心 LLM 基建的唯一接触面——不增加新入口。

## 5. 域纯函数层详解

### 5.1 domain/analysis/prompts.py

```
5 个 prompt + 5 个 tool schema + 5 个 build_*_prompt 函数 + 归一化函数

1. DIRECTOR_SYSTEM / DIRECTOR_TOOL
   输入：报告元数据 + 结构化摘要 + 患者画像
   输出：{ focus_areas: [...], retrieval_queries: [...], analysis_priorities: [...] }

2. KB_RETRIEVAL_SYSTEM / KB_RETRIEVAL_TOOL
   输入：retrieval_queries
   输出：{ hits: [{ source_type, source_id, title, relevance, snippet }] }

3. EXPERT_ANALYSIS_SYSTEM / EXPERT_ANALYSIS_TOOL
   输入：规划 + 检索结果 + 报告数据（每个专家的 prompt 不同，system 部分区分角色）
   输出：{ findings: [...], citations: [...], confidence }

4. HYPOTHESIS_SYSTEM / HYPOTHESIS_TOOL
   输入：Wave1+2 结果
   输出：{ hypotheses: [...], each must have citation with source_type=speculative }

5. SYNTHESIS_SYSTEM / SYNTHESIS_TOOL
   输入：全部 Wave 结果
   输出：{ sections: [...], citations: [...] }  — 最终简报
```

归一化函数职责（与 `normalize_metadata` / `normalize_structure` 一致）：
- `normalize_director_plan(raw) → dict`：收敛 focus_areas 为枚举子集
- `normalize_expert_findings(raw, expert_role) → dict`：裁剪列表长度、类型矫正
- `normalize_brief(raw) → dict`：段落上限、引用 ID 格式校验

### 5.2 domain/analysis/audit_rules.py

纯函数，0 LLM。三个规则各一个函数：

```python
def check_citation_existence(
    citations: list[dict],
    *,
    report_ids: set[str],
    kb_ids: set[str],
) -> list[AuditViolation]:
    """引用的 source_id 是否在输入数据中存在。
    
    report_section 类型 → source_id 必须在 report_ids 中
    kb_* 类型 → source_id 必须在 kb_ids 中
    speculative → 不检查存在性
    """

def check_quote_verifiability(
    citations: list[dict],
    *,
    raw_texts: dict[str, str],   # report_id → raw_text
) -> list[AuditViolation]:
    """quoted_text 是否能在原始文本中定位。
    
    截取 quoted_text 前 30 字，在 raw_text 中搜索。
    找不到 → violation（但不阻断，降级为"引用待验证"标记）。
    """

def check_speculation_fencing(
    sections: list[dict],
    citations: list[dict],
) -> list[AuditViolation]:
    """推测性假设是否被围栏隔离。
    
    is_speculative=true 的段落 → 其所有 citation 的 source_type 必须是 speculative
    source_type=speculative 的 citation → 必须只出现在 is_speculative=true 的段落中
    """

def run_audit(
    sections: list[dict],
    citations: list[dict],
    *,
    report_ids: set[str],
    kb_ids: set[str],
    raw_texts: dict[str, str],
) -> AuditResult:
    """运行全部审计规则，返回 AuditResult(passed, violations)。"""
```

`AuditResult`:
```python
@dataclass
class AuditViolation:
    rule: str          # citation_existence | quote_verifiability | speculation_fencing
    citation_id: str
    detail: str

@dataclass
class AuditResult:
    passed: bool
    violations: list[AuditViolation]
```

审计 **阻断** 条件（任一满足则 `passed=False`）：
- citation_existence 发现 1+ 条引用指向不存在的 source_id
- speculation_fencing 发现 1+ 条推测性引用出现在非推测段落中

审计 **降级** 条件（passed 仍为 True，但 violation 记入 audit_result_json）：
- quote_verifiability 发现引用原文定位失败 → 标记为"引用待验证"

## 6. 服务层编排详解

### 6.1 services/analysis_pipeline.py

```python
async def run_analysis_pipeline(
    store: MedicalStore,
    brief_store: BriefStore,
    kb_store: KbStore,
    brief_id: str,
    *,
    llm: Any | None = None,
    vision: Any | None = None,
) -> None:
    """推进简报到终态。与 parse_pipeline 同构：try/except → _fail。"""
```

**状态机**：
```
分析中(10) → 主任规划(20) → 知识检索(40) → 专家分析(70) → 探索假设(80) → 合成(90)
    → 审计(95) → 已通过(100) / 待修正(100)
```

**输入准备**（从 Store 取数据，不传 LLM 结果）：
1. 取 brief 对应的 report_ids
2. 逐报告取 raw_text + structured data
3. 取患者画像
4. 取 KB 候选集（用 retrieval_queries 查 kb_store）

**各阶段调用链**：

| 阶段 | 纯函数 | IO 函数 |
|------|--------|---------|
| 主任规划 | `build_director_prompt` | `complete_json(llm, ...)` → `normalize_director_plan` |
| 知识检索 | `build_kb_retrieval_prompt` | `complete_json` + `kb_store.list_*` |
| 专家分析 | `build_expert_prompt(role, ...)` | `complete_json` per role → `normalize_expert_findings` |
| 探索假设 | `build_hypothesis_prompt` | `complete_json` → 归一化 |
| 合成 | `build_synthesis_prompt` | `complete_json` → `normalize_brief` |
| 审计 | `run_audit` | 0 LLM，纯 Store 读取 |

### 6.2 触发时机

两种触发方式：

1. **自动**：解析管线 `_finish` 时，如果 `patient_id` 不为空（即已归档），
   检查该患者是否有待分析的报告 → 自动创建 brief 并触发分析。

2. **手动**：医生在患者详情页点击"生成研究简报"→ POST `/api/medical/briefs`。

### 6.3 与解析管线的衔接

解析管线的 `_finish` 增加一步：调用 `_maybe_trigger_analysis`。

```python
# services/parse_pipeline.py 末尾追加

async def _maybe_trigger_analysis(
    store: MedicalStore,
    brief_store: BriefStore,
    kb_store: KbStore,
    patient_id: str,
    *,
    llm: Any | None,
    vision: Any | None,
) -> None:
    """归档后检查：该患者是否有未分析的报告，有则创建 brief 并触发分析。"""
    # 查该患者所有已解析且未分析的报告
    unanalyzed = await brief_store.list_unanalyzed_reports(patient_id)
    if not unanalyzed:
        return
    brief_id = str(uuid.uuid4())
    await brief_store.create_brief(brief_id, patient_id, unanalyzed, user_id="system")
    # BackgroundTasks 已在 records.py 的 upload 路由里处理，
    # 这里直接 await 分析管线（因为在同一个 BackgroundTask 内）
    await run_analysis_pipeline(store, brief_store, kb_store, brief_id, llm=llm, vision=vision)
```

**但** `parse_pipeline` 运行在 BackgroundTasks 内，request 已死。
`brief_store` 和 `kb_store` 需要在 `app.state` 上获取（通过 engine）。

更安全的做法：不在 `_finish` 里直接调用分析管线，而是在 `records.py` 的 upload 路由里，
`parse_pipeline` 完成后再 `add_task(analysis_pipeline, ...)`。

### 6.4 429 与重试

- `complete_json` 内已有 2 次重试（temp 0 → 0.2）
- 分析管线在此基础上增加**阶段级重试**：`_advance` 时记录当前阶段，
  失败后可以从该阶段恢复而非从头开始
- 429 特殊处理：捕获 `RateLimitError`，写入 `error_message="LLM 频率限制，请稍后重试"`，
  状态设为 `待修正`（不是 `失败`，因为不是业务错误）

## 7. REST API

### 7.1 新增路由：api/briefs.py

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 列出简报（分页，按 patient_id 过滤） |
| GET | `/{brief_id}` | 简报详情（含 sections + citations） |
| POST | `/` | 手动创建简报（指定 patient_id + report_ids） |
| POST | `/{brief_id}/reanalyze` | 重新分析（version+1） |
| DELETE | `/{brief_id}` | 逻辑删除 |

**POST `/` 请求体**：
```json
{
  "patient_id": "P-MENG",
  "report_ids": ["uuid-1", "uuid-2"]   // 可选，为空则取全部已解析报告
}
```

**POST `/{brief_id}/reanalyze`**：
- 检查当前状态不是 `分析中`（防重入）
- version + 1
- 将旧版本 is_deleted=1（或保留，前端可展示版本历史）
- 触发 `run_analysis_pipeline`

### 7.2 修改路由：api/records.py

upload 路由的 BackgroundTasks：`parse_pipeline` 完成后追加 `analysis_pipeline`。

## 8. 前端集成

### 8.1 新增组件（frontend/subpackages/medical/）

```
src/
├── components/
│   ├── BriefCard.tsx           # 简报卡片（列表用）
│   ├── BriefView.tsx           # 简报详情（段落后含引用标记）
│   └── CitationPopover.tsx     # 引用弹出层（点 [cit:xxx] 显示原文）
└── pages/
    └── MedicalBriefsPage.tsx   # 简报列表页（可从患者详情页跳入）
```

### 8.2 患者详情页集成

- 右栏（咨询区）上方增加"研究简报"Tab
- 点击展示 `BriefView`，引用标记可点
- 简报状态徽标：分析中（蓝色 ProgressRing）/ 已通过（绿色✓）/ 待修正（橙色⚠）

### 8.3 轮询

与解析管线一致：`setInterval(4s)` 轮询 `GET /api/medical/briefs/{id}`，
直到状态为终态（已通过/待修正）或 120s 超时。

## 9. 核心零改动验证

| 改动 | 是否涉及核心 | 判据 |
|------|-------------|------|
| 新增 briefs 表 + BriefStore | 否 | 域内 persistence |
| 新增 domain/analysis/ | 否 | 纯函数层 |
| 新增 analysis_pipeline.py | 否 | 域内 services |
| 新增 api/briefs.py | 否 | 域内路由，bundle 注册 |
| 新增 BriefStatus 枚举 | 否 | 域内 models |
| LLM 调用 | 否 | 复用 integration/llm_access.py |
| EvidenceRef.domain_extensions | 否（已有） | 复用核心 schema |
| parse_pipeline 追加分析触发 | 否 | 域内 services 修改 |
| records.py 追加 background_task | 否 | 域内 API 修改 |

**结论：核心 0 改动。** 所有新代码均在 `subpackages.medical` 包内。

## 10. 实施顺序

| 步骤 | 文件 | 说明 |
|------|------|------|
| 1 | `models/tables.py` | 新增 briefs 表 + BRIEF_COLUMNS |
| 1 | `models/enums.py` | 新增 BriefStatus |
| 1 | `persistence/schema.py` | 新增 DDL |
| 2 | `persistence/brief_store.py` | 新建，CRUD + list_unanalyzed_reports |
| 3 | `domain/analysis/prompts.py` | 新建，5 组 prompt + tool schema + 归一化 |
| 3 | `domain/analysis/audit_rules.py` | 新建，3 条审计规则 |
| 3 | `domain/analysis/synthesis.py` | 新建，简报合成辅助 |
| 4 | `services/analysis_pipeline.py` | 新建，编排层 |
| 5 | `api/briefs.py` | 新建，5 个端点 |
| 5 | `integration/bundle.py` | 注册 brief_store + briefs 路由 |
| 6 | `services/parse_pipeline.py` | 追加 `_maybe_trigger_analysis` |
| 6 | `api/records.py` | upload 追加分析 background_task |
| 7 | 前端组件 | BriefCard / BriefView / CitationPopover |
| 7 | 前端页面 | MedicalBriefsPage + 患者详情页简报 Tab |

## 11. LLM Token 预算

| 阶段 | 输入估算 | 输出估算 | 调用次数 |
|------|---------|---------|---------|
| 主任规划 | ~2K tokens | ~500 | 1 |
| 知识检索 | ~1.5K × 2 | ~800 × 2 | 2 |
| 专家分析 | ~3K × 4 | ~1K × 4 | 4 |
| 探索假设 | ~4K | ~1K | 1 |
| 合成 | ~6K | ~2K | 1 |
| **合计** | ~26K | ~9.2K | 9 |

信号量控制实际并发 ≤ 2，429 风险可控。

## 12. 与解析管线的降级对称

| 场景 | 解析管线 | 分析管线 |
|------|---------|---------|
| LLM 不可用 | 整条 `失败` | 整条 `待修正`（可重试） |
| 部分阶段失败 | 降级为空，保住原文 | 降级为空，简报标注"该领域暂缺" |
| 完全无数据 | 不可能（有文件才能上传） | 不可能（有已解析报告才能触发） |
| 审计不通过 | N/A | `待修正`，audit_result_json 记录违规 |
