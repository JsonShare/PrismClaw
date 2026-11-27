# 03 — 专家体系 (Expert System)

> 源码目录：`backend/src/prismclaw/engine/experts/`
>
> **[查看交互式架构图](../diagrams/03-expert-system.html)**

## 职责概述

PrismClaw 的一级能力概念收敛为四个：**专家（Expert）**、**专家团（Team）**、**技能（Skill）**、**MCP 连接器（Connector）**。专家体系负责这四类能力从磁盘发现、校验、注册，到会话级作用域派生与运行期治理的完整链路。

核心约束：

- **专家是会话属性**，不是进程级开关。事实源是 `SessionHeader.expert_id`（`schema/agent/session.py`）；同一会话单专家互斥，选择与取消都只改写 header。
- **一次 run 只派生一份作用域**。`EnvironmentCatalog.snapshot()` 组合「当前有哪些专家」与「当前哪些能力被允许」，`build_session_scope()` 据此产出不可变三态作用域 `Scope = NoExpertScope | ActiveScope | RejectedScope`，人格、工具面、技能、子代理全部消费同一份，禁止各自重算。被拒态在类型上不持有工具面，消费点不需要再判 `restricted`。
- **专家包不携带 hooks / MCP / 命令 / LSP 声明**。进程内 hook 通道是独立的通用机制，专家只能通过包名参与作用域过滤。

## 架构总览

```
┌────────────────────────────────────────────────────────────────────┐
│                     Expert System 总览                              │
│                                                                    │
│  backend/.prismclaw/experts/<pkg>/                                │
│    .plugin/plugin.json  agents/*.md  skills/<name>/SKILL.md         │
│    PLUGIN.md                                                        │
│         │                                                          │
│         ▼                                                          │
│  ExpertDiscoveryEngine.discover()                                  │
│         │  ExpertID(name@experts)                                  │
│         ▼                                                          │
│  ExpertLoader + DeclarativeLoadStrategy                            │
│    _resolve_canonical_manifest → validate_expert_package           │
│    （禁止 hooks / MCP / commands / LSP）                            │
│         │                                                          │
│         ├──────────────► ExpertRegistry（只读索引）                 │
│         └──────────────► ExtensionManager                          │
│                          └─ DescriptorRegistry                     │
│                             experts / skills / agents / commands   │
│                             （commands 桶因 schema 禁止恒为空）     │
│         │                                                          │
│         ▼                                                          │
│  CapabilityPolicyRegistry（capability_policy.yaml 启停）           │
│         │                                                          │
│         ▼                                                          │
│  EnvironmentCatalog.snapshot()                                     │
│         │                                                          │
│         ▼                                                          │
│  build_session_scope(header, catalog) → 三态 Scope                 │
│    ├─ NoExpertScope（未选专家：全量工具面，非降级）                 │
│    ├─ ActiveScope（已激活：expert / persona_source / 受限工具面）  │
│    │    tool_surface（active / deferred / member）                 │
│    │    tool_owners（专家包名集合，工具归属校验）                   │
│    └─ RejectedScope（选了但不可用：code + reason，无工具面）        │
│         │                                                          │
│         ├─► ExpertPromptSlotCollector（Role Override + agent md）  │
│         ├─► RuntimeCatalog / SkillRuntime（技能检索与激活）         │
│         ├─► TeamRegistry / TeamGuard（团队协作）                   │
│         └─► ConnectorRegistry / MCPClientManager（连接器）          │
└────────────────────────────────────────────────────────────────────┘
```

## 核心组件

### 1. 清单与身份

