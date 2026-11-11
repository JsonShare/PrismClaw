# 01 — 执行引擎 (Execution Engine)

> 源码目录：`backend/src/prismclaw/engine/execution/`

## 职责概述

执行引擎是 PrismClaw Agent 的运行时核心，负责 LLM 流式交互、工具调度、状态持久化和迭代控制。其设计哲学是 **"骨架与肌肉分离"**：`run_loop()` 作为纯控制流骨架不依赖任何子系统，所有行为通过 `LoopConfig` 的 callback 注入；`create_loop_config()` 是唯一知道子系统存在的闭包组装器，将 Arbiter、SafetyGate、HookDispatcher 映射为 callback 函数。

## 架构总览

**[查看交互式架构图](../diagrams/01-execution-engine.html)**

```
┌─────────────────────────────────────────────────────────────────┐
│                     Execution Engine 总览                        │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │              Runner (run_agent_stream)                     │  │
│  │  session 初始化 → TaskSpec 构建 → 循环运行 → teardown      │  │
│  └─────────────────────────┬─────────────────────────────────┘  │
│                            │                                    │
│  ┌─────────────────────────▼─────────────────────────────────┐  │
│  │              Agent Loop (run_loop)                         │  │
│  │  纯控制流骨架，7+3 callback，单层 while True 循环           │  │
│  └──┬──────────┬──────────┬──────────┬──────────┬────────────┘  │
│     │          │          │          │          │                │
│     ▼          ▼          ▼          ▼          ▼                │
│  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐             │
│  │Query │  │Dispatch      │  │Handoff│  │Check-│  │State │      │
│  │PreReq│─→│Pipeline+Exec │─→│Super- │  │point │  │Govn- │      │
│  │Pipe  │  │+StreamingEx  │  │visor  │  │State │  │ance  │      │
│  └──────┘  └──────┬───────┘  └───────┘  └──────┘  └──────┘      │
│                    │                                             │
│     ┌──────────────▼──────────────┐                             │
│     │    LoopConfig 闭包组装器     │                             │
│     │  Arbiter → should_continue  │                             │
│     │  SafetyGate → before_tool   │                             │
│     │  HookDispatch → after_tool  │                             │
│     └─────────────────────────────┘                             │
│                                                                 │
│  循环: prepare_turn → stream_llm                                 │
│        → should_continue(preflight) → before_tool               │
│        → execute_tools → after_tool → should_continue(post)     │
│        ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ↺ TurnUpdate.restart  │
└─────────────────────────────────────────────────────────────────┘
```

### 核心子目录

| 子目录 | 职责 |
|--------|------|
| `loop/` | Agent 循环骨架、类型定义、Arbiter 仲裁 |
| `dispatch/` | LLM 流式调用管道、工具执行编排、StreamingExecutor 并发 |
| `query/` | PreRequestPipeline — LLM 请求功能性管道 |
| `checkpoint/` | RunState 模型、RunStateMachine 状态机 |
| `handoff/` | HandoffSupervisor — sub_agent 调度链管理 |
| `capabilities/` | CapabilityCatalogService — 运行时目录 |
| `state/` | 无工具回合治理 |
| `skills/` | 技能系统（独立文档 02-skill-system.md） |

## 核心组件

### 1. Agent Loop (`loop/agent_loop.py`)

**核心函数：`run_loop()`**

纯控制流骨架，6 个核心 callback + 5 个 B/S 架构独有 callback：

| Callback | 签名 | 作用 |
|----------|------|------|
| `prepare_next_turn` | `(state) -> TurnUpdate` | 准备下一轮 |
| `stream_llm` | `(state) -> LLMStreamResult` | LLM 流式调用 |
| `should_continue` | `(state, phase) -> bool` | 前置/后置继续判断 |
| `before_tool_call` | `(ctx) -> BeforeToolCallResult` | 工具调用前拦截 |
| `execute_tools` | `(calls, state) -> list` | 执行工具调用 |
| `after_tool_call` | `(ctx) -> AfterToolCallResult` | 工具调用后处理 |

