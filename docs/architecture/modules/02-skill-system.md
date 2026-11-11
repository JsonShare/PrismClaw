# 02 — 技能系统 (Skill System)

> 源码目录：`backend/src/prismclaw/engine/execution/skills/`

## 职责概述

技能系统为 Agent 提供可组合的、声明式的能力扩展机制。每个 Skill 由 `SkillManifest` 声明，通过 `SkillRuntime` 构建执行策略，支持 inline（内联执行）、fork（分支执行）和 subagent（子代理执行）三种模式。系统通过 workflow compliance 机制确保实际调用序列与声明的 `declared_invoke_sequence` 一致，并通过 `RuntimeControlArtifact` 从工具结果中提取技能上下文修改。

## 架构总览

**[查看交互式架构图](../diagrams/02-skill-system.html)**

```
┌─────────────────────────────────────────────────────────────────┐
│                      Skill System 总览                           │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │              SkillDispatch (工具结果后处理)                 │  │
│  │  RuntimeControlArtifact → 三阶段：上下文修改/策略执行/记录  │  │
│  └─────────────────────────┬─────────────────────────────────┘  │
│                            │ 触发                                │
│  ┌─────────────────────────▼─────────────────────────────────┐  │
│  │              SkillRuntime (技能运行时核心)                  │  │
│  │  build_skill_execution() → SkillExecutionStrategy           │  │
│  │  invoke() → compute_workflow_compliance() → 执行            │  │
│  └──┬──────────┬──────────┬──────────┬──────────┬────────────┘  │
│     │          │          │          │          │                │
│     ▼          ▼          ▼          ▼          ▼                │
│  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐             │
│  │Compli│  │Profil│  │DocIn │  │Preloa│  │Agent │              │
│  │ance  │  │eLoad │  │line  │  │d     │  │Adapt │              │
│  │合规  │  │er    │  │引用  │  │Build │  │er    │              │
│  │检查  │  │档案  │  │内嵌  │  │30K   │  │Sk→Ag │              │
│  └──────┘  └──────┘  └──────┘  └──────┘  └──────┘              │
│                                                                 │
│  激活流程: SkillDispatch → SkillRuntime.build()                  │
│           → resolve instructions (fs/plugin/MCP)                 │
│           → ProfileLoader + DocInline + Preload                  │
│           → Compliance(declared vs actual)                       │
│           → execute (inline / fork / subagent)                   │
└─────────────────────────────────────────────────────────────────┘
```

## 核心组件

### 1. SkillRuntime (`runtime.py`)

技能运行时核心，提供两个关键方法：

**`build_skill_execution()`** — 构建完整执行策略：

```
resolve skill (SkillManifest 查找)
    → ensure body loaded (渐进式披露：frontmatter-only → full body)
    → resolve instructions (区分 filesystem/plugin vs MCP)
    → plugin instructions (从 PluginProfile 合并)
    → preload + profile (子技能预加载 + 上下文构建)
    → SkillExecutionStrategy
```

**`invoke()`** — 入口函数，含 workflow compliance 计算：
- 执行技能策略
- 调用 `compute_workflow_compliance()` 对比 declared vs actual
- 记录 `InvokedSkillState`

**`_resolve_instructions()`** — 指令解析策略：

| 来源 | 解析方式 |
|------|---------|
| filesystem / plugin | inline refs（`references/*.md` 路径内嵌） |
| MCP | `prompts/get` 协议获取 |

### 2. Skill Dispatch (`skill_dispatch.py`)

工具结果后处理的三阶段：

| 函数 | 作用 |
|------|------|
| `_apply_context_modifiers_from_results()` | 从工具结果的 `RuntimeControlArtifact` 中提取技能上下文修改 |
| `_execute_skill_execution_strategys_from_results()` | 执行非 inline 技能策略（fork / subagent） |
| `_record_invoked_skill_from_runtime_control()` | 记录已调用技能到 `InvokedSkillState` |

**`ContextInjection`** 数据类：封装技能上下文注入信息。

### 3. Workflow Compliance (`compliance.py`)

**`compute_workflow_compliance()`** — declared vs actual 序列比对：

| 输出字段 | 含义 |
|----------|------|
| `missing` | 声明但未实际调用的工具 |
| `extra` | 实际调用但未声明的工具 |
| `order_match` | 调用顺序是否匹配 |
| `compliance_ratio` | 合规比率（0.0~1.0） |

### 4. Profile Loader (`profile_loader.py`)

**`_load_plugin_profile()`** — 读取插件级别的 profile 文档：

路径：`.prismclaw/plugins/config/<plugin>/prismclaw.md`

### 5. Doc Inline (`doc_inline.py`)

**`_inline_referenced_docs()`** — 扫描技能 body 中的引用路径并内嵌内容：