| 文件 | 职责 | 关键符号 |
|------|------|---------|
| `engine/experts/paths.py` | 专家包清单路径常量（零依赖叶子模块） | `MANIFEST_REL_PATH = .plugin/plugin.json` |
| `engine/experts/manifest/expert_schema.py` | 专家包清单与 Agent MD 的加载期校验 | `ExpertPackageManifest`、`ExpertMember`、`ExpertTeamInfo`、`validate_expert_package` |
| `engine/experts/manifest/manifest.py` | 专家包 manifest 的不透明解析模型 | `ExpertManifest`、`parse_manifest` |
| `engine/experts/manifest/substitution.py` | 专家内容中的 `${VAR}` 变量替换 | `substitute_variables`、`build_expert_variables` |
| `engine/experts/registry/identity.py` | 专家包唯一标识 | `ExpertID`、`EXPERT_SOURCE`、`make_expert_id` |

`validate_expert_package()` 是包级唯一守门函数：校验名称 kebab-case、semver、`agentName` 命中 `agents/<agentName>.md` 且不为通用名 `team-lead`、团队 `teamInfo.leadAgent == agentName`、`displayDescription` 中文长度（40–50 仅为 warning）、`tags`/`quickPrompts` 恰好 3 项、团队 `teamInfo`/`members` 与 agent 文件一一对应，并在解析前显式拒绝 `hooks`、`mcp`、`mcpServers`、`commands`、LSP 字段及 `hooks/`、`commands/`、`.plugin/mcp.json`、`.plugin/.lsp.json`、`.lsp.json` 路径。`members[]` 输入接受 `name` 或 `displayName`，输出统一 `displayName`。

### 2. 发现、加载与注册

| 文件 | 职责 | 关键符号 |
|------|------|---------|
| `engine/experts/loading/discovery_engine.py` | 只扫描专家包目录，产出可加载身份 | `ExpertDiscoveryEngine`、`ExpertLoader`、`ExpertDiscoveryResult` |
| `engine/experts/loading/load_strategy.py` | 加载策略接口与声明式实现 | `LoadStrategy`、`DeclarativeLoadStrategy`、`default_strategies` |
| `engine/experts/loading/_declarative_load.py` | 声明式包的结构化加载 | `_load_declarative_expert`、`_build_manifest_and_capability` |
| `engine/experts/loading/_manifest_parse.py` | 定位 `.plugin/plugin.json` 并触发包级校验 | `_resolve_canonical_manifest` |
| `engine/experts/loading/load_result.py` | 加载结果与结构化错误 | `ExpertPackage`、`PackageLoadResult`、`PackageError` |
| `engine/experts/loading/agent_loader.py` | Agent MD 读取接口 | `ExpertAgentLoader` |
| `engine/experts/registry/expert_registry.py` | 已加载专家包的只读索引，构造后不读盘 | `ExpertRegistry`、`ExpertRegistryIssue` |
| `engine/experts/registry/descriptors.py` | 四类描述符数据模型 | `ExpertDescriptor`、`SkillDescriptor`、`CommandDescriptor`、`AgentDescriptor` |
| `engine/experts/registry/registry.py` | 专家包能力注册表 | `DescriptorRegistry` |
| `engine/experts/lifecycle/state.py` | 生命周期状态机 | `ExpertLifecycleState`、`assert_transition` |
| `engine/experts/ingest/path_remap.py` | 移植包的路径重映射规则 | `DEFAULT_REMAP_RULES`、`parse_remap_rules` |

`ExpertRegistry.get()` 支持 `expert.name` 与 `expert_id` 双键命中；跨来源命中同一 ID 视为歧义并抛 `EXPERT_ID_AMBIGUOUS`。`known_ids(kind)` 是 `CapabilityPolicyRegistry` 校验配置项的目录来源（`expert` / `team` / `skill`）。

### 3. 能力注册与目录

