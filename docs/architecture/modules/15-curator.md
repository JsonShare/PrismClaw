# 策展器

## 职责概述

策展器（Curator）是 PrismClaw 的后台 SOP（Standard Operating Procedure）生命周期管理器，作为 Dreaming 引擎的"维护工兵"运行。核心职责包括：

- **SOP 扫描**：按 session_key 扫描活跃 SOP，归档过期条目（`status='archived'`），升级高频条目（`status='graduated'`）
- **LLM 质量审查**：周期性调用 LLM 对自动生成的 SOP 进行质量审查，执行 keep / promote / archive 决策
- **分布式锁保护**：通过 `DistributedMutex` 确保同一时刻只有一个策展器实例运行（跨实例互斥）
- **状态持久化**：通过 `CuratorStateStore` 将扫描和审查完成时间戳写入 Redis，跨实例共享

策展器与 Dreaming 引擎的关系：Dreaming 引擎负责**生成** SOP（`type='auto_sop'`，存储在 `pc_memory_entries` Postgres 表中），策展器负责**维护**这些 SOP 的生命周期——归档过时的、升级优质的、删除有害的。两者形成**生产者-消费者**关系。

## 架构总览

**[查看交互式架构图](../diagrams/15-curator.html)**

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          策展器模块总览                                   │
│                                                                          │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                 create_curator_loop() 长驻后台协程                 │  │
│  │                                                                   │  │
│  │   ┌─────────────┐    ┌────────────────────────────────────┐      │  │
│  │   │ sleep(间隔)  │───→│ run_curator_cycle()               │      │  │
│  │   └─────────────┘    └──────────────┬─────────────────────┘      │  │
│  └──────────────────────────────────────┼───────────────────────────┘  │
│                                         │                              │
│                                         ▼                              │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                    CuratorCycle.execute()                        │  │
│  │                                                                  │  │
│  │  ┌──────────────────────┐   ┌─────────────────────────────────┐ │  │
│  │  │ DistributedMutex     │   │        扫描阶段                  │ │  │
│  │  │ (curator:cycle)      │   │  _run_scan()                    │ │  │
│  │  │ acquire → check_alive│   │  ├─ _list_active_sop_sessions() │ │  │
│  │  └──────────┬───────────┘   │  └─ _scan_session() ×N (并发)  │ │  │
│  │             │               │     └─ sop_repo.scan_session()  │ │  │
│  │             ▼               └─────────────────────────────────┘ │  │
│  │  ┌──────────────────────┐   ┌─────────────────────────────────┐ │  │
│  │  │ CuratorStateStore    │   │        审查阶段                  │ │  │
│  │  │ (Redis 状态)         │   │  _run_review_if_permitted()     │ │  │
│  │  │ mark_scan_completed  │   │  ├─ should_run_llm_review()     │ │  │
│  │  │ mark_review_completed│   │  └─ run_llm_review() ×N         │ │  │
│  │  └──────────────────────┘   │     └─ sop_repo.review_session() │ │  │
│  │                             └─────────────────────────────────┘ │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                         │                                              │
│                         ▼                                              │
│                  CycleReport                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

**关键设计原则**：
- 单次 Cycle + 定时调度：`CuratorCycle` 不可复用，每个 cycle 需要新实例，避免状态泄漏
- `DistributedMutex` 保护临界区——锁丢失时立即停止，标记 `error="lock_lost"`
- `check_alive` 回调——在扫描和审查的关键点检查锁活性，缩小 UPDATE 竞争窗口
- 扫描并发度：`SCAN_CONCURRENCY = 10`（`asyncio.Semaphore` 控制）
- LLM 审查 prompt 预算：`_REVIEW_PROMPT_CHAR_BUDGET = 60,000`（位于 `persistence/curator/repository.py`，按批次裁剪条目，不截断单条 SOP）

## 核心组件

### CuratorCycle (`cycle.py`)

单次策展器周期执行，生命周期：构造 → `await execute()` → 读取 `CycleReport`

