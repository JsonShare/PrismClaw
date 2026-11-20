# 子代理

## 职责概述

子代理（Sub-agent）模块实现了多代理协作的核心运行机制。它负责：

- 从 `.md` 清单文件发现和注册子代理定义
- 根据上下文模式（isolated/inherit）构建子代理的初始消息上下文
- 执行子代理的 turn 循环，包括 LLM 调用、工具分发和进度上报
- 支持 sync 和 parallel 两种执行模式，以及 `dispatch_mode=background` 的后台派发
- 后台任务的进程内句柄映射、真实取消与终态投递
- 自动从工具调用历史中收集证据（EvidenceRef），并根据 URL/内容推断领域字段
- 管理并行任务的生命周期和 follow-up 通知

## 架构总览

**[查看交互式架构图](../diagrams/09-subagent.html)**

```
┌──────────────────────────────────────────────────────────────────┐
│                       SubAgent 三层架构                          │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │                   Definition 定义层                          │ │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐          │ │
│  │  │ Manifest │  │  Parser  │  │    Registry      │          │ │
│  │  │ (模型)   │  │(md→YAML) │  │ (BFS扫描+诊断)   │          │ │
│  │  └──────────┘  └────┬─────┘  └────────┬─────────┘          │ │
│  └─────────────────────────┼──────────────┼────────────────────┘ │
│                            │              │                      │
│                            ▼              ▼                      │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │                     Core 核心执行层                          │ │
│  │  ┌────────────────────────┐  ┌────────────────────────┐    │ │
│  │  │   AgentInstance        │  │   SubAgentExecutor     │    │ │
│  │  │ ┌──────────────────┐   │  │     (生产级门面)       │    │ │
│  │  │ │  LifecycleMixin  │   │  └───────────┬────────────┘    │ │
│  │  │ ├──────────────────┤   │              │                  │ │
│  │  │ │  ContextMixin    │   │  ┌───────────┴────────────┐    │ │
│  │  │ ├──────────────────┤   │  │      Contract          │    │ │
│  │  │ │  LoopMixin       │   │  │ (快照/封套/交接渲染)   │    │ │
│  │  │ └──────────────────┘   │  └────────────────────────┘    │ │
│  │  └────────────────────────┘                                │ │
│  └──────────────────────────────┬──────────────────────────────┘ │
│                                 │                                │
│                                 ▼                                │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │                    Runtime 运行时层                          │ │
│  │  ┌────────────────────┐  ┌────────────────────┐            │ │
│  │  │  ParallelTask      │  │   Follow-up        │            │ │
│  │  │  (Record+Store)    │  │ (快照+通知渲染)     │            │ │
│  │  └────────────────────┘  └────────────────────┘            │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌──────────────────────┐  ┌──────────────────────────────────┐ │
│  │    SubAgentTool      │  │       Prompting                  │ │
│  │ (LLM可调用入口)      │  │ (系统提示组装+技能预加载)         │ │
│  └──────────┬───────────┘  └──────────────────────────────────┘ │
│             │ sync/parallel 分发                                  │
└─────────────┼────────────────────────────────────────────────────┘
              ▼
         父代理 LLM
```

## 核心组件

### AgentManifest（定义层）

```python
# definition/manifest.py
class AgentManifest(BaseModel):
    name: str                    # kebab-case 格式，验证器强制
    description: str
    context_mode: Literal["inherit", "isolated"]
    execution_mode: Literal["sync", "parallel"]
    max_inherit_messages: int          # 默认值 20
    model: str | None
    allowed_tools: list[str]
    max_turns: int | None
    timeout_seconds: float | None
    preload_skills: list[str]
    can_handoff_to: list[str]    # 验证器检查格式一致性
    system_prompt: str | None
```

验证器：
- `name` 必须匹配 `kebab-case` 正则
- `can_handoff_to` 中的每个名称也必须是 kebab-case
- `can_handoff_to` 中列出的工具必须在 `allowed_tools` 中存在

**声明式配置：.md Frontmatter**