**核心数据流：**

```
prepare_next_turn
       → stream_llm
       → should_continue(preflight)
       → before_tool_call
       → execute_tools
       → after_tool_call
       → should_continue(post-turn)
```

**轮次重启机制：** 单层 `while True` 循环，通过 `TurnUpdate.restart` 和 `continue` 实现轮次重启，而非嵌套循环。

**回调注入点详解：**

| 循环阶段 | 回调注入点 | 说明 |
|---------|-----------|------|
| 上下文准备 | `prepare_next_turn` | PreRequestPipeline 9 步管道 + 压缩重启；`state.pending_messages`（预算警告、工具循环指导等）在此合并进 override 的 appended_messages |
| LLM 流式 | `stream_llm` | 流式调用 LLM，收集 usage |
| 预检 | `should_continue` (pre) | 预算 / abort / max_iterations 检查 |
| 工具执行前 | `before_tool_call` | SafetyGate + HookDispatcher(PRE_TOOL) 拦截 |
| 工具执行 | `execute_tools` | Handoff dispatch + 工具执行 + Skill dispatch |
| 工具执行后 | `after_tool_call` | HookDispatcher(POST_TOOL) + post_tool_review |
| 轮次后检 | `should_continue` (post) | 工具循环 / 所有工具被拒 / abort 检查；终态交由 `CompletionKernel` 唯一裁决 |

**LoopCallbacks Protocol**（`schema/agent/callbacks.py`）将分散在 `AgentRunnerDeps` 上的行为回调收敛为单一协议：

```python
@runtime_checkable
class LoopCallbacks(Protocol):
    on_compaction_node: Callable[..., None] | None
    on_mcp_death: Callable[[str], Awaitable[None]] | None
    on_subagent_progress: Callable[[Any], Awaitable[None]] | None
    on_stream_event: Callable[[Any], Awaitable[None]] | None
```

| 回调 | 职责 | 调用时机 | 返回值 |
|------|------|---------|--------|
| `on_compaction_node` | 通知压缩节点产生 | 压缩完成后 | `None`（同步） |
| `on_mcp_death` | 处理 MCP 服务器断连 | MCP 连接死亡时 | `Awaitable[None]` |
| `on_subagent_progress` | 转发子代理进度 | 子代理执行期间 | `Awaitable[None]` |
| `on_stream_event` | 转发流式事件 | LLM 流式响应期间 | `Awaitable[None]` |

约束：所有回调均可为 `None`（循环引擎调用前必须检查）；回调中不得抛出未处理异常；异步回调应有超时保护。

**LoopConfig 闭包组装**：`create_loop_config()` 是唯一知道子系统存在的地方。`run_loop()` 不直接 import Arbiter、SafetyGate、HookDispatcher，所有行为通过 `LoopConfig` 的 callback 注入，确保循环骨架纯化、子系统可独立替换、测试友好。

### 2. Runner 入口 (`loop/runner.py`)

**核心函数：`run_agent_stream()`**（约 1137 行）

负责完整的运行生命周期：

- Session 初始化与 TaskSpec 构建
- Hook dispatch（session_start / session_end）
- ToolContext 构造与 RunLoopState 初始化
- 循环运行与 teardown
- Recovery 模式处理
- 确认流程（`awaiting_confirmation` / `awaiting_risk_confirmation`）

### 3. LLM 流式管道 (`dispatch/pipeline.py`)

**核心函数：`call_llm_stream_with_outcome()`**

基于 `_OutcomeHolder` 模式，四种结果类型：

| 类型 | 语义 |
|------|------|
| `LLMStreamResult` | 正常流式输出 |
| `LLMStreamError` | 错误（含 retry 逻辑） |
| `LLMStreamRetry` | 需要重试 |
| `LLMStreamNoResponse` | 无响应 |

