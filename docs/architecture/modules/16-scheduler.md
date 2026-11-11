# 调度器

## 职责概述

调度器（Scheduler）是 PrismClaw 的定时任务执行基础设施，提供 cron 表达式驱动的任务触发、一次性唤醒通知、普通 chat turn 的定时执行以及分布式 leader 选举。核心职责包括：

- **定时触发**：1 秒 tick 循环，通过 croniter 计算 `next_at`，到期后分发任务
- **确定性抖动**：基于 task_id 的确定性哈希产生 [0,1) 抖动，防止雷群效应
- **分布式 leader 选举**：通过 `LockManager` 确保只有一个 worker 执行 tick
- **事件通知**：Redis Streams 作为 one-shot 唤醒信号，consumer group 保证跨 worker 单次投递
- **触发幂等**：确定性 `run.id` + PostgreSQL `ON CONFLICT DO NOTHING`，赢家才入队
- **复用主链路**：`ScheduledRunExecutor` 只负责调度状态机；执行由 app 层注入 `run_chat` → `chat_runner._run_chat` → `run_agent_stream`，与普通 `chat.send` 完全一致
- **跨 worker 缓存失效**：Redis pub/sub 通知 + dirty flag 兜底
- **跨节点事件可达**：本地连接优先投递，本机无连接时转发给 affinity owner
- **熔断器**：连续失败达阈值时自动禁用任务

**[查看交互式架构图](../diagrams/16-scheduler.html)**

## 架构范式：event notification，不是 work queue

一次定时 fire 是一个**没有时长上限的普通 chat turn**，而 Redis Streams 的 PEL / `XAUTOCLAIM` 隐含「工作有界、可以用时间窗口判断 worker 已死」。两者不相容：不设执行时长上限就**无法区分「跑得慢」与「进程已死」**，任何自动恢复都要引入时间窗口 / 心跳 / 租约，等于把刚删掉的租约重新加回来。

因此本模块采用：