Agent 通过 `.md` 文件的 YAML frontmatter 声明自身配置，无需编写 Python 代码。插件内置的 subagent 文件位于 `~/.prismclaw/assets/plugins/<domain>/<sub-plugin>/agents/`，工作区自定义的位于 `<workspace>/.work/agents/`。

```yaml
---
name: chart-watcher                    # 必需。小写字母+数字+连字符，1-64字符
description: 监控电子病历中的异常生命体征  # 必需。1-1024字符
when_to_use: 需要监控体征时...          # 可选。触发条件描述，0-2048字符
context_mode: isolated                  # "inherit" | "isolated"，默认 "isolated"
execution_mode: parallel                # "sync" | "parallel"，默认 "parallel"
max_inherit_messages: 20                # 1-100，仅 context_mode="inherit" 时生效
model: Qwen3.5-27B                      # 可选。指定模型
allowed_tools: [read, bash]             # 可选。白名单，None=不限制
max_turns: 15                            # 可选。最大迭代轮次
timeout_seconds: 240.0                   # 可选。超时秒数
preload_skills: [clinical-assessment]   # 可选。预加载技能
can_handoff_to: [reviewer]             # 可选。Handoff 白名单，None=开放，非空=白名单
---
```

验证规则：`name` 必须与文件名一致；`can_handoff_to` 不允许空列表；声明 `can_handoff_to` 时 `allowed_tools` 必须包含 `request_handoff`。

### SubAgentRegistry（定义层）

```python
# definition/registry.py
class SubAgentRegistry:
    # 从 <workspace>/.work/agents/ 下 BFS 扫描 <name>.md
    # 构造时自动扫描（__init__），无需显式调用 discover()
    def _do_scan_layer(self, ...)  # 统一扫描器，含硬停保护
    def diagnose(self)             # 3 类缺口分析
```

诊断类别：
1. name mismatches（文件名与 YAML name 不一致）
2. can_handoff_to unknown targets（指向未注册代理）
3. allowed_tools gaps（声明的工具不在实际注册表中）

**解析与加载流程**：

```
.md 文件 → parse_agent_md(path) → _extract_frontmatter(content) → yaml.safe_load → AgentManifest(**frontmatter, system_prompt=body)
```

关键组件：

| 组件 | 路径 | 职责 |
|------|------|------|
| `AgentManifest` | `engine/subagent/definition/manifest.py` | Pydantic 数据模型，验证所有字段 |
| `parse_agent_md()` | `engine/subagent/definition/parser.py` | 解析 .md 文件，提取 frontmatter + body |
| `SubAgentRegistry` | `engine/subagent/definition/registry.py` | 五层优先级目录扫描 + 名称去重 |
| `DefaultPluginAgentLoader` | `assembly/plugins/agent_manifest_loader.py` | 插件级 Agent 加载器 |

**五层优先级**（由低到高）：内置层（builtin）→ 插件层（plugin）→ 工作区层（workspace）→ 用户层（user）→ 会话层（session）。后加载的同名 Agent 覆盖先加载的。解析错误以 WARNING 记录并跳过。

**运行时加载**：`PluginManager 启动 → DefaultPluginAgentLoader.scan_agents_dir() → SubAgentRegistry._scan_layer(agents_path, source) → parse_agent_md()`。注册表在 `__init__` 完成后即完全填充，无懒加载。

### AgentInstance（核心执行层）

```python
# core/instance.py
class AgentInstance(LifecycleMixin, ContextMixin, LoopMixin):
    """状态化执行单元"""
    # 生命周期: created → running → completed/failed/aborted/paused
```

### LifecycleMixin

```python
# core/_lifecycle_mixin.py
class LifecycleMixin:
    def run(self)              # 主入口：深度检查、技能预加载、工具表面治理、上下文构建、循环执行、结果组装
    def _cleanup_light()                   # MCP 清理、hook 注销、文件状态缓存清除

> 注：`_collect_evidence_from_messages()` 和 `_infer_domain_fields()` 是模块级函数（`core/_lifecycle_mixin.py`），而非 LifecycleMixin 的方法。
```