流式事件类型：`TextChunk`（即时正文推送，零缓冲）、`ReasoningChunk`、`HeartbeatEvent`、`IterationBoundary`（迭代切段信号）、`ToolCallStart`/`ToolCallEnd`、`Done`（含 `final_message` 全转录）、`ErrorEvent`（类型定义来自 `schema/content/streaming.py`）

错误处理策略：
- `AgentTimeoutError` / idle timeout → retry
- `AgentAbortedError` → 终止
- `LLMError` → 按 `rate_limit` / `server_error` / `connection_error` 分类处理

> `AgentTimeoutError`、`AgentAbortedError` 定义于 `schema/shared/exceptions.py`；`LLMError` 定义于 `schema/llm/errors.py`

### 4. 工具执行编排 (`dispatch/execution.py`)

核心编排流程：

```
plan_handoff_dispatch()
    → apply_handoff_dispatch_plan()
    → execute_tool_calls_with_handoff()
    → postprocess_tool_results()
    → project_tool_result_entries()
    → persist_projected_tool_result_entries()
```

**Error Fatigue 机制：** 连续 3 次相同错误注入 critic advisory，避免无限重试。

### 5. StreamingExecutor (`dispatch/streaming_executor.py`)

并发执行模型：

| 工具类型 | 并发策略 | 实现 |
|----------|---------|------|
| safe_tools（只读） | 并行 | Semaphore（默认并发 10） |
| non_safe_tools | 串行 | FIFO Lock |

并发安全性判断优先级（`_resolve_is_concurrency_safe()`）：
1. `tool.is_concurrency_safe` 显式声明
2. `CONCURRENCY_SAFE_TOOLS` 集合查找
3. `side_effect=False` 推断
4. 默认 `False`（安全优先）

### 6. Arbiter 仲裁 (`loop/arbiter.py`)

**核心类：`Arbiter`** + **`RunContext`** + **`consult_arbiter()`**

信号集：`IterationSignals`

仲裁能力：
- **路径感知恢复限制**：基于 `RunContext` 中的恢复链信息
- **多路径螺旋检测**：防止 Agent 在多条推理路径间循环
- **结构性仲裁**：`evaluate_task_mutation()` 检测任务偏移

**终止函数：** `terminate_with_error()` — 统一的错误终止入口

### 7. 核心类型 (`loop/types.py`)

| 类型 | 作用 |
|------|------|
| `RunLoopState` | 跨迭代可变状态（累加计数器、消息队列通道、状态机对象、语义演化对象） |
| `LoopConfig` | 7+3 callback 配置 |
| `BeforeToolCallContext/Result` | 工具调用前上下文和结果 |
| `AfterToolCallContext/Result` | 工具调用后上下文和结果 |
| `TurnUpdate` | 轮次更新（含 `restart` 标志） |
| `HITLCheckResult` | 人机交互检查结果 |
| `LoopTiming` | 循环时序统计 |

### 8. RunState & StateMachine (`checkpoint/`)

**`RunState`** 核心运行状态模型（`models.py`）：
- `session_id`, `run_id`, `iteration`
- `retry_state`, `recovery_state`, `budget_state`
- `InvokedSkillState`, `ResumeBoundary`
- `RequestAssemblySnapshot`, `SystemPromptSectionState`

**`RunStateMachine`**（`state_machine.py`）封装 RunState 操作：
- `retry_counts_view()` — 重试计数视图
- `advance_iteration()` — 推进迭代
- `record_retry()` — 记录重试
- `load_tool_loop_state()` — 加载工具循环状态
- `recovery_chain_for_arbiter()` — 为 Arbiter 构建恢复链
- `terminate_run()` — 终止运行

### 9. HandoffSupervisor (`handoff/supervisor.py`)

管理 sub_agent 调度链：

- `resolve_dispatch_extras()` — 匹配/合并 handoff 请求
- `max_handoff_chain_length` — 默认 5，防止无限递归
- `CapExceededInfo` + `HandoffDispatchExtras` — 数据类

