# 子代理

## 职责概述

子代理（Sub-agent）模块实现了多代理协作的核心运行机制。它负责：

- 从 `.md` 清单文件发现和注册子代理定义
- 根据上下文模式（isolated/inherit）构建子代理的初始消息上下文
- 执行子代理的 turn 循环，包括 LLM 调用、工具分发和进度上报
- 支持 sync 和 parallel 两种执行模式
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

Agent 通过 `.md` 文件的 YAML frontmatter 声明自身配置，无需编写 Python 代码。文件位于 `.prismclaw/plugins/<domain>/<sub-plugin>/agents/` 目录下。

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
can_handoff_to: [architect]             # 可选。Handoff 白名单，None=开放，非空=白名单
---
```

验证规则：`name` 必须与文件名一致；`can_handoff_to` 不允许空列表；声明 `can_handoff_to` 时 `allowed_tools` 必须包含 `request_handoff`。

### SubAgentRegistry（定义层）

```python
# definition/registry.py
class SubAgentRegistry:
    # 从 <workspace>/.prismclaw/agents/ 下 BFS 扫描 <name>.md
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
    def on_complete()                      # 完成回调系统

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
    def run(self)  # 创建 AgentInstance → 管理 ParallelTaskRuntimeRecord 生命周期
                   # scheduled → running → completed/failed/cancelled
                   # 结果上盖章 SubagentEnvelopeArtifact（含 task_id）
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
    def execute()               # 验证深度（max_depth）→ 解析代理 → 分发 sync/parallel
    def _run_single_agent()     # 深拷贝上下文 → 构建 TaskRuntimeContext → executor.run()
    def _run_parallel_agents()  # asyncio.gather + 异常归一化
    def _aggregate_results()    # 合并多代理 SubagentResultEnvelope（去重证据、综合置信度）
```

### ParallelTaskRuntimeRecord

```python
# runtime/parallel.py
class ParallelTaskRuntimeRecord(BaseModel):
    task_id: str
    owner_session_id: str
    kind: Literal["subagent", "fork"]
    status: str                     # scheduled → running → completed/failed/cancelled
    notification_state: str
    projection_payload: dict | None

class ParallelTaskStore(Protocol):
    def append() / get() / list_for_session() / cancel()

class SessionParallelTaskStore:
    """session-store-backed 实现，含分布式锁"""
```

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

注意逻辑优先级：handoff > fork，追踪 failed/cancelled 任务。

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

### 通用 Agent（4 个）

| Agent | 职责 | 独占工具 | Handoff 权限 |
|-------|------|---------|-------------|
| **architect** | 思考与规划——分析需求、设计方案、制定执行计划 | 无独占（仅 `read` + `request_handoff`） | → researcher, builder |
| **researcher** | 信息检索——从本地和外部获取事实，不做分析决策 | `grep`, `glob`, `web-search-mcp:search` | 无（不能派发 handoff） |
| **builder** | 执行——创建、修改、运行，将计划转化为实际产物 | `write`, `edit`, `bash` | 无（不能派发 handoff） |
| **auditor** | 独立验证——确认计划是否被正确执行，验证失败时反馈 | 无独占（`read` + `bash` + `request_handoff`） | → architect |

协作拓扑：architect 是唯一能派发 handoff 的通用 Agent（auditor 仅可回传 architect）。researcher/builder 是叶子节点，不能派发 handoff。

### 领域 Agent

领域 Agent 分布在插件子目录下，聚焦于特定业务域的监控/审查/分析。当前无 Handoff 能力，默认 `context_mode="isolated"`，工具权限默认不限制（除非 frontmatter 显式声明 `allowed_tools`）。

| 维度 | 通用 Agent | 领域 Agent |
|------|-----------|-----------|
| **定位** | 跨域通用能力（规划/检索/执行/验证） | 特定业务域的监控/审查/分析 |
| **工具权限** | 严格隔离，每个 Agent 只拥有其角色所需工具 | 默认不限制（除非显式声明 `allowed_tools`） |
| **Handoff** | architect 是派发中心，auditor 可回传 | 当前无 Handoff 能力 |
| **安全性** | 工具级隔离 + Handoff 白名单 | 业务级安全约束（system_prompt 声明"此 Agent 不会做的事"） |

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
| `ReadOnlyPermissionReducer` | chat 模式下仅允许只读工具 |
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

### 证据自动收集流

```
_collect_evidence_from_messages()（模块级函数）
  → 遍历工具调用历史
  → _TOOL_EVIDENCE_MAP 映射提取
  → _infer_domain_fields()（模块级函数）
    → URL 模式匹配 → medical evidence_level
    → 内容关键词 → legal_source_type
```

## 对外接口

| 接口 | 类型 | 说明 |
|------|------|------|
| `SubAgentTool` (name=`sub_agent`) | 工具 | LLM 调用子代理的入口，支持 sync/parallel |
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
  ├── engine.services.llm              — LLM 调用（stream_fn）
  ├── engine.toolkit                    — 工具注册与执行
  ├── engine.subagent.definition        — 自身定义层
  ├── engine.subagent.runtime           — 自身运行时层
  ├── schema.agent                      — RunRequest, ToolContext, AgentDeps
  ├── schema.session                    — SessionTree, SessionStore
  ├── persistence.session               — session 存储
  ├── persistence.lock                  — 分布式锁
  ├── engine.skills                     — 技能预加载与 runtime
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