`_TOOL_EVIDENCE_MAP`：工具名 → 证据提取规则的映射，用于从工具调用输出中自动识别并收集 EvidenceRef。

### ContextMixin

```python
# core/_context_mixin.py
class ContextMixin:
    def _build_context()               # 根据 context_mode 分发
    def _build_isolated_messages()     # 零上下文，全新代理
    def _build_inherit_messages()      # 加载父 session → 提取消息 → 注入 Prompt Cache 断点
    def _build_sub_context()           # 创建子 ToolContext，递增 subagent_depth
    def _resolve_model()
    def _resolve_timeout()
    def _resolve_max_turns()
    def _resolve_allowed_tools()
    def _collect_memory_injection()
```

inherit 模式通过 `inject_cache_control_breakpoints` 实现 Prompt Cache 共享，避免子代理重复传输父会话的历史消息。

### LoopMixin

```python
# core/_loop_mixin.py
class LoopMixin:
    def _loop()                    # 标准 turn 循环：超时/截止时间、LLM 调用、工具分发、进度发射
    def _settle_tool_surface()     # 工具治理：resolve_registry_tool_governance()
    def _dispatch_tool_calls()     # 委托 execute_preflighted_tool_calls()
```

工具表面治理通过 `SettledSubagentToolSurface` 实现，区分 `visible_tool_names` 和 `callable_tool_names`，支持 skill-gated 工具。

### SubAgentExecutor（核心执行层门面）

```python
# core/executor.py
class SubAgentExecutor:
    def run(self, task, *, background=False, task_id=None)
        # 单点写入记录：scheduled → running → 终态（_finalize 唯一出口）
        # 结果上盖章 SubagentEnvelopeArtifact（含 task_id）
    def launch_background(manifest, task, parent_ctx, *, call_id="", context_snapshot=None)
        # 闸门 → TaskManager.spawn → 登记 task_id → 立即返回 task_id
```

### SubagentResultEnvelope（合约层）

```python
# core/contract.py
def build_context_snapshot()          # 从父 ToolContext 构建 SubagentContextSnapshot
def coerce_context_snapshot()         # 强制类型转换
def render_context_snapshot()         # 渲染为消息格式
def render_task_envelope()            # 渲染任务封套
def normalize_result_envelope_text()  # 规范化结果文本
def parse_subagent_result_envelope()  # 解析结构化结果
def render_result_envelope_handover() # 渲染交接格式
```

`_collect_prior_evidence()` 从 session state 中提取 EvidenceRef；`_wrap_plaintext_result()` 将自由文本结果解析为 `SubagentResultEnvelope`。

### SubAgentTool（工具暴露）

```python
# tool.py
class SubAgentTool:
    name = "sub_agent"
    tool_class = "write"
    read_only_allowed = True
    side_effect = True
    effect_class = "local_mutation"
    visibility_scope = "main_only"

    # parameters 属性：动态注入 agent_name enum（来自 CapabilityCatalogService）
    def execute()               # 深度校验 → 解析代理 → 按 dispatch_mode 分发 now/background
    def _run_single_agent()     # 深拷贝上下文 → 构建 TaskRuntimeContext → executor.run()
    def _run_parallel_agents()  # asyncio.gather + 异常归一化
    def _aggregate_results()    # 合并多代理 SubagentResultEnvelope（去重证据、综合置信度）
    def _aggregate_background_results()  # 后台派发回执：task_id + 进行中状态，不含结果正文
```

### ParallelTaskRuntimeRecord

```python
# runtime/parallel.py
class ParallelTaskRuntimeRecord(BaseModel):
    task_id: str
    owner_session_id: str
    source_call_id: str | None
    kind: Literal["subagent", "fork"]
    status: Literal["scheduled", "running", "completed", "failed", "cancelled"]
    run_id: str | None
    agent_name: str | None
    agent_source: str | None
    started_at_epoch_ms / completed_at_epoch_ms: int | None
    result_summary / error_summary: str | None
    projection_payload: dict

class ParallelTaskStore(Protocol):
    def append() / get() / list_for_session()

class SessionParallelTaskStore:
    """session-store-backed 实现；append 依赖 commit_events 的内部锁，不再外层加锁"""
```

