# 05 — 记忆系统

> 最后修订：2026-11-03（对齐记忆体系重构 SDD v1.4 实施结果，见 `docs/architecture/sdd-memory-refactor.md`）

## 职责概述

记忆系统负责 Agent 长期知识的写入仲裁、自动提取、归因追踪与结构化检索。四层存储的权威介质（与旧文档的差异以本表为准）：

| 层 | 职责 | 介质 |
|---|---|---|
| L1 | 工作索引：结构化预览 + 淘汰；非内容缓存 | Redis hash（`prismclaw:memory:L1:{session_key}`） |
| L2 | 事实层（声明性知识、偏好、约束） | PostgreSQL `pc_memory_entries`（layer='L2'） |
| L3 | 过程层（SOP、工作流、可复用流程） | PostgreSQL `pc_memory_entries`（layer='L3'） |
| L4 | 归档层（会话归档 + pattern/graph/relation 制品） | PostgreSQL `pc_memory_l4_artifacts` + `pc_memory_edges` |

核心能力：
- **写入仲裁**（B2）：五态决策门控（ADD/UPDATE/SUPERSEDE/CONTEST/NOOP），`gateway_mode` 缺省 off
- **自动提取**（A4）：纯聊天回合的事实自动抽取，零工具调用
- **写入质量控制**（D7）：type 受控词表归一、confidence/origin_stage 确定性补齐、entry_id 回传
- **归因追踪**（B1）：注入记账 + use_count 回写 + 用户纠错检测（`attribution_enabled=True` 时）
- **验证信号影子闭环**（D9/T1-5）：`attribution_enabled=False`（生产默认）时由 `ValidationSignalLedger` 产出 task-outcome 信号 + use_count 回写，curator 毕业不依赖归因开关
- **检索降级**（D2）：超时/异常降级为确定性启发式 fallback（500ms 内超时、去零命中、连续失败熔断）
- **L1 结构化预览**（D6）：`[type|subject|age] tail… (id=<8位短id>)`，短 id 支持模型直接 archive

## 架构总览

**[查看交互式架构图](../diagrams/05-memory-system.html)**

```
┌─────────────────────────────────────────────────────────────────┐
│                        记忆系统总览                              │
│                                                                 │
│   ┌──────────────────┐           ┌──────────────────────┐      │
│   │  MemoryWrite     │  五态仲裁 │                      │      │
│   │  Gateway  (B2)   │──────────→│     MemoryStore      │      │
│   │  (mode=off 默认) │           │  L1 Redis / L2-L4 PG │      │
│   └──────────────────┘           └──────────┬───────────┘      │
│                                             │                   │
│   ┌──────────────────┐  type 词表归一       │                   │
│   │  MemoryTool      │────────────────────→│                   │
│   │  save/archive    │  confidence/来源补齐 │                   │
│   └──────────────────┘                      │                   │
│                                             │                   │
│   ┌──────────────────┐  会话归档 + demote    │                   │
│   │  Archive         │────────────────────→│                   │
│   │  (L4 收敛)       │                      │                   │
│   └──────────────────┘                      │                   │
│                                             │                   │
│   ┌──────────────────┐  use_count/验证信号   │                   │
│   │ AttributionRec.  │←────────────────────│                   │
│   │ ValidationLedger │ （D9 影子闭环）      │                   │
│   └──────────────────┘                      │                   │
│                                             │                   │
│                    检索管线（含降级）        │                   │
│   ┌──────────────────────────────────────┐  │                   │
│   │ MemoryQueryPlanner → MemoryRetriever │──┼────────────→      │
│   │  graph 能力探测    → 超时/异常 fallback│  │                   │
│   │ MemorySelector(8s cap→heuristic)     │  │                   │
│   │ MemoryFormatter → XML 段              │  │                   │
│   └──────────────────────────────────────┘  │                   │
└─────────────────────────────────────────────────────────────────┘
```

