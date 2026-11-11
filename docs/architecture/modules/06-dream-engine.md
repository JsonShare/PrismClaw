# 06 — Dreaming 引擎

> 最后修订：2026-11-03（对齐记忆体系重构 SDD v1.4 实施结果）

## 职责概述

Dreaming 引擎是 Agent 的离线记忆整理与知识沉淀系统。三阶段模型（Light Sleep → Deep Sleep → REM Sleep）在空闲时段对记忆进行去重、合并、降级与归档合成。核心能力：

- **三阶段处理模型**：Light（L1→L2 提升与合并下架）/ Deep（去重+三层合并）/ REM（L4 归档合成+制品发布）
- **Light 合并收敛**（D4）：被合并的原件必须 `archive_entry(reason="light_sleep_merged")` 下架，不得只删 L1 索引制造副本
- **Dream Compiler**：候选提取 → publish gate → merge judge（duplicate/refine/contradict/new）
- **Dream Governance**：Shadow / Canary / Direct 三通道发布 + 审批/拒绝/回滚
- **Dream Dispatch**：Redis Streams 队列 + **消费组自愈（NOGROUP 自动重建 + 30s 冷却防风暴 + 重建审计）** + stale reclaim + dead letter + 指数退避
- **Dream Scheduler**：空闲循环 + 夜间窗口（时区感知）+ 多 worker leader-only 扫描
- **Memory Value Scoring**（E3）：多因子价值评分驱动降级与保留决策
- **SDD B3 三层合并**（`tiers_enabled` 缺省 False，生产走 legacy 路径）：Light（零 LLM 精确去重）/ Mid（验证回写+衰减+CONTEST 退出）/ Deep（证据门控语义合并+纠错学习）

## 架构总览

**[查看交互式架构图](../diagrams/06-dream-engine.html)**

```
┌─────────────────────────────────────────────────────────────────┐
│                     Dreaming 引擎总览                            │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                   DreamScheduler                          │   │
│  │  ┌───────────┐  ┌───────────────┐  ┌─────────────────┐    │   │
│  │  │ Idle Loop │  │ Nightly       │  │ Leader-Only     │    │   │
│  │  └─────┬─────┘  └──────┬────────┘  └────────┬────────┘    │   │
│  └────────┼───────────────┼─────────────────────┼────────────┘   │
│           ▼               ▼                     ▼                │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │             DreamDispatchQueue（Redis Streams）           │   │
│  │  enqueue → read(NOGROUP 自愈) → ack/fail → reclaim       │   │
│  │  → dead_letter；重建审计 event=group_rebuilt{stream_len} │   │
│  └────────────────────────┬──────────────────────────────────┘  │
│                           ▼                                      │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                     DreamEngine                           │   │
│  │  Light：LLM 整合 → 合并件 store + 原件 archive_entry      │   │
│  │  Deep：去重 + 合并；REM：归档合成 + 制品发布              │   │
│  └───────┬──────────────────────────────────────────────────┘   │
│          ▼                                                       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │       DreamCompiler → DreamGovernanceRuntime              │   │
│  │       Shadow / Canary / Direct（审批/拒绝/回滚）          │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## 核心组件

### DreamEngine（`engine/dream/engine.py`）

三阶段 Dream 周期编排器。

- **核心方法**：`trigger_dream_cycle()` / `run_light()` / `run_deep()` / `run_rem()` / `run_nightly()`
- **Light Sleep（D4 修订）**：LLM 整合产出 `(orig_ids, context, fact, type)` → 合并件 `store()` → **原件经 `_cleanup_light_sleep_source_ids` → `archive_entry(reason="light_sleep_merged")` 下架**（fallback/recovered 路径同一语义）
- **`_hydrate_fulltext()`**：候选全文必须从 L2 回读（`get_contents_by_ids`），不喂截断镜像给 LLM；无法解析的候选整体跳过
- **关键常量**：`_DREAM_MAX_SCAN = 200`；`_REM_MAX_ITEMS = 12`；`_REM_FEEDBACK_LIMIT = 3`；`effective_threshold_s = max(light_sleep_threshold_s, 12h)`

### DreamCompiler（`engine/dream/compiler.py`）

- **Publish Gate**：content ≥ 8 字符、confidence ≥ 0.72、procedure steps ≥ 2、evidence ≥ 3
- **合并动作**：duplicate / refine / contradict / new；版本链 `_resolve_version_chain_id()`

### Memory Value Scoring（`engine/dream/scoring.py`）

- `memory_value()` 多因子评分；权重：`_IMPORTANCE_WEIGHTS` L3=3.0/L2=1.5，confidence/stability/importance/recency 各 2.0x
- 衰减：短期 0.05 / 默认 0.005 / 决策 0.0

### DreamCandidateCollector（`engine/dream/candidates.py`）

- `capture_turn()`（最近 4 条）/ `capture_session()`（完整分支）；`_SESSION_SUMMARY_HEAD/TAIL = 1000`

### Dream Tiers（`engine/dream/tiers.py`，`tiers_enabled=False` 时走 legacy）

- Light：零 LLM 精确去重，≥3 次重复 → L3 过程建议；Mid：验证回写 + 衰减 + CONTEST 退出 + E3 降级 + E5 边去活；Deep：证据门控语义合并 + E4 纠错学习 + 死边清理
- 常量：`_DEMOTE_FRACTION = 0.2`、`_CONFIDENCE_FLOOR = 0.05`、`_CONFIDENCE_CEIL = 0.95`

### Dream Publishing / Governance / Review（publishing.py / governance_runtime.py / review_service.py）

- 三通道发布（shadow 不存储 / canary 灰度 / direct 直接）；hash-percent rollout
- **Review 决策（T2-4 修订）**：`awaiting_archive_migration` 分支已随 `pc_memory_archives` 死表移除（backlog 恒 0 的迁移等待语义删除）

### Dream Dispatch（`engine/dream/dispatch/dispatch.py`）

Redis Streams 队列调度系统。

- **消费组自愈（D1）**：`_read_group` 遇 NOGROUP → `_recover_group()`（复位 `_group_ready` + 重建 + 重试一次）；`_group_rebuild_cooldown_s = 30s` 防风暴；重建写审计 `event=group_rebuilt{stream_len}`
- **ack/fail NOGROUP 容忍**：组在 claim 与 ack 之间丢失 → 降级审计（`ack_group_missing`/`fail_group_missing`），任务按 `"0-0"` 重投递幂等重跑（ack 已 xdel 已处理条目，stream 残留即未完成任务）
- **类**：`RedisDreamDispatchQueue`（enqueue/read/ack/fail/reclaim_stale/replay_dead_letter/stats/metrics）、`DreamDispatchConsumer`
- **构建函数**：`build_light_dispatch_job()` / `build_nightly_dispatch_job()`

### Dream Scheduler（`engine/dream/dispatch/scheduler.py`）

- 空闲检测 + 时区感知夜间窗口 + leader-only 扫描；`DreamSessionState`（light_pending / nightly_pending / dream_lock_owner）
- nightly worker → `run_nightly`（deep + REM；include_light=False 缺省）

## 关键数据流

### 1. Light Sleep 流（L1→L2 提升 + 合并下架）

```
DreamEngine.run_light()
  → _light_sleep_impl()（≥12h 间隔）
  → L1 候选 → _hydrate_fulltext（L2 回读，缺失跳过）
  → LLM 整合 [{original_ids, context, consolidated_fact, type}]
  → 合并件 store()（L2='facts' / L3='procedures' + <context> 前缀）
  → 原件 _cleanup_light_sleep_source_ids → archive_entry("light_sleep_merged")
  → LLM 失败 → fallback：候选入 DreamCandidateStore → Compiler 发布
     → 已发布候选的 source ids 同样 archive_entry 下架