### 后台执行（P1a）

`background` 是 `sub_agent` 的调用参数，而非 manifest 属性（一次调用可含多个 agent，manifest 级开关会产生混合语义）：

```json
{"dispatch_mode": "now | background", "agents": [{"agent_name": "...", "task": "..."}]}
```

派发后立即返回任务列表，不含结果正文：

```
已派出 2 个后台子任务：
- pt:3f9a1c2d8e4b explorer（进行中）
- pt:7c21b45f0a9d executor（进行中）
完成状态会自动更新；后台派发返回 (agent_id, task_id)，用 subagent_control 的 status 查询、interrupt 停止（除 list 外必须同时提供两个键）。
```

状态机与写入约束：

```
scheduled ──► running ──► completed / failed / cancelled
```

- `launch_background` 先做本会话 `scheduled|running` 计数闸门（`max_parallel_per_turn`），再 `TaskManager.spawn(category="subagent", owner=session_id)`，并登记 `task_id → (TaskManager, managed_task_id)`（`engine/subagent/core/executor.py`）。
- 记录只由 `run()` 写：`scheduled → running` 在执行开始前落库，使闸门统计与真实执行一致；终态唯一出口 `_finalize`，正常/异常/取消三条路径统一写终态并触发投递。
- 中断经 `subagent_control` 的 `interrupt` 执行：先用 `(agent_id, task_id)` 组合键定位任务，`task_id` 与所属 `agent_id` 不匹配时返回明确错误；中断在安全边界生效，绝不沿用父任务的 `managed_task_id`（那等于终止整轮父会话）。
- 父 run 中止时，`_background_main` 监听共享 `abort` Event 并取消内层 run，长工具调用无需等待循环检查点。
- 投递：终态经 `subagent_terminal_sink` 写入会话队列并推送 `subagent_trace`（`sub_session_id="background:<task_id>"`），下一轮 note 携带 `task_id` 指针。running 阶段不推送，避免无 run 的孤立条目。
- 自动开轮不做：`_run_chat` 以用户消息为可见内容，合成「后台完成」消息会污染会话树；完成信息由下一轮 note 呈现。

部署约束：后台能力只在单副本、无网关分流时启用。注册工具时以 `settings.affinity.enabled` 推导 `allow_background`；开启即从 schema 移除 `dispatch_mode`，显式调用返回错误，不静默降级。

能力边界：子代理 loop 为 `headless=True` 且未装配 `check_hitl`，后台子代理无法向用户请求审批。

P1b（多副本）未实现，开放前必须先修复以下单进程假设：

| 失效点 | 修法 |
| --- | --- |
| 后台 task 在进程内，崩溃即丢 | 独立 `run_id` claim ownership，心跳 + TTL；启动时扫描无主非终态记录，置 `failed` 或重放 |
| 闸门「读-判-写」非原子 | Redis Lua 原子占位（检查 + 写入 running） |
| `SessionQueue` 为进程内字典 | 唤醒经网关路由到归属 worker，或改由 Redis 队列投递 |
| affinity TTL 短于任务时长 | 后台任务续期 affinity，或在任务运行期持有归属 |

### Follow-up 通知

```python
# runtime/follow_up.py
class SessionFollowUpSnapshot:
    latest_handoff_request: ...
    parallel_task_summary: ...
    parallel_task_attention: ...
    follow_up_attention: ...

def session_follow_up_snapshot()    # 构建快照
def render_follow_up_status()       # 渲染状态文本
def build_session_follow_up_note()  # 构建 session 注入笔记
```

注意逻辑优先级：handoff > fork。note 只在存在活跃任务（scheduled/running），或任务记录出现在最后一条 assistant 消息之后时注入；已被回复覆盖的旧任务不重复提示。

### 系统提示组装