**`request_handoff` 工具**（`handoff/request_handoff.py`）：子代理专用的内置工具，允许一个 Agent 将任务移交给另一个 Agent。

| 参数 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `target` | string | `^[a-z][a-z0-9-]*$` | 目标 Agent 名称 |
| `task` | string | 8-8192 codepoints | 任务描述，作为目标 Agent 的初始用户消息 |
| `context_hint` | string | 0-16384 codepoints | 可选上下文提示 |

约束：仅子代理可用（`subagent_depth > 0`）；每轮循环仅允许一次 handoff 尝试；aborting/timed_out 状态下 handoff 被丢弃。

**`can_handoff_to` 白名单机制**（`AgentManifest` 字段）：

| 场景 | 行为 |
|------|------|
| `can_handoff_to` 省略（`None`） | 开放模式——不检查白名单，但 target 必须在 catalog 中存在 |
| `can_handoff_to` 非空列表 | 白名单模式——target 必须在列表中 |
| `can_handoff_to` 空列表 | 不允许——Pydantic 验证报错 |
| `allowed_tools` 排除 `request_handoff` 但声明了 `can_handoff_to` | 不允许——白名单不可达 |

**运行时校验流程**：`request_handoff.execute()` → 检查 subagent_depth → 验证参数 → 检查 handoff_request_seen → 检查 abort/timeout → 查询 CapabilityCatalogService → 检查白名单 → 构建 HandoffPayload

**Handoff 链追踪**：每次 handoff 生成唯一的 `handoff_chain_id`，维护 `hop_index` 和 `effective_hop_index`，确保完整链路可审计。

### 10. 工具循环策略 (`dispatch/call_handling.py`)

**`evaluate_tool_loop_policies()`** 检查项：

| 策略 | 检测逻辑 |
|------|---------|
| per-tool max uses | 单工具调用次数上限 |
| 重复签名检测 | 相同工具+相同参数 |
| Error fatigue | 连续错误次数 |
| Search-loop detection | 双条件：调用次数 + 输出字符占比 |
| Same-target re-read (Check 5) | 同目标累计读取 ≥ `same_target_read_warn_at`（默认 4）且该目标零写入 → advisory |
| doom-loop 三级阶梯 | 连续相同工具签名 → soft-warn → 纠偏注入（after_tool_call 节流后）→ freeze（只冻结调用，不终止；终态由 CompletionKernel 裁决） |
| 有界循环检测 (Check 2b) | 签名窗口满且独特组合 ≤ 2 的交替打转 → warn（终止权收编后只产嫌疑信号） |
| 失败归因分流 | external 失败（MCP 传输/业务错误、sub-agent 整体失败）不进签名窗口、不计连错，只累计 external 计数 |
| 交付观测 | 本批次有**命名** write 类工具（write/edit 等；bash 整体排除——其小结果可能是计算 stdout，假交付比漏判更糟）的成功执行 → 追加进 `delivery_events` 台账（v2.2 累积式证据，非一次性布尔） |

**核心类型：** `ParsedToolCall`、`ToolLoopOutcome`

**doom-loop 阶梯行为（`doom_loop_threshold` 配置，默认 3）：**

| 阶段 | 条件 | 行为 |
|------|------|------|
| 软告警 | streak == `repeated_tool_signature_threshold` | 注入 legacy 告警 guidance |
| 纠偏注入 | `warn_at` < streak < `absolute_max` | 注入中文纠偏 guidance（action=warn，主循环 continue，调用方 after_tool_call 节流后回流） |
| 冻结 | streak >= `absolute_max`（2×threshold） | action=freeze：注册冻结签名，下一回合 before_tool_call 起 block；模型无视冻结重试达 `escalate_above` → 置 `frozen_call_defied` 嫌疑标志 |

**Check 5 同目标重复读取（2026-10-23，s:d54adf8fb4aa4742 事故修复）：**