## 核心组件

### MemoryWriteGateway（`engine/memory/gateway.py`）

写入仲裁器，所有记忆写入必须经过此网关（`gateway_mode` 缺省 `off` = 直写）。

- **类**：`MemoryWriteGateway`
- **核心方法**：`submit()` / `_arbitrate_locked()` / `_find_neighbors()` / `_llm_arbitrate()` / `_execute()` / `_audit()`
- **枚举**：`GatewayDecision`（ADD / UPDATE / SUPERSEDE / CONTEST / NOOP）
- **数据类**：`GatewayResult`、`GatewayLockError`
- **关键常量**：`_GATEWAY_LOCK_TTL_MS = 30_000`；`_CONTEST_CONFIDENCE = 0.3`
- **模式**：off / shadow / active；SUPERSEDE 需 `gateway_supersede_active` 才生效；Fail-open：LLM 失败时默认 ADD

### MemoryTool（`engine/toolkit/memory/memory_tools.py`）

模型侧唯一记忆写入/归档入口（action=save/archive）。

- **写入质量控制（D7）**：type 走受控词表（`schema/memory/classify.py`：`CANONICAL_TYPES` = fact/preference/constraint/procedure/observation；`normalize_type()` 归一 facts→fact、procedures/process/workflow/auto_sop→procedure、general→fact、patient_medical_fact→fact 等），未知词拒绝；confidence=0.7、origin_stage=`memorize_tool` 确定性补齐；save 返回 8 位短 id
- **守卫（D8）**：`_has_non_error_tool_use` 只认 `MessageEntry.is_error == False` 的工具执行
- **archive**：按 entry_id（8 位短 id <=> 完整 id 前缀匹配）下架条目

### ChatCapture（`engine/memory/capture.py`）

纯聊天回合的事实自动提取器。产出经 `normalize_type()` 归一到受控词表再入库。

- **核心函数**：`build_extraction_prompt()` / `parse_facts()` / `extract_chat_facts()` / `store_chat_facts()`
- **关键常量**：`MIN_CHAT_TURNS_FOR_EXTRACTION = 3`；`SHORT_TERM_TTL_SECONDS = 7*24*3600`；`AUTO_EXTRACTION_CONFIDENCE = 0.5`
- **时间范围**：permanent / short_term / recurring；**origin_stage**：`auto_chat_extraction`

### Archive（`engine/memory/archive.py`）

会话归档：`archive_session_background()` 在会话 consolidate 时触发（`runtime/memory_aftermath.py`）。

- **归档收敛（D15/T2-1）**：写 L4 artifact 后 `demote_entries=True`（默认）将该会话 `source_session_id` 匹配的 active 条目置 archived——检索改由 L4 制品补位（粒度变粗是预期取舍）
- **核心函数**：`archive_session_background()` / `_llm_archive_summary()` / `_simple_summary()`；常量 `MIN_USER_MESSAGES_FOR_ARCHIVE = 3`

### AttributionRecorder（`engine/memory/attribution.py`）

记忆注入归因追踪器，**仅在 `attribution_enabled=True` 时装配**（`runner_assembly.py`）。

- **核心方法**：`record_injection()` / `mark_turn_complete()` / `last_turn_injections()` / `pending_entry_ids()` / `record_validation_events()` / `build_validation_event()`
- **函数**：`detect_user_correction()`（`_NEGATION_MARKERS` / `_MIN_SHARED_TOKENS = 2` / `_MIN_OVERLAP_RATIO = 0.2`，jieba 分词）

### ValidationSignalLedger（`engine/memory/validation_signals.py`，D9/T1-5 新增）

无归因环境的最小验证信号簿记，**独立于 `attribution_enabled`**，生产默认配置下无条件装配：