```python
# prompting.py
def compose_subagent_system_prompt()    # system_prompt + preload_catalog_block + preload_instructions_block + memory_block
def resolve_preloaded_skill_context()   # 通过 skill_runtime 解析 declared_invoke_sequence
def render_preloaded_skill_catalog()
def render_preloaded_skill_instructions()
```

### 常量硬上限

```python
# core/constants.py
TIMEOUT_HARD_CEILING_S = 1800.0   # 30 分钟
MAX_TURNS_CEILING = 50
MAX_FILES_PER_LAYER = 200
MAX_FILE_BYTES = 256_000
```

## Agent 职责边界与工具权限

### 内置 Agent（3 个）

| Agent | 职责 | 工具 |
|-------|------|------|
| **explorer** | 只读核对事实与出处：位置、现状、原文 | `read`, `grep`, `glob`, `web-search-mcp:search`, `web-search-mcp:fetch` |
| **executor** | 在划定范围内修改或生成内容，并自行验证生效 | `read`, `grep`, `glob`, `write`, `edit`, `bash`, `web-search-mcp:search`, `web-search-mcp:fetch` |
| **reviewer** | 独立验收结论或产出，给出可复现依据 | `read`, `grep`, `glob`, `bash` |

协作拓扑：不设固定的角色瀑布流或必经审查节点。主 Agent 负责规划、任务拆解、
写入范围分工、冲突裁决、架构取舍与最终综合；子代理只承担边界清晰的能力单元。
三个角色共用同一套生命周期和结果契约；默认 `context_mode="isolated"`，工具权限
由 manifest 的 `allowed_tools` 显式限定。

医疗、法律等垂直领域能力由插件提供（经 `subagent_search` 发现），系统不内置领域
专家——插件侧已有专用数据源（FHIR、病历、法规库）和执业档案，内置角色无法替代，
并列存在只会造成重复派发与命名冲突。

### 生命周期与协作

| 维度 | `one_shot` | `persistent` |
|------|-----------|--------------|
| 身份 | 一次调用一个 `agent_id` / `run_id` | 同一 `agent_id` 跨多个 `run_id` |
| 后续交互 | 不支持 | `send` / `status` / `list` / `interrupt` / `resume` / `close` |
| 后台执行 | 1–8 个并行或顺序任务 | 每次 spawn 只允许一个，依赖持久 lease |
| 恢复 | 不恢复 | 未决工具副作用需 `confirm_recovery` 才能继续，且不重放原调用 |

多 Agent 结果以独立 `results[]` 返回，保留各自的证据、结论与置信度；聚合器不自动
去重、不合并结论、不计算全体最低置信度，综合由主 Agent 完成。

### 插件领域 Agent

插件子目录下的领域 Agent 聚焦特定业务域，当前无 Handoff 能力，默认
`context_mode="isolated"`。内置 Agent 的工具权限全部由 manifest 显式限定；插件 Agent
同样声明 `allowed_tools`，MCP 工具可用 `<server>:*` 前缀通配，运行时按注册表展开，
避免在清单里枚举远端工具名。

| 维度 | 内置 Agent | 插件领域 Agent |
|------|-----------|-----------|
| **定位** | 与领域无关的通用能力单元（查事实/做产出/验收） | 特定业务域的一次性工作流（监控/审查/分析） |
| **工具权限** | 显式白名单，只读角色无写入能力 | 显式白名单：本地读写 + 指定 MCP server（`<server>:*`） |
| **编排** | 主 Agent 直接派发，支持持久协作 | 通过 `subagent_search` 发现后派发 |
| **调度** | 主 Agent 按需派发 | 同样按需派发；周期运行需主 Agent 经 `schedule` 工具触发，系统无 agent 级调度器 |
| **安全性** | 工具级隔离 + 生命周期边界 | 工具级白名单 + 业务级约束（system_prompt 声明"此 Agent 不会做的事"） |

### 工具权限隔离机制

**AllowedToolsPolicy**（`schema/agent/security.py`）：