| 常量 | 值 | 作用 |
|------|-----|------|
| `_MAX_SINGLE_REF_BYTES` | 10 KB | 单个引用文件最大字节数 |
| `_MAX_TOTAL_INLINE_BYTES` | 30 KB | 内嵌总字节数上限 |

引用路径模式：`references/*.md`

### 6. Agent Adapter (`agent_adapter.py`)

将 SkillManifest 适配为 AgentManifest：

- **`build_agent_manifest_from_skill()`** — 从 SkillManifest 构建 AgentManifest
- **`derive_skill_agent_name()`** — 规范化技能 agent 名称

### 7. Preload Builder (`_preload.py`)

**`_build_preload_messages()`** — 渐进式预加载子技能内容：

| 级别 | 策略 | 说明 |
|------|------|------|
| full | 完整内容 | 内容在预算内 |
| partial | 部分内容 | 超预算截断 |
| name listing | 仅名称 | 预算不足时降级 |

**字符预算：** 30K 字符

### 8. Schema 合约 (`schema/services/skills.py`)

核心合约类型：

| 类型 | 关键字段 |
|------|---------|
| `SkillManifest` | `declared_invoke_sequence`, `pipeline_context` |
| `SkillInvocation` | 单次技能调用记录 |
| `SkillInvocationState` | `plugin_name`, `invocation_context`, `is_workflow`, `declared_invoke_sequence`, `actual_invoke_sequence`, `compliance_result` |
| `SkillExecutionStrategy` | 执行策略（inline / fork / subagent） |

## 关键数据流

### 技能构建流程

```
SkillManifest 查找
    ↓
ensure body loaded (frontmatter → full body)
    ↓
resolve instructions:
    ├── filesystem/plugin → inline refs (doc_inline.py)
    └── MCP → prompts/get
    ↓
plugin instructions (profile_loader.py)
    ↓
preload sub-skills (_preload.py, 30K 预算)
    ↓
SkillExecutionStrategy 构建
```

### 技能执行与合规检查流程

```
SkillRuntime.invoke()
    ↓
执行 SkillExecutionStrategy (inline / fork / subagent)
    ↓
工具结果后处理:
    ├── _apply_context_modifiers_from_results() → RuntimeControlArtifact 提取
    ├── _execute_skill_execution_strategys_from_results() → fork/subagent 执行
    └── _record_invoked_skill_from_runtime_control() → InvokedSkillState 记录
    ↓
compute_workflow_compliance():
    declared_invoke_sequence vs actual_invoke_sequence
    → missing / extra / order_match / compliance_ratio
```

## 对外接口

| 接口 | 函数/类 | 说明 |
|------|---------|------|
| 技能构建 | `SkillRuntime.build_skill_execution()` | 构建技能执行策略 |
| 技能执行 | `SkillRuntime.invoke()` | 执行技能 |
| 合规检查 | `compute_workflow_compliance()` | declared vs actual 序列比对 |
| 上下文修改 | `_apply_context_modifiers_from_results()` | 从工具结果提取技能上下文 |
| 非内联执行 | `_execute_skill_execution_strategys_from_results()` | fork/subagent 模式执行 |
| Agent 适配 | `build_agent_manifest_from_skill()` | Skill → Agent manifest 转换 |
| 文档内嵌 | `_inline_referenced_docs()` | 引用文件内嵌到 body |
| 预加载 | `_build_preload_messages()` | 子技能渐进式预加载 |

## 依赖关系

```
skills/
  ├── runtime.py → compliance.py, profile_loader.py, doc_inline.py,
  │                _preload.py, agent_adapter.py
  ├── skill_dispatch.py → runtime.py (RuntimeControlArtifact 提取)
  ├── compliance.py → (schema types)
  ├── profile_loader.py → (filesystem read)
  ├── doc_inline.py → (filesystem read, 10K/30K 限制)
  ├── agent_adapter.py → (schema types)
  └── _preload.py → runtime.py (子技能内容获取)
```

**外部依赖：**
- `engine/execution/loop/` — Agent Loop 调用 SkillRuntime
- `engine/execution/dispatch/` — 工具结果中提取 RuntimeControlArtifact
- `engine/plugins/` — PluginProfile 读取
- `schema/services/skills.py` — SkillManifest 合约
- MCP SDK — `prompts/get` 协议

## 配置项

| 配置项 | 位置 | 默认值 | 说明 |
|--------|------|--------|------|
| `_MAX_SINGLE_REF_BYTES` | `doc_inline.py` | 10 KB | 单个引用文件最大字节数 |
| `_MAX_TOTAL_INLINE_BYTES` | `doc_inline.py` | 30 KB | 内嵌总字节数上限 |
| Preload 字符预算 | `_preload.py` | 30K | 子技能预加载字符预算 |
| `declared_invoke_sequence` | `SkillManifest` | — | 声明的工具调用序列 |