- `note_injections(session_id, entry_ids)`：retriever 注入成功后登记（去重保序）
- `take_pending(session_id)`：aftermath 回合尾取走（取走即清）
- `record_events(session_key, events)` / `bump_use_counts(session_key, entry_ids)`：写 `pc_memory_validation_events` + 回写 use_count
- 分层边界：不含 reference log / 纠错检测（那是 AttributionRecorder 的职责）

### MemoryQueryPlanner（`engine/compression/memory_query_planner.py`）

- **核心函数**：`build_evidence_strategy()`（新增 `graph_available` 参数，缺省 False）
- **能力探测（D10）**：graph 扩展由 `memory_graph_enabled` 真实能力决定，开关关闭时策略不再谎报 enabled
- **策略类型**：procedure_first / fact_episode_first / balanced；TemporalMode：current_only / include_history

### MemoryRetriever（`engine/compression/memory_retriever.py`）

- **核心方法**：`retrieve_evidence()` / `build_evidence_strategy()` / `get_l1_snapshot()` / `after_turn()` / `consolidate_session()` / `_expand_via_edges()`（E7）
- **检索预算双路径**：交互 3s（禁 LLM selector）/ 非交互 20s（启 LLM selector）
- **降级（D2）**：`TimeoutError→timeout_fallback` / 非超时异常→`error_fallback` / 连续 3 次失败→熔断 300s 直走 fallback；`_heuristic_fallback()` 零 LLM/向量/BM25：扫 active 条目（≤500）关键词交集打分（去零 + 30 天衰减微调），500ms 独立短超时
- **影子记账（T1-8）**：注入后 emit `memory_shadow_use_count` + ledger 登记
- **协议**：`MemoryRetrievalSettings`（search_l2_quota / search_l3_quota / archive_max_results / archive_enabled / archive_min_similarity / memory_graph_enabled）

### MemorySelector（`engine/compression/memory_selector.py`）

- **LLM selector 预算（D16）**：`_SELECTOR_LIMIT_S = 8.0`，`asyncio.wait_for` 包装（接口无 timeout 参数；取消后服务端费用照计为已知代价），超限走 `heuristic_timeout` 兜底
- **启发式两阶段排序（D5）**：检索序 → relevance band（rank≤3→3 / ≤10→2 / ≤20→1 / 其余→0）为首位；层级 bias 仅同带内 tie-break
- **Ranking v2（B4）**：`_composite_scores()` = relevance × decay × use_factor × confidence（`ranking_v2_enabled=True` 时）；abstention shadow/hard

### MemoryFormatter（`engine/compression/memory_formatter.py`）

- **XML 结构**：`<memory_facts>` / `<memory_procedures>` / `<memory_archives>`
- **优先级**：memory_facts=high、memory_archives=low
- 输出 EvidencePacket 包装为动态 ContextBlock（cacheable=False）

## 关键数据流

### 1. 写入流

```
MemoryTool.save()                               ChatCapture
  → type 归一（词表拒绝未知词）                   → store_chat_facts()
  → confidence/origin_stage 补齐                  → Gateway（mode=off 直写）
  → Gateway.submit()（off=直写）                 → L2/L3 store
  → L2/L3 store → L1 结构化预览镜像
    （supersedes/confidence 全字段保真；淘汰：superseded→低置信→时间）
```

### 2. 检索流（含降级）

```
用户查询 → QueryPlanner.build_evidence_strategy(graph_available=…)
         → RetrieveEvidence(3s 交互 / 20s 完整)
         │ 正常：L2/L3 双路搜索（向量腿可选；未注入 embedding 时纯 BM25）
         │       → Selector（8s LLM / heuristic band 排序）
         │ 超时/异常/熔断：_heuristic_fallback（确定性，500ms 内）
         → Formatter.format_blocks_from_packets() → 动态内存段
```

### 3. 归因与验证信号流（attribution_enabled 双路径）