Check 2 的指纹是「同调用 + 逐字节同结果」，对「同文件不同区间」重读天然失效（每次区间/字节都不同 = 机械层面的新信息）——该事故中模型 26 轮反复 `sed`/`cat` 同一 mining_work 区间，L1 全过，直到 L2 评审第 3 次连击才熔断（前 12 轮零拦截）。

- **计数**：`record_target_observations`（after_tool_call，全批次覆盖——`record_tool_call_observation` 只看首个调用）维护 `ToolLoopState.target_read_counts` / `target_write_counts`。读/写由**结果规模**判别（≥ `_READ_MIN_RESULT_CHARS`=500 字符 = 读取；write 类工具的小结果 = 写入），不解析 shell 语义；bash 多路径等歧义调用不计（宁缺勿错）。
- **触发**：当前调用目标累计读取 ≥ K 且零写入（读后编辑再读是合法模式，写计数即排除）→ `action="warn"` 定向 advisory。**不冻结**——读取是任务刚需（Check 4 能冻 search 是因为搜索可弃）。预警后每 3 次重读再提醒（K 及 K+3、K+6…），无额外状态。
- **配套（同次修复）**：P0 重写 streak=2 注入文案（现行"consolidate & conclude NOW"对已宣布计划的模型是"劝弃"，恰与其自我计划冲突；新文案点名重叠重读 + 提醒执行计划 + 允许不完整交付）；P1 把读取直方图（`build_read_histogram`，top-3）喂进评审提示，首个 stalled 预计从 iter 17 提前到 8-11。
- **搜索类工具归 Check 4**（query 目标重复计价在那边），Check 5 显式跳过避免双重预警。

**终止权收编（CompletionKernel）：** 机械层（Check 1-5、冻结阶梯）与 L2 进展评审均不再自行 terminate，只产 warn/freeze 引导与**纠偏**信号（偏离只回流纠偏指引，不掐断）；run 级终态**唯一出口**是 `engine/execution/control/kernel.py` 的 `CompletionKernel.decide()`（2026-10-30 收权，由 `execution/state/governance.py` 调用）。`should_continue` 的 preflight / all_blocked / post 三个站点均只把事件交给内核裁决，不各自 terminate。

**`CompletionKernel.decide(event)` 裁决顺序（首个命中返回）：**

| 顺序 | 条件 | 裁决 |
|------|------|------|
| 1 预算硬上限 | `BudgetGuard`：迭代数 / 运行时长 / 美元成本超限 | `TERMINATE` |
| 2 非模型停止 | preflight / post 正常推进；`all_blocked` 且恢复额度未尽 | `PROCEED` / `RESTART` |
| 3 全拒熔断 | `all_blocked` 且恢复链耗尽 | `TERMINATE`（全拒不可自愈） |
| 4 无完成契约 | `contract is None` | `COMPLETE`（交付物是文本，机械侧无产物可核） |
| 5 申报阻塞 | `declares_blocked` 且评审未通过 | 已有修复尝试或已产生交付物 → `TERMINATE`；否则 `REPAIR`（计入一次修复） |
| 6 评审通过 | 交付契约满足 | `COMPLETE` |
| 7 评审未通过 | advisory 首次 → `REPAIR`(advisory_nudge)，二次放行 `COMPLETE`；强制修复额度耗尽 → `TERMINATE`；否则 `REPAIR` |

> 机械层与纠偏层的诊断信号（`frozen_call_defied` / `cycling_loop_streak` / `consecutive_errors` / 停滞连击）只作为**纠偏输入**，由 `after_tool_call` 节流后注入 guidance 回流执行内层；它们**不进入**终止判定。