```

### 2. Deep Sleep 流（去重 + 合并）

```
run_deep() → run_mid_tier()（验证回写 + 衰减 + CONTEST 退出 + E3 降级）
          → run_deep_tier()（证据门控合并 + E4 + 死边清理）
```

### 3. REM Sleep 流（L4 归档合成）

```
run_rem() → 扫描 ≤12 条目 → 归档合成 → build_rem_artifacts → 治理发布 → 反馈 ≤3
```

### 4. Dispatch 调度流（含自愈）

```
Scheduler 入队 → Consumer.read()
  ├─ 正常：dispatch → process → ack/fail（重试/死信）
  └─ NOGROUP：_recover_group（冷却 30s）→ 重读；audit group_rebuilt
```

## 对外接口

| 接口 | 来源 | 说明 |
|------|------|------|
| `DreamEngine.trigger_dream_cycle()` / `run_light()/run_deep()/run_rem()/run_nightly()` | `engine.py` | 三阶段编排 |
| `RedisDreamDispatchQueue.read()` | `dispatch.py` | 读取（NOGROUP 自愈） |
| `DreamScheduler.start()` / `mark_activity()` | `scheduler.py` | 调度 |
| `DreamCompiler.compile_candidates()` | `compiler.py` | 候选编译 |

## 依赖关系

- **Redis**：Dispatch Streams + 消费组 + 去重标记 + 死信/审计流
- **MemoryStore**：L1/L2/L3/L4 读写（light 合并经 archive_entry）
- **LLM Client**：merge judge、light 整合、归档摘要
- **DreamStateStore**：light_pending / nightly_pending / dream_lock_owner

## 配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `group_rebuild_cooldown_s` | 30 | 消费组重建冷却（防风暴） |
| `tiers_enabled` | false | B3 三层合并开关（false 走 legacy） |
| `publish_gate_min_*` | 8/0.72/2/3 | 发布门控 |
| `demote_fraction` | 0.2 | Mid 降级比例 |
| `confidence_floor/ceil` | 0.05/0.95 | 置信度边界 |
| light sleep 阈值常量 | 12h | `effective_threshold_s` 下限 |