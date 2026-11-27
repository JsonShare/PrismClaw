# 02 — 技能系统 (Skill System)

> 源码目录：`backend/src/prismclaw/engine/execution/skills/`

## 职责概述

技能系统为 Agent 提供可组合的、声明式的能力扩展机制。每个 Skill 由 `SkillManifest` 声明，通过 `SkillRuntime` 构建执行策略，支持 inline（内联执行）、fork（分支执行）和 subagent（子代理执行）三种模式。技能以 canonical 名 `{expert_name}:{skill_name}` 注册；发现面采用渐进式披露（索引 → 按需加载 → `skill_search` 检索），避免把全部技能正文塞进系统提示。系统通过 workflow compliance 机制确保实际调用序列与声明的 `declared_invoke_sequence` 一致，并通过 `RuntimeControlArtifact` 从工具结果中提取技能上下文修改。

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
│  │检查  │  │档案  │  │内嵌  │  │7.5K  │  │Sk→Ag │              │
│  └──────┘  └──────┘  └──────┘  └──────┘  └──────┘              │
│                                                                 │
│  激活流程: SkillDispatch → SkillRuntime.build()                  │
│           → resolve instructions (fs/expert/MCP)                 │
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
resolve skill (canonical `{expert_name}:{skill_name}` 查找)
    → ensure body loaded (渐进式披露：frontmatter-only → full body)
    → resolve instructions (区分 filesystem/expert vs MCP)
    → expert profile（从专家包 PLUGIN.md 合并）
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
| filesystem / expert | inline refs（`references/*.md` 路径内嵌） |
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

**`_load_expert_profile()`** — 读取专家包级别的运行配置文档：

路径：`~/.prismclaw/assets/experts/<expert>/PLUGIN.md`（回退 `README.md`）

### 5. Doc Inline (`doc_inline.py`)

**`_inline_referenced_docs()`** — 扫描技能 body 中的引用路径并内嵌内容：

| 配置项 | 默认值 | 作用 |
|--------|--------|------|
| `doc_inline_min_chars` | 2500 | 单个引用文档内联预算（token 估算） |
| `doc_inline_max_chars` | 7500 | 内联总预算（token 估算） |

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

**预算：** 默认 7,500 tokens（`agent.context.budget.subcaps.skill_preload`）

### 8. Schema 合约 (`schema/services/skills.py`)

核心合约类型：

| 类型 | 关键字段 |
|------|---------|
| `SkillManifest` | `declared_invoke_sequence`, `pipeline_context` |
| `SkillInvocation` | 单次技能调用记录 |
| `SkillInvocationState` | `expert_name`, `invocation_context`, `is_workflow`, `declared_invoke_sequence`, `actual_invoke_sequence`, `compliance_result` |
| `SkillExecutionStrategy` | 执行策略（inline / fork / subagent） |
| `SkillSource` | `bundled` / `expert` / `mcp` / `project` / `personal` |

## 渐进披露与会话选择

技能正文不随系统提示全量下发，而是三层披露：

| 层 | 内容 | 上限 |
|----|------|------|
| 1 | 项目技能逐个列名（`skills_section`），专家包技能不进此段 | 项目技能数量本身有界 |
| 2 | 专家包索引（`packages_section`）：只给包名 + 描述 + 能力计数，不列技能名 | `_PACKAGE_LIST_LIMIT = 5`，描述截断 30 token |
| 3 | `skill_search` 按关键词检索具体技能，再用 `activate_skill` 激活正文 | 按需 |

检索与激活共用同一套命名解析：canonical 名 `{expert}:{skill}` 优先；只给裸名时按包别名匹配，同名多候选判为歧义并报错，不回退取第一个。会话内勾选走 `replace_session_skill_selection()`，上限 `MAX_SELECTED_SKILLS = 20`，结果写入 `SessionHeader.selected_skill_ids`；授权口径是**包级**——命中包的技能随之进入 `tool_owners`，不引入逐技能白名单。

## 关键数据流

### 技能构建流程

```
SkillManifest 查找
    ↓
ensure body loaded (frontmatter → full body)
    ↓
resolve instructions:
    ├── filesystem/expert → inline refs (doc_inline.py)
    └── MCP → prompts/get
    ↓
expert profile (profile_loader.py)
    ↓
preload sub-skills (_preload.py, 7.5K token 预算)
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
| 技能检索 | `SkillDiscoveryRuntime.search()` | 按关键词检索可用技能 |
| 会话勾选 | `replace_session_skill_selection()` | 写入 `selected_skill_ids`，上限 20 |

## 依赖关系

```
skills/
  ├── runtime.py → compliance.py, profile_loader.py, doc_inline.py,
  │                _preload.py, agent_adapter.py
  ├── skill_dispatch.py → runtime.py (RuntimeControlArtifact 提取)
  ├── compliance.py → (schema types)
  ├── profile_loader.py → (filesystem read)
  ├── doc_inline.py → (filesystem read, 2500/7500 token 预算)
  ├── agent_adapter.py → (schema types)
  └── _preload.py → runtime.py (子技能内容获取)
```

**外部依赖：**
- `engine/execution/loop/` — Agent Loop 调用 SkillRuntime
- `engine/execution/dispatch/` — 工具结果中提取 RuntimeControlArtifact
- `engine/experts/` — 专家包加载与 `PLUGIN.md` 运行配置读取
- `schema/services/skills.py` — SkillManifest 合约
- MCP SDK — `prompts/get` 协议

## 配置项

| 配置项 | 位置 | 默认值 | 说明 |
|--------|------|--------|------|
| `doc_inline_min_chars` | `doc_inline.py`（`subcaps`） | 2500 | 单个引用文档内联预算（token 估算） |
| `doc_inline_max_chars` | `doc_inline.py`（`subcaps`） | 7500 | 内联总预算（token 估算） |
| `skill_preload` | `_preload.py`（`subcaps`） | 7500 | 子技能预加载 token 预算 |
| `declared_invoke_sequence` | `SkillManifest` | — | 声明的工具调用序列 |