**失败归因（`ToolResult.error_origin`）：** MCP 传输/业务错误（adapter）与 sub-agent 整体失败（subagent/tool.py）打标 `external`；内置工具错误经 `tool_runtime.error_result(error_origin="internal")` 显式 internal；未打标按 internal 保守处理。external 失败不进 `recent_tool_signatures`（Check 2/2b 无信号）、不计 `consecutive_errors`（Check 3 只管 internal），只累计 `consecutive_external_errors` / `consecutive_external_same_signature` 供纠偏层区分「换源对抗中」与「死磕单一挂掉源」——科研数据源（CNKI 等）连续失败的合法重试不被误杀。

### 11. PreRequestPipeline (`query/pre_request_pipeline.py`)

功能性管道构建 LLM 请求，不修改 tree 或全局状态。
核心类型：`PreparedTurn`

### 12. 无工具回合治理 (`state/governance.py`)

**`evaluate_no_tool_governance()`** — 当 Agent 一轮没有调用工具时的治理决策：

| 结果 | 含义 |
|------|------|
| `restart_turn` | 重启本轮 |
| `done` | 完成 |
| `await_confirmation` | 等待确认 |
| `repair` | 修复 |

### 13. CapabilityCatalogService (`capabilities/catalog.py`)

统一运行时目录，三类条目：
- `SkillCatalogEntry`
- `ToolCatalogEntry`
- `AgentCatalogEntry`

### 14. AgentHook 协议与 HookDispatcher (`engine/hooks/`)

**AgentHook Protocol** 定义 Agent 循环的 Hook 协议，由 `HookRegistry` 管理：

```python
@runtime_checkable
class AgentHook(Protocol):
    async def before_prompt_build(self, context: PromptBuildContext) -> PromptBuildResult | None: ...
    async def after_response(self, observation: ResponseObservation) -> None: ...
    async def before_compaction(self, context: CompactionContext) -> None: ...
    async def after_compaction(self, context: CompactionContext, result: CompactResult) -> None: ...
```

| Hook 方法 | 输入 | 返回值 | 职责 |
|-----------|------|--------|------|
| `before_prompt_build` | `PromptBuildContext`（session_id, workspace_id, agent_id, available_tools, prompt, extras） | `PromptBuildResult(prepend, append)` 或 `None` | prompt 构建前注入上下文：`prepend` 前置到系统提示，`append` 追加到末尾 |
| `after_response` | `ResponseObservation`（session_id, assistant_text, tool_calls） | `None` | 观测 LLM 响应，用于审计/统计 |
| `before_compaction` | `CompactionContext`（session_id, workspace_id, agent_id, tokens_before, message_count） | `None` | 压缩前通知 |
| `after_compaction` | `CompactionContext` + `CompactResult` | `None` | 压缩后通知 |

可选方法（通过 `getattr` 发现）：`on_run_start(session_id, control)`、`on_run_end(session_id, terminated_by)`

约束：`before_prompt_build` 返回 `None` 等同于不注入；`HookRegistry.collect_prompt_additions` 聚合所有 hook 的 prepend/append 用 `"\n\n"` 连接；单个 hook 异常不阻塞其他 hook。

**HookDispatcher** 提供更细粒度的事件分发，支持 12 种 `HookEvent`：

| 事件类别 | 事件 |
|---------|------|
| 会话生命周期 | `SESSION_START`, `SESSION_END`, `SETUP`, `STOP` |
| 用户输入 | `USER_PROMPT_SUBMIT` |
| 工具调用 | `PRE_TOOL`, `POST_TOOL`, `POST_TOOL_FAILURE` |
| 压缩 | `PRE_COMPACT`, `POST_COMPACT` |
| Subagent | `SUBAGENT_START`, `SUBAGENT_STOP` |
| 环境 | `FILE_CHANGED`, `CWD_CHANGED` |
| 权限 | `PERMISSION_REQUEST`, `PERMISSION_DENIED` |

调度语义：每个 handler 返回 `HookResult`，第一个 `blocking` 结果停止后续执行；`DENY`/`BLOCK` 拒绝工具调用；`ALLOW` 不覆盖审批门禁（只追加拒绝，从不移除）；默认超时 30 秒。

