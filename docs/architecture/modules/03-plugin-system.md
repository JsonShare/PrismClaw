# 03 — 插件系统 (Plugin System)

> 源码目录：`backend/src/prismclaw/engine/plugins/`

## 职责概述

插件系统为 PrismClaw 提供可扩展的能力注册与生命周期管理。支持三种插件方言（Canonical / Trae / BARE_SKILLS），通过信任层级实现安全门控，通过 Hook 系统实现行为拦截与注入，通过 Circuit Breaker 保护系统免受故障插件影响。

## 架构总览

**[查看交互式架构图](../diagrams/03-plugin-system.html)**

```
┌─────────────────────────────────────────────────────────────────┐
│                     Plugin System 总览                           │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  DiscoveryEngine.discover() → to_activate / to_deactivate │   │
│  └──────────────────────────┬───────────────────────────────┘   │
│                             │                                    │
│  ┌──────────────────────────▼───────────────────────────────┐   │
│  │  PluginLoadOrchestrator: 三源并行加载 + 合并               │   │
│  │  LoadStrategy: NativePython / Declarative / Trae           │   │
│  └──────────────────────────┬───────────────────────────────┘   │
│                             │                                    │
│  ┌──────────────────────────▼───────────────────────────────┐   │
│  │  Ingest Pipeline: detect dialect → convert → canonical    │   │
│  │  CANONICAL / TRAE / BARE_SKILLS → 规范化副本              │   │
│  └──────────────────────────┬───────────────────────────────┘   │
│                             │                                    │
│  ┌──────────────────────────▼───────────────────────────────┐   │
│  │  Activator: install → load → activate → deactivate        │   │
│  │  生命周期: UNINSTALLED → INSTALLED → LOADED → ACTIVE      │   │
│  └──────────────────────────┬───────────────────────────────┘   │
│                             │ register_fn                       │
│  ┌──────────────────────────▼───────────────────────────────┐   │
│  │  DescriptorRegistry: plugins / skills / commands /         │   │
│  │                       agents / hooks                       │   │
│  └──┬──────────────┬──────────────┬─────────────────────────┘   │
│     │              │              │                              │
│     ▼              ▼              ▼                              │
│  ┌──────┐    ┌──────────┐   ┌──────┐                           │
│  │Trust │    │  Hooks    │   │Sandbox│                           │
│  │Guard │    │Dispatch   │   │Policy │                           │
│  │层级  │    │+Breaker   │   │约束   │                           │
│  └──────┘    └──────────┘   └──────┘                            │
│                                                                 │
│  生命周期: Discovery → Ingest → Load → Activate → Hook/Trust    │
└─────────────────────────────────────────────────────────────────┘
```

### 核心子目录

| 子目录 | 职责 |
|--------|------|
| `loading/` | 发现引擎、加载策略、激活器 |
| `lifecycle/` | 生命周期状态机管理 |
| `registry/` | 能力注册表（plugins/skills/commands/agents/hooks） |
| `trust/` | 信任层级与沙箱策略 |
| `hooks/` | Hook Schema、Factory、Handlers、Circuit Breaker |
| `ingest/` | 方言检测与规范化管道 |
| `manifest/` | 插件 Manifest 模型与解析 |

## 核心组件

### 1. Plugin Discovery Engine (`loading/discovery_engine.py`)

**`PluginDiscoveryEngine`** — 配置驱动的插件发现：

```python
discover() → PluginDiscoveryResult
    # to_activate: 需要激活的插件列表
    # to_deactivate: 需要停用的插件列表
    # errors: 发现过程中的错误
```

### 2. Plugin Activator (`lifecycle/activator.py`)

**`PluginActivator`** — 管理生命周期状态机：

```
UNINSTALLED → INSTALLED → LOADED → ACTIVE → INACTIVE
```

| 方法 | 状态转换 |
|------|---------|
| `install()` | UNINSTALLED → INSTALLED |
| `load()` | INSTALLED → LOADED |
| `activate(register_fn)` | LOADED → ACTIVE |
| `deactivate(unregister_fn)` | ACTIVE → INACTIVE |
| `unload()` | INACTIVE → INSTALLED |