| 参数 | 类型 | 说明 |
|------|------|------|
| `sop_repo` | `CuratorSopRepository` | SOP 仓库接口 |
| `settings` | `Any` | 策展器配置（archive_after_days、llm_review_enabled 等） |
| `state_store` | `CuratorStateStore` | Redis 状态存储 |
| `lock_manager` | `LockManager` | 分布式锁管理器 |
| `task_manager` | `TaskManagerProtocol` | 任务管理器 |
| `l1_index` | `Any` | L1 索引 |
| `workspace_base_dir` | `Path \| None` | 工作区根目录 |
| `llm_client` | `Any` | LLM 客户端（审查必需） |
| `mode` | `CycleMode` | `"scan_and_review"` 或 `"review_only"` |
| `force_review` | `bool` | 是否强制执行 LLM 审查（忽略间隔） |
| `owner_label` | `str` | 标识来源（"timed" 等） |
| `abort_event` | `asyncio.Event \| None` | 外部取消信号 |

CycleMode = `Literal["scan_and_review", "review_only"]`

### CuratorStateStore (`state.py`)

策展器跨实例 Redis 状态的**唯一写入者**：

| 方法 | 功能 |
|------|------|
| `mark_scan_completed()` | 写入扫描完成时间戳 |
| `mark_review_fully_completed()` | 写入 LLM 审查完成时间戳 |
| `seed_if_missing()` | NX 初始化 last_run_at（首次启动） |
| `get_last_scan_at()` | 读取上次扫描时间（float） |
| `get_last_review_at()` | 读取上次审查时间（int） |

Redis 键：

| 键名 | 格式 | 说明 |
|------|------|------|
| `prismclaw:curator:last_run_at` | `str(time.time())` | 上次扫描完成时间 |
| `prismclaw:curator:llm_review_last_run_at` | `str(int(time.time()))` | 上次审查完成时间 |

### 策展器入口函数 (`__init__.py`)

| 函数 | 功能 |
|------|------|
| `create_curator_loop(settings, sop_repo, redis_client, l1_index, ...)` | 长驻后台协程，按间隔调度策展器周期 |
| `run_curator_cycle(sop_repo, settings, redis_client, ...)` | 执行一轮策展器周期（CuratorCycle 包装） |
| `run_curator_scan(sop_repo, archive_days, l1_index, ...)` | 执行扫描阶段 |
| `run_llm_review(sop_repo, session_key, settings, llm_client, ...)` | 执行 LLM 审查 |
| `should_run_llm_review(settings, state_store)` | 检查是否应执行 LLM 审查 |
| `_list_active_sop_sessions(sop_repo)` | 列出所有拥有活跃 SOP 的 session_key |
| `_scan_session(sop_repo, session_key, archive_days, ...)` | 扫描单个会话 |
| `_review_session(sop_repo, session_key, settings, ...)` | 审查单个会话 |

### LLM 审查 Prompt

```
你是一个 SOP 质量审查员。审查以下自动生成的 SOPs，对每条给出决策：

可用决策: keep / promote / archive
- keep: 质量好，继续保留
- promote: 高频使用 + 高质量，推荐升级为正式 Skill
- archive: 质量差或内容有害，归档

审查标准：
1. 是否是 CLASS-LEVEL 通用过程（非 instance-specific）
2. 步骤是否清晰可执行
3. 是否有安全风险
4. 内容是否过时或错误

输出 JSON 数组: [{"id": "...", "decision": "keep|promote|archive", "reason": "..."}]
```

## 关键数据流

### 策展器周期执行流