## 关键数据流

### 主循环数据流

```
用户消息
    ↓
run_agent_stream() → 构建 TaskSpec + RunLoopState
    ↓
run_loop() 循环：
    prepare_next_turn() → 准备轮次上下文（合并 pending_messages）
    stream_llm() → LLM 流式响应
    should_continue(preflight) → Arbiter 前置判断
    before_tool_call() → SafetyGate + Hook 拦截
    execute_tools() → StreamingExecutor 并发执行
    after_tool_call() → Hook 后处理 + 技能上下文修改
    should_continue(post-turn) → Arbiter 后置判断
    ↓
TurnUpdate.restart → continue（轮次重启）
TurnUpdate.done → 循环结束
```

### Recovery 模式数据流

```
RunState.recovery_state → RunStateMachine.recovery_chain_for_arbiter()
    → Arbiter.decide() 路径感知恢复限制
    → RunLoopState 语义演化对象
```

## 对外接口

| 接口 | 函数/类 | 说明 |
|------|---------|------|
| 主入口 | `run_agent_stream()` | Agent 运行入口 |
| 循环骨架 | `run_loop()` | 纯控制流循环 |
| 闭包组装 | `create_loop_config()` | 注入子系统 callback |
| LLM 调用 | `call_llm_stream_with_outcome()` | 流式 LLM 请求 |
| 工具执行 | `execute_tool_calls_with_handoff()` | 工具调度入口 |
| 仲裁 | `consult_arbiter()` | 统一仲裁入口 |
| 状态操作 | `RunStateMachine` | RunState CRUD |

## 依赖关系

```
execution/
  ├── loop/agent_loop.py → loop/types.py, loop/arbiter.py
  ├── loop/runner.py → loop/agent_loop.py, dispatch/pipeline.py,
  │                     dispatch/execution.py, checkpoint/state_machine.py,
  │                     handoff/supervisor.py, capabilities/catalog.py
  ├── dispatch/pipeline.py → (LLM SDK)
  ├── dispatch/execution.py → dispatch/streaming_executor.py,
  │                           dispatch/call_handling.py
  ├── dispatch/streaming_executor.py → (concurrent.futures)
  ├── checkpoint/state_machine.py → checkpoint/models.py
  ├── handoff/supervisor.py → (schema types)
  ├── query/pre_request_pipeline.py → (schema types)
  └── state/governance.py → (schema types)
```

**外部依赖（通过 callback 注入，非直接 import）：**
- `engine/security/gate/` — SafetyGate（通过 `before_tool_call` callback）
- `engine/plugins/hooks/` — HookDispatcher（通过 `after_tool_call` callback）
- `engine/execution/skills/` — SkillRuntime（通过工具结果后处理）

**关键源码索引：**

| 概念 | 文件路径 |
|------|---------|
| LoopCallbacks Protocol | `schema/agent/callbacks.py` |
| AgentHook Protocol + HookRegistry + HookDispatcher | `engine/hooks/__init__.py` |
| AllowedToolsPolicy | `schema/agent/security.py` |
| 7-Reducer Governance 链 | `engine/toolkit/registry/governance.py` |
| request_handoff 工具 | `engine/execution/handoff/request_handoff.py` |
| Agent Loop 核心 | `engine/execution/loop/agent_loop.py` |

## 配置项

| 配置项 | 位置 | 默认值 | 说明 |
|--------|------|--------|------|
| `CONCURRENCY_SAFE_TOOLS` | `streaming_executor.py` | — | 安全工具集合 |
| Semaphore 并发数 | `StreamingExecutor` | 10 | safe_tools 最大并发 |
| `max_handoff_chain_length` | `HandoffSupervisor` | 5 | Sub-agent 调度链深度上限 |
| Error fatigue 阈值 | `call_handling.py` | 3 | 连续相同错误注入 advisory |
| Search-loop 条件 | `call_handling.py` | 双条件 | 调用次数 + 输出字符占比 |