| 层 | 职责 | 不负责 |
|----|------|--------|
| `store.add_run` | 确定性 `run.id` + `ON CONFLICT DO NOTHING`，决定谁赢得这次 fire | 执行、状态流转 |
| `dispatch` | 读后即 ACK，spawn 一次 handler | 重试、死信、PEL 回收、去重 |
| `consumer` | 每节点本地 semaphore 背压 | 跨节点并发上限 |
| `executor` | run 状态记录、通知、熔断 | 判重、加锁、超时、执行 |

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          调度器模块总览                                   │
│                                                                          │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │ Layer 1: SchedulerEngine (1s tick, leader-only)                  │  │
│  │  Leader 选举 / nextFireAt 缓存 / _recover_missed / 确定性抖动     │  │
│  │  fire_slot = 原始 cron next_ts（不含 jitter）                     │  │
│  └──────────────────────────┬────────────────────────────────────────┘  │
│                             ▼                                            │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │ 幂等门: store.add_run(run)                                        │  │
│  │  run.id = sha256(f"{task_id}:{fire_slot}")[:16]                   │  │
│  │  INSERT ... ON CONFLICT DO NOTHING RETURNING → 只有赢家继续       │  │
│  │  同一事务内 stamp last_fired_at                                   │  │
│  └──────────────────────────┬────────────────────────────────────────┘  │
│                             ▼  (仅赢家)                                  │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │ Layer 2: RedisSchedulerDispatchQueue (one-shot 唤醒信号)          │  │
│  │  XADD → XREADGROUP → 立即 XACK → spawn(handler(job))              │  │
│  │  无 SET NX 去重 / 无 XAUTOCLAIM / 无死信 / 无 attempt_count       │  │
│  └──────────────────────────┬────────────────────────────────────────┘  │
│                             ▼                                            │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │ Layer 3: SchedulerDispatchConsumer (每节点本地背压)               │  │
│  │  全局 Semaphore(3) + 每用户 Semaphore(2)                          │  │
│  └──────────────────────────┬────────────────────────────────────────┘  │
│                             ▼                                            │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │ Layer 4: ScheduledRunExecutor (仅调度状态机)                      │  │
│  │  running → _create_session → await run_chat → 终态 → 熔断 → 通知  │  │
│  │  不设执行超时 / 不设租约 / 不判重 / 不加锁                       │  │
│  │  执行、事件、时间线、交付物全部交给普通 chat 主链路               │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  跨 worker 缓存失效: pub/sub INVALIDATE_CHANNEL + dirty flag 兜底         │
│  跨节点事件可达: 本地连接优先；无本地连接 → 转发给 affinity owner        │
└──────────────────────────────────────────────────────────────────────────┘
```

**关键设计原则**：

- Leader-only tick：只有持有 leader lock 的 worker 执行调度；两个 leader 脑裂时由 fire slot 的 CAS 兜底
- Lock heartbeat 独立于 tick：续约周期 `min(2s, TTL/3)`（默认 TTL 5s → 约 1.67s），防止慢 tick 导致锁过期
- 锁 value 嵌入 worker 身份：`"{worker_id}:{random}"`（随机段保唯一，CAS 语义不变）；follower 抢锁失败是设计内常态——记 **INFO** 并携带当前持锁者（`holder`，由锁 value 解析），同一持锁者只记一次、翻转/复得后再记（变化驱动），不再每秒刷 WARNING
- nextFireAt 缓存：进程内缓存 + Redis pub/sub 跨 worker 失效
- Missed-task 恢复：首次 leader tick 时检测错过的一次性任务
- **fire_slot 必须用不含 jitter 的原始 cron `next_ts`**：`_tick()` 与 `_recover_missed()` 对同一次 fire 必须算出同一个 `run.id`；用带 jitter 的 `next_at` 会让幂等门失效
- 定时只是「触发方式」，不是另一套执行 mode：不做定时专用的工具策略 / 轮次上限 / 交付物补偿；执行、事件、落库全部与普通 chat 一致
- 单机 N=1 与多节点 N>1 走同一套代码，没有 topology 分支

## 崩溃语义：at-most-once

不设执行时长上限 ⇒ 无法区分「跑得慢」与「进程已死」 ⇒ 自动恢复必然引入时间窗口 / 心跳 / 租约 ⇒ 与「无时长上限」互斥。本模块选择 at-most-once：

- 消息在 handler 启动**之前**就已 XACK，进程消失后通知不会重投
- run 停在 `pending` 或 `running`，**不自动补跑**；这是如实状态，不是卡死的 bug
- 用户数据不丢：消息 / 时间线 / 交付物由主链路写在共享 `RedisSessionStore`，任意节点刷新可见
- 下一次 cron fire 的 fire slot 不同，自然产生新 run
- `RunStatus = pending | running | success | failed`，`TaskStatus = idle | running | success | failed | disabled`，均无 `timeout`

## 核心组件

### SchedulerEngine (`engine.py`)

驱动定时任务触发的核心引擎，作为长驻协程运行于 `TaskManager`：

| 方法 | 功能 |
|------|------|
| `start()` | 主循环：leader 选举 → tick → sleep |
| `stop()` | 停止：取消 watch/heartbeat 任务，释放锁 |
| `invalidate_cache(task_id)` | 缓存失效（CRUD 后调用，支持跨 worker） |
| `_cron_fire_timestamp(task, anchor)` | 返回**不含抖动**的原始 cron 下次触发时间戳 |
| `_compute_next_fire(task, anchor)` | 原始 `next_ts` + 确定性抖动 → `next_at` |
| `_dispatch(task, now, fire_slot)` | 幂等门 + 入队 |

**`_tick()`**：
1. `_ensure_leader()` — 获取/验证 leader 锁
2. 缓存对账 — `_cache_dirty` 时清除 nextFireAt 缓存
3. `_recover_missed()` — 首次 leader tick 检测错过任务（`run_once` 策略）
4. `store.list_tasks(enabled_only=True)`
5. 遍历任务：`_is_expired()` → 禁用；`_compute_next_fire()` → `now >= next_at` 时取 `fire_slot = _cron_fire_timestamp(task)` 并 `_dispatch()`
6. 到期分发由 `asyncio.gather(*dispatch_coros, return_exceptions=True)` 并发执行

**`_dispatch(task, now, fire_slot)`**：
- 构造 `ScheduledTaskRun(id=make_run_id(task.id, fire_slot), status="pending")`
- `won = await store.add_run(run, fired_task_id=task.id, fired_at=now)`
- `won is False` → 记 `scheduler.task.fire_deduped` 并直接返回（不 XADD）
- `won is True` → 构造 `SchedulerDispatchJob` 并 `await dispatch_enqueue(job)`
- 入队抛异常 → `store.update_run(status="failed", error="dispatch_enqueue_failed")` + `logger.exception`；由于外层 `gather(return_exceptions=True)` 会吞异常，这个显式收口是 run 不留 `pending` 的唯一保障

**Leader 选举 / 缓存失效 / 抖动**与旧版一致：
- `_lock_heartbeat_loop()` 续约周期 `min(2s, TTL/3)`，失败则 `_lock_token = None`，下次 tick 重新竞选
- `_watch_invalidation_with_reconnect()` 订阅 `INVALIDATE_CHANNEL`，断线置 `_cache_dirty = True` 并指数退避重连；`_redis_ping_loop()` 30s PING 保活
- 抖动量 = `_jitter_fraction(task_id) × jitter_fraction × interval`，上限 `jitter_cap_seconds`

### RedisSchedulerDispatchQueue (`dispatch.py`)

Redis Streams **一次性唤醒信号**，不是工作凭证：

| 方法 | 功能 |
|------|------|
| `ensure_group()` | 创建 consumer group（幂等，吞 `BUSYGROUP`） |
| `enqueue(job) -> None` | `XADD` 7 个业务字段：`run_id / task_id / task_name / instruction / user_id / workspace_id / fired_at` |
| `consume(handler, block_ms, count)` | `XREADGROUP` → **立即 XACK** → 解析 → `create_task(handler(job))` |

- 消息在 handler 启动前 ACK；ACK 失败记 `scheduler.dispatch.ack_error` 后丢弃该条通知（与 at-most-once 一致）
- `_parse_message()` 缺字段直接返回 `None` 并丢弃坏消息，不会生成空值 job 去执行
- `inflight` 只用于停机时 cancel 与异常日志：`_on_done()` 检索 `task.exception()` 记 `scheduler.dispatch.handler_error`，**不重投、不恢复**
- `CancelledError` 从消费循环自然传播，并 cancel 所有 inflight task

### SchedulerDispatchConsumer (`dispatch.py`)

消费循环的并发控制包装，**每节点本地**语义：

| 参数 | 说明 |
|------|------|
| `max_concurrent` | 每节点并发上限（默认 3） |
| `max_per_user` | 每节点每用户并发上限（默认 2） |

- `asyncio.Semaphore(max_concurrent)` + `_user_semaphores[user_id]`
- 跨节点总上限属容量治理，不在本模块范围

### ScheduledRunExecutor (`executor.py`)

**只做调度状态机**：不构造 `RunRequest`、不调用 `run_agent_stream`、不写交付物。执行入口由 app 层通过 `run_chat` 回调注入，engine 层不反向依赖 `app/web`。

| 方法 | 功能 |
|------|------|
| `execute(job)` | 直接 `await _execute_inner(job)`；**不判重、不加锁、不套超时** |
| `_execute_inner(job)` | 状态流转 + 失败处理 + 熔断 |
| `_create_session(job)` | 以任务名为标题创建 `web:{user_id}:{workspace_id}:s:{hex}` 会话 |
| `_notify(...)` | 广播 `scheduled_task.started / completed / disabled` |

**`_execute_inner(job)`**：
1. `store.update_run(status="running", started_at)` + `store.update_status(task_id, "running")`
2. `_notify("scheduled_task.started")`
3. `_create_session(job)`
4. `await run_chat(user_id, conversation_id=session_id, content=instruction, workspace_id)` — **无超时包裹**
5. 终态写入 `update_run(status, finished_at, session_id, error, duration_s)` + `set_conversation`
6. 熔断：连续失败 ≥ `max_consecutive_failures` → `update_status("disabled")` + `put_task(enabled=False)` + `scheduled_task.disabled`
7. `_notify("scheduled_task.completed")`

`CancelledError` 不被捕获：停机与崩溃一致，run 如实停在 `running`。

### 执行装配 (`app/_lifespan.py`)

`_run_scheduled_chat` 把一次定时触发包装成一次普通 `chat.send`：

```python
state = ConnectionState(ws=SimpleNamespace(app=app), ws_session_id=..., user_id=user_id)
state.ws.send_json = _virtual_socket_send  # → _fanout_to_peer_connections(state, envelope)
msg = ChatSendMessage(conversation_id=session_id, workspace_id=..., content=instruction)
await _run_chat(state, msg, app.state.web_deps.web_settings)
```

虚拟 socket 复用主链路的投递实现：有活跃连接就投递（界面打开时实时显示），
没有活跃连接则正常返回（静默执行）。**关键是「没有界面」必须算发送成功**——
否则 `send_event` 会判定为 WS 故障，触发 5s 冷却与 `chat.done` 的 8 次退避重试
（约 31.5s），而 `_emit_terminal_done` 排在时间线 / 交付物落库之前，会把收尾整体拖慢。

消息、`ExecutionTimeline`、`OUTPUT_FILES`、`RunProjection` 全部由 `_run_chat`
主链路落库，最终界面通过 `GET /api/sessions/{id}/messages` 回放完整的
「任务描述 → 执行过程 → 执行结果 → 交付物」。

### 跨节点事件路由 (`app/gateway/` + `app/web/connection.py`)

定时任务在节点 B 执行、用户 UI 连在节点 A 时，帧必须跨节点送达。补齐的是既有
`AffinityRegistry / ForwardPublisher / ForwardConsumer / GatewayRouter` 断点，
不是定时任务专用层，对所有 chat 生效：

| 组件 | 职责 |
|------|------|
| `gateway/event_codec.py` | 转发线格式单一真源：`encode_forward_event(user_id, frame)` / `decode_forward_event` / `web_session_key` |
| `gateway/factory.py` | `_dispatch_forwarded_event` 按 `user_id` 查**本机** `ConnectionRegistry` 并直投 `ws.send_json`；`app` 惰性引用，适配插件重载后重建的 `web_deps` |
| `web/connection.py` | `_fanout_to_peer_connections` 本地优先；本机无连接时才 `route("web:{user_id}", payload)` 转发一次 |
| `web/websocket/websocket.py` | `broadcast_to_user` 走同一路由，保证 `scheduled_task.*` 到达用户实际连接节点 |
| `gateway/router.py` | `route()` 任何 Redis 异常都兜底 `"local"`，绝不穿透到 `send_event`；`CancelledError` 仍传播 |

路由点放在**本地投递边界**而非「每个 `send_event` 之前」：`SERVER_READY` 在
`force_claim` 之前发送，先路由会把握手帧转给旧 owner 导致新 socket 永远收不到；
本地 socket 存活时也不存在跨节点需求。本地优先还让普通 chat 热路径零 Redis 往返。

**已知边界**：`GatewayRouter` 是单 owner affinity，同一用户多个连接分散在多个节点时只有 owner 节点收到帧。逐帧全投递需把 affinity 改成 user 级频道集合，属第二阶段。

### 与主链路一致带来的行为

- 定时任务持有真实的 `session_queue`，因此引擎侧 `headless=False`：`scheduled_task` 工具与交互类工具（如 `AskUserQuestion`）在模型 tool surface 中可见，与普通会话相同
- 不再有定时专用的 `CronToolPolicy` / `max_tool_calls_per_run` / 轮次上限 / 交付物兜底落盘；统一由核心引擎的通用机制约束
- 不再有调度层执行超时、`status="timeout"`、`tool_calls_count`、DB 全局活跃 run 限流；`max_concurrent_*` 仅作每节点本地背压

## 关键数据流

### 定时触发流

```
SchedulerEngine._tick() (1s, leader-only)
  ├── _ensure_leader() → True
  ├── _cache_dirty? → 清除 _next_fire_cache
  ├── store.list_tasks(enabled_only=True)
  └── 遍历 task:
        ├── _is_expired? → _expire_task()
        ├── _compute_next_fire(task) → next_at (含抖动)
        └── now >= next_at?
              ├── fire_slot = _cron_fire_timestamp(task)   # 原始 next_ts，不含抖动
              └── _dispatch(task, now, fire_slot)
                    ├── run.id = make_run_id(task.id, fire_slot)
                    ├── won = store.add_run(...)  # ON CONFLICT DO NOTHING RETURNING
                    │     ├── False → fire_deduped，return（不 XADD）
                    │     └── True  → 同一事务 stamp last_fired_at
                    └── dispatch_enqueue(job)
                          └── 异常 → update_run(failed, "dispatch_enqueue_failed")