| 文件 | 职责 | 关键符号 |
|------|------|---------|
| `engine/execution/capabilities/extension_manager.py` | 专家包能力注册中心；技能注册时加 `{expert}:{skill}` 前缀 | `ExtensionManager`、`get_extension_manager` |
| `engine/execution/capabilities/catalog.py` | 运行期统一目录：技能、工具、子代理 | `RuntimeCatalog`、`SkillCatalogEntry`、`AgentCatalogEntry`、`SkillResolution` |
| `engine/execution/capabilities/assembly.py` | 从 provider / extension / 子代理注册表组装目录 | `assemble_capability_catalog` |
| `engine/execution/capabilities/discovery_runtime.py` | 按关键词检索工具 / 技能 / 子代理 | `ToolDiscoveryRuntime`、`SkillDiscoveryRuntime`、`SubagentDiscoveryRuntime` |
| `engine/execution/capabilities/delta_projection.py` | 每轮只投影「延迟工具解锁」增量 | `build_capability_delta_blocks` |

### 4. 会话作用域与选择

| 文件 | 职责 | 关键符号 |
|------|------|---------|
| `engine/experts/scope.py` | 纯函数派生一次 run 的唯一三态作用域 | `build_session_scope`、`Scope`、`NoExpertScope`、`ActiveScope`、`RejectedScope`、`ToolSurface`、`tool_surface_of` |
| `engine/experts/selection.py` | 会话级专家选择与取消 | `select_session_expert`、`get_session_expert_state`、`ExpertState` |
| `engine/experts/codes.py` | 统一错误码与异常 | `ExpertErrorCode`、`ExpertError` |

`build_session_scope()` 的判定顺序：激活来源合法性（仅接受 `user`）→ 注册表命中（`EXPERT_NOT_FOUND`）→ 部署启停策略（`MODULE_DISABLED`）→ 组装 `tool_owners`（专家包名 + 用户显式勾选技能所属包），最后落在三态之一：

- **`NoExpertScope`**：用户未选专家。工具面全量（`restricted=False`），但 `tool_owners` 仍只含显式勾选技能所属包——不是降级路径。
- **`ActiveScope`**：专家已激活。`tool_surface` 受限，`persona_source` 指向 `agents/<agentName>.md`（缺失为 `None`），团队包额外暴露 `team_name` / `lead_agent`。
- **`RejectedScope`**：选了但不可用。只带 `code`、`expert_id`、`reason`，**没有 `tool_surface` 字段**；归属门控因 `tool_owners` 为空而天然全拒。

`tool_surface_of(scope)` 是唯一的面读取入口：`None` 只表示无作用域（headless，不限制）；被拒态返回空面而不是 `None`，`None` 在治理链语义是放行全部，用它表示被拒会构成「少给变多给」。

取消专家即把 `expert_id` 置空并把 `expert_suppressed_by_admin` 置真、记录 `last_user_prompt_expert_selection`，没有 pending 标记，也不注入取消文案。

### 5. 人格注入

| 文件 | 职责 | 关键符号 |
|------|------|---------|
| `engine/experts/prompt_slot.py` | 生成专家人格槽位文本 | `ExpertPromptSlotCollector`、`ROLE_OVERRIDE`、`build_expert_agent_prompt`、`strip_expert_agent_frontmatter` |
| `engine/execution/query/context_preparation.py` | 人格与固定段落超过上下文 50% 时整段置空并上报 | `EXPERT_PROMPT_TOO_LONG` |

注入内容 = Role Override 头 + 剥离 YAML frontmatter 的 `agents/<agentName>.md` 正文。人格源路径由 `ActiveScope.persona_source` 指向；专家不可用时作用域退化为 `RejectedScope`，人格槽返回空串并上报 `EXPERT_UNAVAILABLE`。

### 6. 技能