**核心数据类：** `PluginActivationRecord`

### 3. Descriptor Registry (`registry/registry.py`)

**`DescriptorRegistry`** — 五类能力注册表：

| 类别 | 说明 |
|------|------|
| plugins | 插件描述符 |
| skills | 技能描述符 |
| commands | 命令描述符 |
| agents | 代理描述符 |
| hooks | Hook 描述符 |

**查询方法：** `by_id()`, `by_name()`, `by_state()`

### 4. 信任层级 (`trust/trust.py`)

**`TRUST_HIERARCHY`** — 三级信任模型：

| 信任级别 | 来源 | 说明 |
|----------|------|------|
| TRUSTED | builtin | 内置插件，最高信任 |
| VERIFIED | marketplace | 市场发布，已验证 |
| UNTRUSTED | session/local | 本地/会话插件，最低信任 |

**双层门控：**

1. **`is_source_allowed_by_policy()`** — 企业黑白名单策略
2. **`resolve_workspace_trusted()`** — 文件夹级信任
3. **`is_source_trusted_for_load()`** — 双层门控（来源策略 + 工作区信任）

### 5. PluginSandboxPolicy (`trust/sandbox.py`)

插件沙箱策略约束：

| 字段 | 说明 |
|------|------|
| `allowed_paths` | 允许访问的路径列表 |
| `blocked_paths` | 禁止访问的路径列表 |
| `blocked_commands` | 禁止执行的命令列表 |
| `network_access` | 网络访问策略 |
| `max_memory_mb` | 最大内存限制 |
| `timeout_seconds` | 执行超时 |

**`PluginSandbox`** 隔离器方法：
- `validate_command()` — 命令校验
- `validate_path_access()` — 路径访问校验
- `build_safe_env()` — 构建安全环境变量

### 6. Hook Schema (`hooks/schema.py`)

**`HookContext`** — Hook 输入上下文：

| 字段 | 类型 | 说明 |
|------|------|------|
| `event` | `str` | 事件名（PreToolUse / PostToolUse 等） |
| `session_id` | `str` | 会话 ID |
| `tool_name` | `str \| None` | 工具名 |
| `tool_input` | `dict \| None` | 工具参数 |
| `skill_name` | `str \| None` | 技能名 |
| `plugin_id` | `str \| None` | 插件 ID |

**`HookResult`** — Hook 输出结果：

| 字段 | 类型 | 说明 |
|------|------|------|
| `outcome` | `HookOutcome` | SUCCESS / BLOCKING / NON_BLOCKING_ERROR / CANCELLED |
| `decision` | `HookDecision` | APPROVE / BLOCK |
| `permission_decision` | `PermissionDecision` | ALLOW / DENY / ASK |
| `updated_input` | `dict \| None` | 修改后的工具参数（PreToolUse） |
| `additional_context` | `str \| None` | 注入的额外上下文 |
| `system_message` | `str \| None` | 系统消息 |
| `stop_reason` | `str \| None` | 停止原因 |

### 7. Hook Factory (`hooks/factory.py`)

**声明式 Hook 从配置到可执行的唯一转换层。**

**`normalize_hooks_config()`** — 归一化 hooks.json：

```
{"hooks": {"PreToolUse": [...]}} → {"PreToolUse": [...]}
```

**`build_hook_handlers()`** — 配置 → Handler 实例：

| spec 类型 | Handler |
|-----------|---------|
| `type: command` / 含 `command` | `ShellCommandHookHandler` |
| `type: http` / 含 `url` | `HttpHookHandler` |
| callable | `CallbackHookHandler` |
| 含 `matcher` 的组 | `MatcherFilteredHandler` 包装 |

### 8. Hook Handlers (`hooks/handlers.py`)

**`HookHandler`** (ABC) — 统一接口：`async def execute(self, context: HookContext) -> HookResult`