```
attribution_enabled=True：
  注入 → AttributionRecorder.record_injection()
       → mark_turn_complete → use_count 回写 + 纠错检测
attribution_enabled=False（默认）：
  注入 → ValidationSignalLedger.note_injections()
       → aftermath 回合尾 take_pending + 扫描本回合工具轨迹
         （有工具报错→negative；成功工具+回复→positive；纯聊天→无信号）
       → record_events + bump_use_counts
  → curator 毕业双路径（use_count OR positive≥2 且 negative==0）
```

### 4. 归档流（L4）

```
会话 consolidate → archive_session_background()
  → LLM/简单摘要 → store_l4_artifact
  → demote 会话内 active 条目（status=archived）
  → L4 search_l4_artifacts 补位检索
```

## 对外接口

| 接口 | 来源 | 说明 |
|------|------|------|
| `MemoryWriteGateway.submit()` | `gateway.py` | 记忆写入仲裁 |
| `MemoryTool.execute(action=save/archive)` | `memory_tools.py` | 模型侧记忆写入/归档 |
| `normalize_type()` / `CANONICAL_TYPES` | `classify.py` | type 受控词表 |
| `ValidationSignalLedger.note_injections()` | `validation_signals.py` | 无归因注入簿记 |
| `archive_session_background()` | `archive.py` | 会话归档（含 demote） |
| `MemoryRetriever.retrieve_evidence()` | `memory_retriever.py` | 证据包检索（含降级） |
| `select_relevant_memories()` | `memory_selector.py` | 记忆选择（8s cap） |
| `build_evidence_strategy()` | `memory_query_planner.py` | 查询策略（graph 探测） |
| `format_l1_snapshot()` | `_static_context_types.py` | L1 索引渲染（15 行上限） |

## 依赖关系

- **Redis**：L1 索引（hash + TTL 30d）、Gateway 锁
- **PostgreSQL**：L2/L3 条目（含 bm25vector 全文索引；embedding 列仅当启用向量腿时使用）
- **LLM Client**：Gateway 仲裁、ChatCapture 提取、Selector 选择
- **EdgeStore**：E7 边扩展（`memory_graph_enabled` 门控）

## 配置项与关键常量（本次重组新增/变更）

| 常量 | 默认值 | 位置 | 说明 |
|------|--------|------|------|
| `_L1_PREVIEW_TAIL_TOKENS` / `_PREVIEW_SUBJECT_TOKENS` | 48 / 40 | `composite.py` | L1 结构化预览（含短 id 尾注） |
| `MAX_L1_SNAPSHOT_LINES` | 15 | `_static_context_types.py` | L1 注入上限（超出附 trailer） |
| `_FALLBACK_FETCH_TIMEOUT_S` / `_FALLBACK_MAX_RESULTS` | 0.5 / 8 | `memory_retriever.py` | 降级 fallback 短超时与硬顶 |
| `_RETRIEVAL_CIRCUIT_FAILURES` / `_RETRIEVAL_CIRCUIT_COOLDOWN_S` | 3 / 300 | `memory_retriever.py` | 检索熔断 |
| `_SELECTOR_LIMIT_S` | 8.0 | `memory_selector.py` | LLM selector 单次预算 |
| `_SUBAGENTS_CATALOG_TOKEN_BUDGET` | 1500 | `_catalog_builder.py` | subagents 目录 token 预算 |
| `_GATEWAY_LOCK_TTL_MS` | 30000 | `gateway.py` | Redis 锁 TTL |
| `ARCHIVE_CACHE_MAX_ENTRIES` | 200 | `memory_retriever.py` | 归档缓存 |
| `_DECAY_LAMBDA_SHORT_TERM/DEFAULT/DECISION` | 0.05/0.005/0.0 | `memory_selector.py` | 衰减率 |

**配置开关现状（D14 未决，缺省全关）**：`attribution_enabled=False`、`gateway_mode=off`、`ranking_v2_enabled=False`、`memory_graph_enabled=False`、`notary_enabled=False`、`memory_forgetting_budget_entries=0`。