| 文件 | 职责 | 关键符号 |
|------|------|---------|
| `engine/execution/skills/runtime.py` | 技能执行策略构建与调用 | `SkillRuntime.build_skill_execution`、`invoke`、`resolve_skill`、`_ensure_body_loaded` |
| `engine/execution/skills/selection.py` | 会话技能勾选（上限 20） | `replace_session_skill_selection`、`MAX_SELECTED_SKILLS` |
| `engine/execution/skills/expert_utils.py` | 从 `SkillSource.EXPERT` 清单反解专家包名 | `_resolve_expert_name` |
| `engine/execution/skills/agent_adapter.py` | 技能清单与子代理清单互转 | `derive_skill_agent_name`、`build_agent_manifest_from_skill` |
| `engine/execution/skills/skill_dispatch.py` | 工具结果后的技能上下文注入与策略执行 | `_apply_context_modifiers_from_results`、`ContextInjection` |
| `engine/execution/skills/compliance.py` | 声明序列与实际调用序列比对 | `compute_workflow_compliance` |
| `engine/execution/skills/_preload.py` | 子技能预加载（按 token 预算渐进披露） | `_build_preload_messages` |
| `engine/execution/skills/doc_inline.py` | 内嵌技能 body 引用的文档 | `_inline_referenced_docs` |
| `engine/execution/skills/profile_loader.py` | 读专家包 `PLUGIN.md`（回退 `README.md`） | `_load_expert_profile` |

技能 canonical 名为 `{expert_name}:{skill_name}`；`SkillSource` 取值为 `bundled` / `expert` / `mcp` / `project` / `personal` / `managed` / `workspace` / `builtin`（`schema/agent/domains.py`，存储字段与 Web 分组共用同一枚举）。技能归属按包计算：专家包技能随包进入 `tool_owners`，专家激活即决定可激活的技能集合。三层披露与检索细节见 [02-skill-system.md](02-skill-system.md) 的「渐进披露与会话选择」。

调用策略四值 `auto/user_only/model_only/disabled` 由 `resolve_invocation_policy` 统一解析（显式字段优先，其次 WorkBuddy 的 `disable` / `disable-model-invocation` / `user-invocable` 布尔字段，未知显式值降级为 `disabled`）。`filter_eligible` 只做环境过滤；`skill_search` 与 `activate_skill` 只放行 `auto/model_only`，`GET /api/skills/list` 只放行 `auto/user_only`（`scope=assets|all` 只区分来源）。

### 7. MCP 连接器

| 文件 | 职责 | 关键符号 |
|------|------|---------|
| `engine/mcp/connectors.py` | `backend/configs/mcp.json` 的唯一只读视图与启用判定 | `ConnectorRegistry`、`DEFAULT_MCP_CONFIG_PATH` |
| `engine/mcp/client_manager.py` | 连接生命周期与工具注册 | `MCPClientManager` |
| `backend/configs/mcp.json` | 部署级连接器配置 | `connectors` 段 |

`ConnectorRegistry` 拒绝未知顶层键，要求每个连接器条目可被 `McpServerConfig` 校验；`servers` 只返回 `enabled` 项。连接器工具不进入专家工具白名单，而是按专家包作用域单独治理。

### 8. 团队与子代理

| 文件 | 职责 | 关键符号 |
|------|------|---------|
| `engine/experts/scope.py` | 团队工具面（`TEAM_LEAD_TOOLS`、`DEFERRED_TEAM_TOOLS`） | `tool_surface_for_expert` |
| `engine/subagent/team_registry.py` | run 级团队注册（Redis）与团队清单读取 | `TeamRegistry`、`TeamRegistration`、`get_team_registry`、`load_team_manifest` |
| `engine/subagent/team_guard.py` | 派发前的团队边界校验 | `guard_dispatch`、`TeamGuardError` |
| `engine/subagent/team_tools.py` | 团队创建/删除工具 | `TeamCreateTool`、`TeamDeleteTool` |
| `engine/subagent/tool_surface.py` | 子代理唯一工具面（只含 `EXPERT_MODE_TOOLS`） | `SUBAGENT_TOOL_SURFACE` |
| `engine/subagent/definition/manifest.py` | 子代理定义；成员预加载技能字段 | `AgentManifest.preload_skills`（frontmatter alias `skills`） |
| `engine/subagent/prompting.py` | 成员预加载技能解析与系统提示拼装 | `resolve_preloaded_skill_context`、`compose_subagent_system_prompt` |
| `engine/execution/loop/preparer.py` | 团队专家会话启动时注册 `TeamCreateTool` | `RunPreparer._prepare` |