```

`_recover_missed()` 用 `next_dt.timestamp()` 作为同一个 `fire_slot`，因此与 `_tick()` 对同一次 fire 幂等。

### event notification 消费流

```
RedisSchedulerDispatchQueue.consume(handler)
  └── 永久循环:
        └── XREADGROUP → messages
              └── 每条 message:
                    ├── XACK 立即执行（失败 → ack_error 日志，丢弃该通知）
                    ├── _parse_message(msg_id, fields) → job | None
                    └── asyncio.create_task(handler(job))
                          └── _on_done(): discard + 检索异常 → handler_error 日志
```

无 `XAUTOCLAIM`、无 PEL 回收、无死信、无 `attempt_count`、无 `stream_id`、无重试。

### 定时执行流（复用普通 chat 主链路）

```
ScheduledRunExecutor.execute(job)
  └── _execute_inner(job)
        ├── store.update_run(status="running") + update_status("running")
        ├── _notify("scheduled_task.started")
        ├── _create_session(job) → session_id = "web:{user}:{ws}:s:{hex}"
        ├── await run_chat(...)   # 无超时包裹
        │     └── app/_lifespan._run_scheduled_chat
        │           ├── 虚拟 socket: send_json → _fanout_to_peer_connections
        │           │     ├── 本机有连接 → 实时投递
        │           │     └── 本机无连接 → route() 转发给 affinity owner（一次）
        │           └── chat_runner._run_chat → run_agent_stream（同一引擎）
        │                 ├── 用户 / assistant 消息 → SessionTree
        │                 ├── ExecutionTimeline → CustomEntry
        │                 ├── OUTPUT_FILES → CustomEntry
        │                 └── run_projection 树条目（flusher 周期审计落盘；
        │                     run 结束 pop 投影，无 finalized 写入）
        ├── store.update_run(终态) + set_conversation
        ├── 熔断（连续失败 ≥ 阈值 → disabled）
        └── _notify("scheduled_task.completed")