| Handler | 执行方式 |
|---------|---------|
| `ShellCommandHookHandler` | 子进程执行，stdin 注入 HookContext JSON，stdout 解析 HookResult |
| `HttpHookHandler` | HTTP POST 请求，body 为 HookContext JSON |
| `CallbackHookHandler` | Python callable 直接调用 |
| `MatcherFilteredHandler` | 带 matcher 正则过滤的装饰器 |

**ShellCommandHookHandler 特性：**
- 环境变量注入：`PRISMCLAW_PLUGIN_ROOT` / `PRISMCLAW_PLUGIN_DATA` / `PRISMCLAW_PLUGIN_OPTION_*`
- 变量替换：`${PRISMCLAW_PLUGIN_ROOT}` + `${user_config.KEY}`
- 超时保护：默认 30s

### 9. Hook Circuit Breaker (`hooks/circuit_breaker.py`)

**`HookCircuitBreaker`** — 熔断状态机：

```
CLOSED (正常) → OPEN (熔断) → HALF_OPEN (尝试恢复) → CLOSED
```

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `failure_threshold` | 5 | 连续失败次数阈值 |
| `recovery_timeout` | 30.0s | 熔断恢复时间 |
| `half_open_max_requests` | 3 | HALF_OPEN 允许的最大请求数 |
| `request_timeout` | 30.0s | 单个请求超时 |
| `success_threshold` | 3 | HALF_OPEN 恢复所需连续成功次数 |

**`HookCircuitBreakerManager`** — 为每个 `(plugin_id, event_name, hook_id)` 组合维护独立熔断器。

### 10. Ingest Pipeline (`ingest/pipeline.py`)

**`ingest_plugin()`** — 从任意方言物化规范化插件副本：

| 方言 | 处理 |
|------|------|
| `CANONICAL` | 直接复制 |
| `TRAE` | 复制 + 生成 canonical manifest |
| `BARE_SKILLS` | 复制 + 合成最小 manifest |
| `UNKNOWN` | 抛出 `IngestError` |

核心原则：**源目录永不修改**，副本输出到 `output_root`。

### 11. Load Strategy (`loading/load_strategy.py`)

**`PluginLoadStrategy`** (ABC) — 加载策略接口：

| 策略 | 识别条件 | 加载方式 |
|------|---------|---------|
| `DeclarativePluginLoadStrategy` | `.plugin/plugin.json` 或 `plugin.json` | 声明式加载 |
| `NativePythonPluginLoadStrategy` | `main.py` | importlib 动态加载 + SDK 全局状态收集 |
| `TraePluginLoadStrategy` | `.trae-plugin/plugin.json` | 内存转换为 canonical 后声明式加载 |

**`default_strategies()`** — 默认策略列表顺序：NativePython → Declarative → Trae

**v5 渐进式披露：** 技能加载时只解析 frontmatter（`body=""`），content pipeline 参数暂存到 `pipeline_context`，body 按 `SkillRuntime._ensure_body_loaded()` 按需加载。

### 12. Plugin Manifest (`manifest/manifest.py`)

**`PluginManifest`** — Pydantic 模型：

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | `str` | 插件名（必须通过 `is_valid_plugin_name` 校验） |
| `hooks` | `str \| dict \| list \| None` | Hook 配置 |
| `commands` | `str \| list \| dict \| None` | 命令配置（支持三种格式） |
| `agents` | `str \| list \| None` | 代理配置 |
| `skills` | `str \| list \| None` | 技能配置 |
| `mcp_servers` | `str \| dict \| list \| None` | MCP 服务器配置 |
| `user_config` | `dict[str, UserConfigOption] \| None` | 用户可配置选项 |
| `dependencies` | `list[str] \| None` | 依赖插件 |
| `porting` | `dict \| None` | Marketplace 级 porting 配置 |

**`parse_manifest()`** — 从原始字典解析为 `PluginManifest` 模型。

## 关键数据流

### 插件发现与激活流程