团队约束：

- 成员之间不可互相连接。`guard_dispatch()` 对 `is_member` 或调用者非 lead 一律抛 `TEAM_SPAWN_BY_MEMBER`。
- 成员工具面固定为 `EXPERT_MODE_TOOLS`，不派生、不控制其他子代理。
- 成员预加载技能：裸名优先解析 `{team_name}/{裸名}`，未命中再回落通用解析；同名多版本由解析器判为歧义。

### 9. 部署策略、读侧快照与治理

| 文件 | 职责 | 关键符号 |
|------|------|---------|
| `infra/config/capability_policy.py` | `backend/configs/capability_policy.yaml` 的只读策略注册表，按 mtime/size 热加载 | `CapabilityPolicyRegistry`、`CapabilityPolicySnapshot`、`CapabilityPolicyEntry`、`CAPABILITY_KINDS` |
| `infra/environment_catalog.py` | 组合专家目录与策略的唯一读侧快照入口 | `EnvironmentCatalog`、`EnvironmentCatalogSnapshot` |
| `engine/execution/loop/deps.py` | 运行依赖上的作用域构建门面 | `ExpertBundle.build_session_scope` |
| `engine/execution/loop/tools.py` | 工具归属越权拦截 | `allowed_tool_owners_denied` |
| `engine/toolkit/registry/governance.py` | 工具治理与归属过滤 | `allowed_tool_owners` 过滤 |

治理语义：`Scope.tool_owners` 只含专家包名（及显式勾选技能所属包）；未注册的工具名在治理规范化阶段被丢弃，不进入可见工具集；工具实际归属不在允许集合内时，调用以 `allowed_tool_owners_denied:<owner>` 阻断（fail-closed）。

### 10. 装配与热更新

| 文件 | 职责 | 关键符号 |
|------|------|---------|
| `assembly/experts/expert_lifecycle.py` | 构建 runner 级专家运行时 bundle | `ExpertLifecycleManager`、`ExpertRuntimeBundle`、`ExpertRuntimeCandidate` |
| `assembly/experts/runtime_assembly.py` | 冷启动组装运行时与策略/目录 | `build_expert_runtime`、`ExpertRuntimeAssembly` |
| `assembly/experts/load_wiring.py` | 发现 + 加载流水线接线 | `assemble_expert_load_result` |
| `assembly/experts/agent_manifest_loader.py` | Agent MD 的默认加载实现 | `DefaultExpertAgentLoader` |
| `assembly/experts/reload_service.py` | 运行时重建与应用 | `reload_expert_runtime`、`reload_app_expert_runtime`、`sync_expert_lifecycle_settings` |
| `assembly/runtime_coordinator.py` | 订阅发布文档并重载运行时 | `ModuleRuntimeCoordinator`、`collect_local_package_roots` |
| `infra/config/module_release.py` | 版本化发布文档与 checksum | `ReleaseDocument`、`ModuleReleaseStore`、`create_release_document`、`build_local_release_document` |

`ReleaseDocument{version, packages, capability_policy, metadata, checksum}` 的 checksum 覆盖 payload（packages + capability_policy + metadata），不含 version。热更新链路：发布文档 → `ModuleRuntimeCoordinator`（pubsub + 轮询）→ 校验本地包 hash → 重建 `ExpertRuntimeBundle` → 重建 `EnvironmentCatalog`。因为 checksum 校验内容，旧文档内容变化后必须重新发布，不能只提升 version。

### 11. 通用进程内 Hook 通道

专家包**不声明** hooks，但引擎保留一条独立的进程内 hook 通道，供全局行为扩展与平台能力使用：