```

### 跨 worker 缓存失效流

```
Worker A: CRUD → engine.invalidate_cache(task_id) → 清缓存 + _cache_dirty=True
          → redis.publish(INVALIDATE_CHANNEL, task_id)
Worker B (Leader): _watch_invalidation_with_reconnect() → pubsub.listen()
          → _watch_invalidation(data) → invalidate_cache(task_id)
          → 下次 _tick(): _cache_dirty → _next_fire_cache.clear()
```

## 对外接口

| 组件 | 方法 | 返回类型 | 说明 |
|------|------|----------|------|
| `SchedulerEngine` | `start()` / `stop()` / `invalidate_cache(task_id)` | `None` | 主循环 / 停止 / 缓存失效 |
| `RedisSchedulerDispatchQueue` | `ensure_group()` / `enqueue(job)` / `consume(handler, block_ms, count)` | `None` | 入队无返回值；异常直接抛给触发点 |
| `SchedulerDispatchConsumer` | `start()` | `None` | 启动消费循环 |
| `ScheduledRunExecutor` | `execute(job)` | `None` | 执行一次已领取的 fire |
| `PostgresSchedulerStore` | `add_run(run, *, fired_task_id, fired_at)` | `bool` | 唯一幂等门，赢家返回 True |
| `GET /api/scheduled-tasks/{task_id}/runs` | `list_runs(...)` | `RunResponse[]` | 按 `fired_at DESC`，前端恢复终态与运行历史的唯一真源 |

### 前端消费契约（实时状态）

`ScheduledRunExecutor` 在写库之后才广播事件，因此 WS 帧到达时 `/runs` 已反映同一状态：

1. `useChatSocket` 把 `scheduled_task.*` 帧原样派发为 `window` 上的 `prismclaw:scheduler-event`；帧不含 `conversation_id`，不走会话链路。
2. `ScheduledTasksPage` 用 `started` / `completed` 维护 `liveRuns`（仅进行态），用 `/runs` 恢复终态与最近 3 条运行记录；`RunStatusBadge` 统一渲染。不存在定时清除的假状态。
3. `ChatPage` 在 `completed` 时用 `session_id` 调 `prependConversation()` 实时插入左侧会话列表，无需刷新等 `ready` 帧 / REST 会话列表重拉。
4. 历史行若停在 `pending` / `running`，显示为「上次执行未完成」而非失败：at-most-once 语义下进程崩溃会让该行永久停留，既不能伪装成执行中，也不能误报失败。
5. 事件监听只读任务名，用 ref 而非把任务列表 state 放进 effect 依赖，避免每次重拉都注销 / 重注册监听而丢失连续帧。

回归用例：`frontend/tests/e2e/scheduled-task-realtime.spec.ts`（桩 API + mock WS 帧，无凭据依赖）。

## 依赖关系

- **Schema**：`prismclaw.schema.scheduler` — `ScheduledTask` / `ScheduledTaskRun` / `SchedulerDispatchJob` / `SchedulerStoreProtocol` / `INVALIDATE_CHANNEL` / `make_run_id`
- **Schema**：`prismclaw.schema.shared.exceptions.LockAcquireError`、`prismclaw.schema.shared.LockManager`
- **Schema**：`prismclaw.schema.session.ids.build_web_session_key`
- **Execution**：`app/web/chat/chat_runner._run_chat` 复用主链路（内部 `run_agent_stream`）；engine 层不反向依赖 app/web
- **Gateway**：`app/gateway/event_codec` / `affinity` / `forwarder` / `router` / `factory`
- **Foundation**：`prismclaw.engine.foundation.distributed_mutex.DistributedMutex`
- **外部库**：`croniter` — cron 解析、`redis.asyncio` — Streams / pub/sub、PostgreSQL `ON CONFLICT`

## 配置项

`SchedulerSettings`（`infra/settings/_plugins.py`）：

| 属性 | 说明 |
|------|------|
| `enabled` | 调度器总开关 |
| `dialogue_creation_enabled` | 是否允许通过对话创建定时任务 |
| `tick_interval_seconds` | tick 循环间隔（秒，默认 1.0） |
| `jitter_fraction` / `jitter_cap_seconds` | 抖动比例因子 / 上限（秒） |
| `max_concurrent_executions` | **每节点**并发上限（默认 3） |
| `max_concurrent_per_user` | **每节点每用户**并发上限（默认 2） |
| `max_tasks_per_user` | 每用户最大任务数（默认 20） |
| `max_consecutive_failures` | 熔断阈值（默认 3） |
| `missed_policy_default` | 错过任务的默认恢复策略（默认 `skip`） |
| `dispatch_key_prefix` | 分发队列 Redis 键前缀 |
| `leader_lock_key` / `leader_lock_ttl_ms` | leader 锁键名 / TTL（毫秒，默认 5000） |

Redis Streams 队列参数（`dispatch.py` 构造参数）：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `key_prefix` | `"prismclaw:scheduler:dispatch:"` | Redis 键前缀 |
| `group_name` | `"scheduler-dispatch"` | Consumer group 名称 |

Consumer 参数：`max_concurrent=3`、`max_per_user=2`，均为**每节点**值。

## 已知边界与未修问题

- 单 owner affinity：同一用户多连接跨节点时只有 owner 节点收到帧
- `max_concurrent_*` 是每节点语义，不是全局硬上限
- 真实两节点端到端 cron 验收（A1–A8）尚未跑过
- 模型端点 `enable_thinking=False` 未透传导致 `text_chars=0`、`search-loop FREEZE` 收敛过晚、memory selector 每轮 8s 固定超时 —— 属模型端点 / 引擎收敛，不在调度状态机范围