```python
class AllowedToolsPolicy(BaseModel):
    allow_all: bool = True           # True = 白名单模式关闭，允许所有工具
    allowed_names: tuple[str, ...] = ()  # 白名单
    denied_names: tuple[str, ...] = ()   # 黑名单

    def allows(self, tool_name: str) -> bool:
        if tool_name in self.denied_names: return False
        if self.allow_all: return True
        return tool_name in self.allowed_names
```

决策逻辑：黑名单优先 → `allow_all=True` 允许所有不在黑名单中的 → `allow_all=False` 仅允许白名单。

**7-Reducer Governance 链**（`engine/toolkit/registry/governance.py`）：

`AllowedToolsPolicy` 是 7-Reducer 工具治理链的第一环：

```
AllowedToolsPolicy → ActorIdentity → ReadOnlyPermission → HeadlessMode
→ DeferredTool → SkillGated → Discovery
→ ResolvedToolSet { visible, callable, searchable, denied, decision_trace }
```

| Reducer | 作用 |
|---------|------|
| `AllowedToolsPolicyReducer` | 基于白名单/黑名单过滤工具可见性和可调用性 |
| `ActorIdentityReducer` | 根据调用者身份（main/subagent）限制工具 |
| `ServerReadOnlyReducer` | 只读服务器（`server_read_only`）仅允许只读工具 |
| `HeadlessModeReducer` | 无界面模式下移除交互式工具 |
| `DeferredToolReducer` | 隐藏需要发现但尚未发现的工具 |
| `SkillGatedReducer` | 技能门控——仅激活技能声明的工具 |
| `DiscoveryReducer` | 发现机制相关限制 |

**运行时权限解析**：Agent 的 `allowed_tools` 与当前激活 Skill 的 `allowed_tools` 取交集：`effective_allowed = manifest_allowed ∩ skill_allowed`。

## 关键数据流

### 同步子代理执行流

```
SubAgentTool.execute()
  → depth 检查（max_depth）
  → CapabilityCatalogService 解析 agent_name
  → _run_single_agent()
    → 深拷贝 ToolContext
    → 构建 TaskRuntimeContext
    → SubAgentExecutor.run()
      → 创建 AgentInstance
      → ParallelTaskRecord: scheduled → running
      → LifecycleMixin.run()
        → 技能预加载（resolve_preloaded_skill_context）
        → 工具表面治理（_settle_tool_surface）
        → 上下文构建（ContextMixin._build_context）
        → turn 循环（LoopMixin._loop）
        → 证据收集（_collect_evidence_from_messages，模块级函数）
        → 结果组装
      → ParallelTaskRecord: running → completed/failed
      → SubagentEnvelopeArtifact 盖章
  → parse_subagent_result_envelope()
```

### 上下文继承流（inherit 模式）

```
ContextMixin._build_inherit_messages()
  → 从 session_store 加载父 session
  → 提取父 session 消息列表
  → inject_cache_control_breakpoints()  # Prompt Cache 断点注入
  → 追加当前任务消息
```

### 并行子代理执行流

```
SubAgentTool._run_parallel_agents()
  → asyncio.gather(*[_run_single_agent(agent) for agent in agents])
  → 异常归一化（ExceptionGroup → 单一错误）
  → _aggregate_results()
    → 合并 SubagentResultEnvelope
    → 去重 EvidenceRef
    → 综合评估 overall_confidence
```

### 后台子代理执行流

```
SubAgentTool.execute(dispatch_mode="background")
  → _run_single_agent(background=True)
    → child_context 按引用回填 abort / task_manager / subagent_terminal_sink
    → SubAgentExecutor.launch_background()
      → 闸门（本会话 scheduled|running < max_parallel_per_turn）
      → task_id = pt:xxxxxxxxxxxx
      → TaskManager.spawn("subagent:<task_id>", _background_main, category="subagent")
      → register_background_handle(task_id, manager, managed_task_id)
      → 立即返回 task_id（不等待执行）
  → _aggregate_background_results()  # 派发回执，无结果正文

后台协程 _background_main()
  → asyncio.wait(run_task, abort_watcher)
  → SubAgentExecutor.run(background=True, task_id=...)
    → 写入 scheduled → running
    → LifecycleMixin.run()
    → _finalize(status)  # completed / failed / cancelled
      → 落库终态 → subagent_terminal_sink → subagent_trace 推送
  → unregister_background_handle(task_id)
```