| 文件 | 职责 | 关键符号 |
|------|------|---------|
| `engine/hooks/__init__.py` | Hook 注册、事件枚举与分发 | `HookRegistry`、`HookDispatcher`、`HookEvent`、`AgentHook` |
| `engine/hooks/schema.py` | Hook 输入/输出契约 | `HookContext`、`HookResult`、`HookOutcome`、`PermissionDecision`、`HookDecision` |
| `engine/hooks/handlers.py` | Handler 统一接口 | `HookHandler` |

`HookDispatcher.dispatch(..., active_experts=...)` 支持按专家包名过滤：`active_experts` 为 `None` 时全部执行；否则归属包不在激活集的 handler 被静默跳过，全局 handler 恒执行。

## 专家包磁盘结构

```
backend/.prismclaw/experts/<pkg>/
├── .plugin/plugin.json     # 清单（固定路径）
├── PLUGIN.md               # 包级指令 / 执业档案（读取时回退 README.md）
├── agents/
│   └── <agentName>.md      # 成员定义：frontmatter + 人格正文
├── skills/
│   └── <name>/SKILL.md     # 技能定义
└── avatars/                # 头像等静态资源
```

清单固定位于 `.plugin/plugin.json`，其它路径与目录不参与识别。必填字段（节选）：`name`、`version`、`description`、`expertType`（`agent` / `team`）、`agentName`、`displayName`、`profession`、`displayDescription`、`avatar`、`categoryId`、`categoryName`、`defaultInitPrompt`；团队包另需 `teamInfo{leadAgent, memberAgents}` 与 `members[]`（成员展示名输入 `name` 或 `displayName` 均可，输出 `displayName`）。

禁止项：`hooks` / `mcp` / `mcpServers` 字段，`commands` 字段与 `commands/` 目录，LSP 相关字段与 `.plugin/mcp.json`、`.plugin/.lsp.json`、`.lsp.json` 文件；Agent MD 禁止声明 `tools` / `allowed_tools` / `allowedTools`，且 `name` 必须等于文件名。

## 关键数据流

### 冷启动装配

```
build_expert_runtime()
  → ExpertLifecycleManager.get_bundle()
  → assemble_expert_load_result(directories)
      → ExpertDiscoveryEngine.discover() → ExpertID 列表
      → ExpertLoader + DeclarativeLoadStrategy
      → _resolve_canonical_manifest() → validate_expert_package()
  → ExpertRegistry(load_result.all_experts, issues)
  → ExtensionManager.load_from_load_result()
  → CapabilityPolicyRegistry(catalog=ExpertRegistry)
  → EnvironmentCatalog(expert_registry, capability_policy_registry)
```

### 会话选择与取消

```
select_session_expert(session_id, expert_id)
  ├─ expert_id 非空：registry.get → 启停校验 → header.expert_id = name
  │                   → 发布 ExpertStateChanged(active)
  └─ expert_id 为空：header.expert_id = None, suppressed = True
                      → 发布 ExpertStateChanged(suppressed) + ExpertCancelled
```

### 一次 run 的作用域派生

```
EnvironmentCatalog.snapshot()
  → build_session_scope(SessionHeader, catalog)
      → _activation_rejection()      # 仅允许 user 来源
      → ExpertRegistry.get()         # EXPERT_NOT_FOUND / EXPERT_UNAVAILABLE
      → CapabilityPolicy.states      # MODULE_DISABLED
      → 三态判定：
          ├─ 有 rejection → RejectedScope(code, expert_id, reason)
          ├─ 无 expert_id → NoExpertScope(全量工具面, tool_owners)
          └─ 命中专家     → ActiveScope(tool_surface_for_expert(), tool_owners,
                                        expert, persona_source)
  → ToolContext.session_scope = Scope（消费点统一经 tool_surface_of() 取面）
  → 团队专家：TeamCreateTool.register(...)
```

### 人格注入

```
SessionHeader.expert_id
  → ExpertPromptSlotCollector.collect(session)
  → resolve_expert_prompt(expert_id, expert_name_hint)
  → build_expert_agent_prompt(agent_md)
      → strip_expert_agent_frontmatter()
      → ROLE_OVERRIDE + body
  → context_preparation 预算守卫
      estimated ≥ context_window × 0.5 → 置空 + EXPERT_PROMPT_TOO_LONG
```