```
PluginDiscoveryEngine.discover()
    ↓
PluginDiscoveryResult (to_activate / to_deactivate / errors)
    ↓
PluginActivator:
    install() → load() → activate(register_fn)
    ↓
DescriptorRegistry.register() — 五类能力注册
```

### Hook 执行流程

```
hooks.json 配置
    ↓
normalize_hooks_config() — 归一化
    ↓
build_hook_handlers() — 构建 Handler 实例
    ↓
HookCircuitBreakerManager.get_or_create() — 获取熔断器
    ↓
HookCircuitBreaker.can_execute() → True
    ↓
Handler.execute(HookContext) → HookResult
    ↓
熔断器 record_success() / record_failure()
```

### 插件 Ingest 流程

```
source_dir
    ↓
detect_dialect() → PluginDialect (CANONICAL / TRAE / BARE_SKILLS / UNKNOWN)
    ↓
shutil.copytree() — 物化副本（源目录不修改）
    ↓
按方言处理：
    CANONICAL → 直接可用
    TRAE → write_canonical_manifest()
    BARE_SKILLS → _write_bare_skills_manifest()
    ↓
IngestResult (source / output_dir / manifest_path / dialect)
```

## 对外接口

| 接口 | 函数/类 | 说明 |
|------|---------|------|
| 发现 | `PluginDiscoveryEngine.discover()` | 插件发现 |
| 生命周期 | `PluginActivator.install/load/activate/deactivate/unload()` | 状态管理 |
| 注册查询 | `DescriptorRegistry.by_id/by_name/by_state()` | 能力查询 |
| 信任检查 | `is_source_trusted_for_load()` | 双层门控 |
| 沙箱校验 | `PluginSandbox.validate_command/validate_path_access/build_safe_env()` | 安全约束 |
| Hook 构建 | `build_hook_handlers()` | 配置 → Handler |
| Hook 执行 | `HookHandler.execute()` | 统一执行接口 |
| 熔断保护 | `HookCircuitBreaker.execute_with_protection()` | 熔断包装 |
| Ingest | `ingest_plugin()` | 方言规范化 |
| 加载 | `PluginLoadStrategy.load()` | 策略化加载 |
| Manifest | `parse_manifest()` | 解析插件清单 |

## 依赖关系

```
plugins/
  ├── loading/discovery_engine.py → loading/load_strategy.py
  ├── lifecycle/activator.py → registry/registry.py
  ├── registry/registry.py → registry/identity.py
  ├── trust/trust.py → (schema types)
  ├── trust/sandbox.py → (schema types)
  ├── hooks/schema.py → (standalone, no engine deps)
  ├── hooks/factory.py → hooks/handlers.py, hooks/schema.py
  ├── hooks/handlers.py → hooks/schema.py, manifest/substitution.py
  ├── hooks/circuit_breaker.py → (standalone)
  ├── ingest/pipeline.py → ingest/detect.py, ingest/trae_convert.py
  ├── loading/load_strategy.py → manifest/manifest.py, loading/_declarative_load.py,
  │                               loading/_manifest_parse.py, loading/agent_loader.py
  └── manifest/manifest.py → registry/identity.py
```

**外部依赖：**
- `engine/execution/skills/` — SkillRuntime 使用 PluginProfile
- `engine/security/` — 信任层级与沙箱策略
- `schema/services/skills.py` — SkillManifest 合约
- MCP SDK — `prompts/get` 协议

## 配置项

| 配置项 | 位置 | 默认值 | 说明 |
|--------|------|--------|------|
| Hook 超时 | `handlers.py` | 30.0s | Shell/HTTP hook 执行超时 |
| `failure_threshold` | `circuit_breaker.py` | 5 | 熔断连续失败阈值 |
| `recovery_timeout` | `circuit_breaker.py` | 30.0s | 熔断恢复时间 |
| `half_open_max_requests` | `circuit_breaker.py` | 3 | HALF_OPEN 最大请求数 |
| `request_timeout` | `circuit_breaker.py` | 30.0s | 单请求超时 |
| `success_threshold` | `circuit_breaker.py` | 3 | HALF_OPEN 恢复成功数 |