### 证据自动收集流

```
_collect_evidence_from_messages()（模块级函数）
  → 遍历工具调用历史
  → _TOOL_EVIDENCE_MAP 映射提取
  → _infer_domain_fields()
    → 遍历域注册的 EvidenceInferencer（核心不含领域规则）
    → 返回 {domain}.{field} → domain_extensions
```

## 对外接口

| 接口 | 类型 | 说明 |
|------|------|------|
| `SubAgentTool` (name=`sub_agent`) | 工具 | LLM 调用子代理的入口，支持 sync/parallel/background |
| `SubAgentExecutor.launch_background()` | 方法 | 后台派发：闸门 + TaskManager 登记，秒回 task_id |
| `cancel_background_task()` | 函数 | 按 task_id 经进程内句柄映射真实取消 |
| `subagent_control` | 工具 | 子代理 status / list / interrupt / send / resume / close（main_only，不 skill_gated） |
| `SubAgentRegistry.__init__()` | 方法 | 构造时自动扫描并注册所有子代理清单 |
| `SubAgentRegistry.diagnose()` | 方法 | 3 类缺口诊断 |
| `SubAgentExecutor.run()` | 方法 | 执行子代理的统一门面 |
| `ParallelTaskStore` | 协议 | 并行任务持久化接口 |
| `SubagentResultEnvelope` | 数据合约 | 结构化结果合约（evidence, findings, decisions, open_questions） |
| `AgentManifest` | 数据模型 | 子代理定义清单 |
| `build_context_snapshot()` | 函数 | 从父上下文构建快照 |
| `render_result_envelope_handover()` | 函数 | 渲染交接格式 |

## 依赖关系

```
子代理模块
  ├── engine.services.llm              — LLM 调用（client）
  ├── engine.toolkit                    — 工具注册与执行
  ├── engine.subagent.definition        — 自身定义层
  ├── engine.subagent.runtime           — 自身运行时层
  ├── schema.agent                      — RunRequest, ToolContext, AgentDeps
  ├── schema.session                    — SessionTree, SessionStore
  ├── persistence.session               — session 存储
  ├── persistence.lock                  — 分布式锁
  ├── engine.execution.skills          — 技能预加载与 runtime
  ├── engine.security.sandbox           — 沙箱策略
  ├── infra.redis                       — 并行任务、分布式锁
  └── infra.settings                   — 配置
```

## 配置项

| 配置 | 位置 | 说明 | 默认值 |
|------|------|------|--------|
| `TIMEOUT_HARD_CEILING_S` | `core/constants.py` | 超时硬上限 | `1800.0` (30 min) |
| `MAX_TURNS_CEILING` | `core/constants.py` | 最大 turn 数硬上限 | `50` |
| `MAX_FILES_PER_LAYER` | `core/constants.py` | 每层最大文件数 | `200` |
| `MAX_FILE_BYTES` | `core/constants.py` | 单文件最大字节数 | `256_000` |
| `AgentManifest.context_mode` | `.md` frontmatter | 上下文模式 | — |
| `AgentManifest.execution_mode` | `.md` frontmatter | 执行模式 | — |
| `AgentManifest.max_turns` | `.md` frontmatter | 自定义 turn 上限 | — |
| `AgentManifest.timeout_seconds` | `.md` frontmatter | 自定义超时 | — |
| `AgentManifest.preload_skills` | `.md` frontmatter | 预加载技能列表 | `[]` |
| `AgentManifest.can_handoff_to` | `.md` frontmatter | 可交接代理列表 | `[]` |
| `AgentManifest.model` | `.md` frontmatter | 指定模型 | — |
| `AgentManifest.allowed_tools` | `.md` frontmatter | 允许的工具列表 | `None` (alias "tools") |
| `SubAgentConfig.max_parallel_per_turn` | `schema/config/config.py` | 本会话同时执行的子代理上限（含后台闸门） | `5` |