### 技能检索与激活

```
skills_section      → 项目技能逐个列名
packages_section    → 专家包名 + 描述 + 计数（≤5）
skill_search        → SkillDiscoveryRuntime.search()
activate_skill      → RuntimeCatalog.resolve_skill_reference()
                       → 技能名优先，包别名次之（多成员即歧义）
                    → SkillRuntime.build_skill_execution()
                    → SkillRuntime.invoke() → compute_workflow_compliance()
```

### 团队协作

```
团队专家会话启动
  → load_team_manifest() → teamInfo(leadAgent, memberAgents)
  → TeamCreateTool.register() → TeamRegistry(Redis, session+run 作用域)
  → lead 派发成员 → guard_dispatch()
      ├─ 成员/非 lead 调用 → TEAM_SPAWN_BY_MEMBER
      ├─ 未注册团队 → TEAM_NOT_CREATED
      └─ 成员不属于当前包 → TEAM_MEMBER_NOT_IN_PACKAGE
```

### 热更新

```
ReleaseDocument(version, packages, capability_policy, metadata, checksum)
  → ModuleReleaseStore.publish() / publish_and_wait()
  → ModuleRuntimeCoordinator（pubsub 订阅 + 30s 轮询）
  → validate_local_packages(document, package_roots)
  → 重建 ExpertRuntimeBundle（ExtensionManager / ExpertRegistry / SkillProvider）
  → 重建 EnvironmentCatalog（读侧快照必须指向新注册表）
  → refresh_runner_capabilities()
```

## 对外接口

| 接口 | 函数 / 类 | 说明 |
|------|----------|------|
| 会话选择 | `select_session_expert()` | 设置或取消会话专家 |
| 会话状态 | `get_session_expert_state()` | 读取 `expert_id` / 来源 / 抑制标记 / 勾选技能 |
| 作用域 | `build_session_scope()`、`ExpertBundle.build_session_scope()`、`tool_surface_of()` | 一次 run 的三态作用域与唯一取面入口 |
| 人格 | `ExpertPromptSlotCollector.collect()` | 生成 Role Override + agent 正文 |
| 专家目录 | `ExpertRegistry.get/list_experts/known_ids/issue_for` | 只读索引与配置校验目录 |
| 运行时构建 | `ExpertLifecycleManager.get_bundle/rebuild/build_candidate/commit` | bundle 生命周期 |
| 能力注册 | `ExtensionManager.iter_skill_descriptors/iter_agent_descriptors` | 技能与子代理描述符 |
| 技能解析 | `RuntimeCatalog.resolve_skill_reference()` | 技能名 / 包别名统一解析 |
| 技能执行 | `SkillRuntime.build_skill_execution()`、`invoke()` | 构建并执行技能策略 |
| 技能勾选 | `replace_session_skill_selection()` | 上限 20，校验归属与启停 |
| 团队 | `TeamCreateTool.register()`、`TeamDeleteTool` | 创建 / 删除 run 级团队 |
| 团队校验 | `guard_dispatch()` | 派发边界校验 |
| 连接器 | `ConnectorRegistry.settings()` | 读取部署级 MCP 配置 |
| 启停策略 | `CapabilityPolicyRegistry.is_enabled/state_of/filter_enabled` | 专家 / 团队 / 技能启停 |
| 读侧快照 | `EnvironmentCatalog.snapshot()` | 组合专家目录与策略 |
| 热更新 | `reload_expert_runtime()`、`ModuleReleaseStore.publish()` | 重建运行时与发布文档 |
| 包校验 | `validate_expert_package()` | 专家包结构守门 |

## 依赖关系