```
create_curator_loop() 循环
  │
  ├── sleep(check_interval_seconds)
  ├── get_last_scan_at() → 判断间隔是否到达
  │
  └── run_curator_cycle(mode="scan_and_review")
        │
        └── CuratorCycle.execute()
              │
              ├── DistributedMutex(curator:cycle).acquire()
              │     └── LockAcquireError → return CycleReport(acquired=False)
              │
              ├── [mode=scan_and_review] _run_scan()
              │     ├── run_curator_scan()
              │     │     ├── _list_active_sop_sessions(sop_repo) → [sk1, sk2, ...]
              │     │     └── 并发: _scan_session(sk) → (archived, graduated)
              │     │           └── sop_repo.scan_session(session_key, archive_days, ...)
              │     └── 日志: scanned/archived/graduated/errors
              │
              ├── _run_review_if_permitted()
              │     ├── llm_review_enabled? → no → skip
              │     ├── llm_client / workspace_base_dir 缺失? → skip
              │     ├── should_run_llm_review() → interval 未到? → skip
              │     ├── _list_active_sop_sessions() → [sk1, sk2, ...]
              │     └── 遍历: run_llm_review(sk)
              │           └── _review_session(sk)
              │                 └── sop_repo.review_session(session_key, settings, llm_client, ...)
              │
              ├── _mark_completion()
              │     ├── state_store.mark_scan_completed()
              │     └── state_store.mark_review_fully_completed()
              │
              └── _build_report() → CycleReport
```

### LLM 审查间隔控制流

```
should_run_llm_review(settings, state_store)
  │
  ├── settings.llm_review_enabled == False → return False
  │
  ├── state_store.get_last_review_at() → last_run
  │     └── time.time() - last_run < settings.llm_review_interval_seconds → return False
  │
  └── return True
```

## 对外接口

### CuratorCycle

| 方法 | 返回类型 | 说明 |
|------|----------|------|
| `execute()` | `CycleReport` | 执行一轮策展周期 |

### CuratorStateStore

| 方法 | 返回类型 | 说明 |
|------|----------|------|
| `mark_scan_completed()` | `None` | 标记扫描完成 |
| `mark_review_fully_completed()` | `None` | 标记审查完成 |
| `seed_if_missing()` | `None` | NX 初始化时间戳 |
| `get_last_scan_at()` | `float \| None` | 上次扫描时间 |
| `get_last_review_at()` | `int \| None` | 上次审查时间 |

### 入口函数

| 函数 | 返回类型 | 说明 |
|------|----------|------|
| `create_curator_loop(...)` | `None` | 长驻后台协程 |
| `run_curator_cycle(...)` | `CycleReport` | 执行一轮 |
| `run_curator_scan(...)` | `CuratorReport` | 扫描阶段 |
| `run_llm_review(...)` | `ReviewOutcome` | LLM 审查 |

## 依赖关系

- **Foundation**：`prismclaw.engine.foundation.distributed_mutex.DistributedMutex`
- **Schema**：`prismclaw.schema.agent.curator.CuratorSopRepository` — SOP 仓库接口（scan_session / review_session / list_active_sop_sessions）
- **Schema**：`prismclaw.schema.agent.curator_types.CuratorReport`、`CycleReport`、`ReviewOutcome`
- **Schema**：`prismclaw.schema.shared.exceptions.LockAcquireError`、`LockLostError`
- **Schema**：`prismclaw.schema.persistence.LockManager`
- **Schema**：`prismclaw.schema.services.task_protocol.TaskManagerProtocol`
- **外部**：`redis.asyncio` — 状态存储

## 配置项

策展器配置通过 `settings` 对象传入，关键属性：

| 属性 | 说明 |
|------|------|
| `check_interval_seconds` | 后台循环检查间隔（秒） |
| `interval_seconds` | 策展周期最小间隔（秒） |
| `archive_after_days` | SOP 归档天数阈值（默认 90） |
| `promotion_min_days` | SOP 升级最少天数（默认 7） |
| `llm_review_enabled` | 是否启用 LLM 审查 |
| `llm_review_interval_seconds` | LLM 审查间隔（秒） |
| `max_consecutive_failures` | 连续失败上限（触发熔断） |

常量：

| 常量 | 值 | 说明 |
|------|-----|------|
| `CURATOR_CYCLE_LOCK_KEY` | `"curator:cycle"` | 分布式锁键名 |
| `SCAN_CONCURRENCY` | `10` | 并发扫描上限 |
| `_REVIEW_PROMPT_CHAR_BUDGET` | `60,000` | LLM 审查 prompt 字符预算（`persistence/curator/repository.py`，批次级） |