```
engine/experts/
  ├── paths.py                → (零依赖)
  ├── codes.py                → (零依赖)
  ├── registry/identity.py    → (零依赖)
  ├── manifest/expert_schema.py → registry/identity.py, schema/shared, subagent/definition/manifest.py
  ├── manifest/manifest.py    → registry/identity.py
  ├── loading/discovery_engine.py → load_strategy.py, load_result.py, paths.py, identity.py
  ├── loading/load_strategy.py → _declarative_load.py, agent_loader.py, load_result.py
  ├── loading/_manifest_parse.py → manifest/*, expert_schema.py, paths.py
  ├── registry/expert_registry.py → load_result.py, codes.py
  ├── registry/registry.py    → descriptors.py
  ├── selection.py            → codes.py, load_result.py, expert_registry.py,
  │                             trajectory/bus, capability_policy.py, session.py
  ├── scope.py                → codes.py, load_result.py, expert_registry.py,
  │                             capability_policy.py, environment_catalog.py, session.py
  ├── prompt_slot.py          → codes.py, session.py, trajectory/bus
  └── ingest/path_remap.py    → (路径规则)
```

跨包依赖：

- `engine/execution/capabilities/` — 目录组装、技能/工具/子代理检索与增量投影。
- `engine/execution/skills/` — `SkillRuntime`、技能激活与遵从度。
- `engine/execution/loop/` — `engine/execution/loop/preparer.py` 派生作用域并注册团队工具；`engine/execution/loop/tools.py` 做归属拦截。
- `engine/subagent/` — 团队注册、派发校验、成员工具面、预加载技能。
- `engine/hooks/` — 独立通用 hook 通道，按专家包名过滤。
- `engine/mcp/` — 连接器注册表与客户端管理。
- `infra/config/capability_policy.py`、`infra/environment_catalog.py`、`infra/config/module_release.py` — 启停策略、读侧快照、发布协议。
- `assembly/experts/` — 冷启动装配、运行时重建与热更新接线。
- `schema/agent/session.py` — `SessionHeader` 的专家字段事实源。

## 配置项

| 配置项 | 位置 | 默认值 / 取值 | 说明 |
|--------|------|---------------|------|
| 能力启停策略 | `backend/configs/capability_policy.yaml`（缺失回退 `backend/configs/capability_policy.example.yaml`） | `defaults` / `bulk` / `overrides` / `strict` | 专家、团队、技能的部署级开关；`strict` 下未显式启用即禁用 |
| 策略热加载间隔 | `CapabilityPolicyRegistry.reload_interval_seconds` | 5.0s | 按 mtime + size 判断是否重载 |
| MCP 连接器 | `backend/configs/mcp.json` | `connectors` 对象 | 唯一连接器来源，未知顶层键报错 |
| 专家包根目录 | `settings.experts.asset_experts_dir` | `~/.prismclaw/assets/experts` | 专家包扫描根 |
| 项目技能目录 | `settings.skills.workspace_skills_dir` | — | 三层披露第一层的项目技能来源 |
| 子代理资产目录 | `settings.subagent.asset_subagents_dir` | — | 核心子代理扫描根 |
| 清单相对路径 | `MANIFEST_REL_PATH` | `.plugin/plugin.json` | 声明式加载识别条件 |
| Agent MD 目标 / 上限 token | `engine/experts/manifest/expert_schema.py` | 2000 / 4000 | 超出上限拒绝加载，超目标仅告警 |
| 中文简介长度 | `engine/experts/manifest/expert_schema.py` | 建议 40–50 字 | 超出仅 `warning` |
| 技能勾选上限 | `engine/execution/skills/selection.py` | 20 | `MAX_SELECTED_SKILLS` |
| 包索引上限 | `engine/execution/request/system_prompt.py` | 5 | `_PACKAGE_LIST_LIMIT` |
| Hook 分发超时 | `engine/hooks/__init__.py` | 30.0s | `DEFAULT_DISPATCH_TIMEOUT` |
| 发布轮询间隔 | `ModuleRuntimeCoordinator` | 30.0s | 同时订阅 Redis 失效频道 |
