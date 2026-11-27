# 08 — MCP 集成 (MCP Integration)

## 职责概述

MCP（Model Context Protocol）集成模块负责将外部 MCP 服务器桥接到 PrismClaw 的工具系统中，管理服务器生命周期、工具注册与调度、网络安全策略。它通过 Mixin 架构将配置、连接、生命周期、注册与适配器逻辑解耦，支持 stdio / SSE / Streamable HTTP / HTTP 四种传输协议，并提供沙箱隔离与网络 ACL 安全层。

核心能力：
- **服务器生命周期管理**：非阻塞后台 supervisor + 连接超时 + 自动重连
- **工具桥接**：MCPToolAdapter 将 MCP 工具映射为 PrismClaw Tool Protocol
- **双名模式**：`name`（注册表键 `server:tool`）与 `_sdk_key`（SDK 调度键）分离
- **沙箱隔离**：bubblewrap 文件系统/网络/环境变量隔离
- **网络 ACL**：Fail-Closed 白名单 + 通配符匹配 + per-server 域名策略
- **Composite/Declarative 模式**：多管理器聚合 + 纯声明式只读模式
- **运行时遥测**：per-server 工具调用/失败/超时/拒绝统计
- **部署级连接器注册表**：`backend/configs/mcp.json` + `ConnectorRegistry`，只暴露 `enabled=true` 的 connector
- **工具归属**：部署级 MCP 工具的 `expert_name` 为 `None`（核心工具），不参与专家包工具作用域门控

## 架构总览

**[查看交互式架构图](../diagrams/08-mcp-integration.html)**

```
MCPClientManager（每个进程一个实例，五个 Mixin 合成）
  ├── ConfigMixin        沙箱策略 · 遥测记录 · 占位符与环境变量解析
  ├── ConnectionMixin    连接 / 重连 / 死亡处理
  ├── LifecycleMixin     supervisor · connect_all · ready 门控
  ├── RegistryMixin      工具注册注销 · 日志脱敏 · 状态查询
  └── AdapterMixin       MCPToolAdapter 构建（双名模式）
        │
        ▼
  ConnectorRegistry（backend/configs/mcp.json）
        │  只暴露 enabled=true 的 connector
        ▼
  McpSettings → 每 server 一份 McpServerConfig（transport / timeout / sandbox）
        │
        ├─ ToolRegistry    注册 server:tool，供 LLM 调度
        ├─ SandboxPolicy   wrap_mcp_stdio() 沙箱包装
        └─ McpNetworkAcl   fail-closed 域名白名单
```

## 核心组件

### MCPClientManager（`engine/mcp/client_manager.py`）

MCP 服务器生命周期管理器，每个 PrismClaw 进程一个实例。

- **类**：`MCPClientManager(ConfigMixin, ConnectionMixin, LifecycleMixin, RegistryMixin, AdapterMixin)`
- **构造参数**：`settings: McpSettings`、`task_manager: TaskManagerProtocol`、`expert_name: str | None = None`（部署级连接器不传，工具归属 `None`）
- **核心状态**：
  - `_adapters: dict[str, list[Any]]` — per-server 适配器列表
  - `_servers: dict[str, ServerStatus]` — 服务器状态
  - `_locks: dict[str, asyncio.Lock]` — per-server 互斥锁
  - `_registry: _ToolRegistryProto | None` — 工具注册表
  - `_group: ClientSessionGroup` — MCP SDK 会话组
  - `_sessions: dict[str, mcp.ClientSession]` — per-server 会话
  - `_runtime_telemetry: dict[str, Any]` — 运行时遥测
  - `ready: asyncio.Event` — 就绪事件
- **架构不变量**：`prismclaw.engine.*` 不得直接导入此模块，通过 `deps.mcp_death_handler` 和 `external_tool_registrar` 回调解耦

### PrismClawClientSessionGroup（`engine/mcp/client_manager.py`）

SDK ClientSessionGroup 的子类，修复 `__aexit__` 双重关闭问题。

- 修复：只关闭 `_exit_stack`（owner 模式）或只关闭 `_session_exit_stacks`（非 owner 模式）
- 关闭后清理 `_sessions`、`_tool_to_session`、`_tools`、`_resources`、`_prompts`

### MCPToolAdapter（`engine/mcp/adapter.py`）

MCP 工具到 PrismClaw Tool Protocol 的桥接适配器。

- **类**：`MCPToolAdapter`（dataclass）
- **双名模式**（J1 修复）：
  - `name`：规范名 `{server_name}:{remote_tool_name}`（如 `filesystem:read_file`），注册表键
  - `_sdk_key`：SDK 调度键（默认等于 `remote_tool.name`），`execute()` 使用
- **属性**：
  - `server_name` / `remote_tool` / `server_config` / `group` / `sdk_key`
  - `expert_name` — 归属专家包；部署级连接器为 `None`（核心工具）
  - `description` — 从 `remote_tool.description` 获取
  - `parameters` — 从 `remote_tool.inputSchema` 获取
  - `tool_class` — read / write（由 `_derive_tool_class()` 决定）
  - `side_effect` — tool_class != "read"
  - `deferred` — True（MCP 工具默认延迟）
- **核心方法**：`execute(args, context)` → ToolResult
  - 超时控制：`asyncio.timeout(server_config.call_timeout_seconds)`
  - 死服务器检测：`_DEAD_SERVER_EXCEPTIONS`（ClosedResourceError, BrokenResourceError, EOFError）
  - McpError 连接丢失检测：code in {-32000, -32099}
  - OSError broken pipe 检测：errno in {EPIPE, ECONNRESET, EBADF}
  - 内容转换：`_convert_content_block()`（TextContent→TextBlock, ImageContent→ImageBlock, AudioContent→文本标记, EmbeddedResource/ResourceLink→文本引用）
  - 结构化内容：`structuredContent.file_change` → UiArtifact(file_changes)
  - 运行时观察：`_note_result()` → `runtime_observer.note_tool_result()`
- **校验**：remote_tool.name 不允许包含 `:`（与命名空间分隔符冲突）

### ConfigMixin（`engine/mcp/_mixin_config.py`）

配置与遥测 Mixin。

- **核心方法**：
  - `set_sandbox_policy()` — 注入 SandboxPolicy
  - `_require_sandbox_policy()` — 断言沙箱策略已设置
  - `sandbox_backend` / `sandbox_policy` — 属性
  - `_emit_sandbox_startup_advisories()` — 沙箱启动建议
  - `_initialize_server_statuses()` — 初始化服务器状态
  - `_telemetry_for()` — 获取 per-server 遥测
  - `note_tool_decision()` — 记录工具决策（approved/denied）
  - `note_tool_result()` — 记录工具结果（success/error/timeout/server_dead）
  - `_enabled_servers()` — 获取已启用服务器列表
  - `_get_lock()` — 获取 per-server 异步锁

### ConnectionMixin（`engine/mcp/_mixin_connection.py`）

连接管理 Mixin。

- **核心方法**：
  - `_connect_one()` — 连接单个服务器
  - `_build_server_params()` — 构建服务器参数（stdio / sse / streamable-http）
  - `restart_server()` — 重启服务器（通过 supervisor command 或直接）
  - `_restart_server_impl()` — 重启实现（旧适配器注销 → 重连 → 新适配器注册）
  - `_handle_server_death()` — 处理服务器死亡（注销适配器 + 标记 failed）
- **连接流程**：
  1. 检查 unfilled placeholder（模板占位符检测）
  2. 环境变量解析（`_substitute_env_placeholder()`）
  3. 构建服务器参数
  4. 沙箱包装（stdio + sandbox.enabled）
  5. 超时连接
  6. 构建适配器 + 注册
- **stderr 捕获**：`_open_stderr_capture()` / `_read_stderr_tail()` — 子进程错误诊断

### LifecycleMixin（`engine/mcp/_mixin_lifecycle.py`）

生命周期 Mixin。

- **核心方法**：
  - `start_background()` — 非阻塞启动 supervisor 任务
  - `connect_all()` — 连接所有已启用服务器
  - `_run_supervisor()` — supervisor 主循环（connect_all → command queue）
  - `is_ready()` / `wait_ready()` — 就绪检查
  - `shutdown()` — 关闭（取消 supervisor + 排空 MCP 任务 + 关闭会话组）
- **启动摘要**：`_log_startup_summary()` — 连接/失败/禁用/工具总数
- **工具数警告**：`_TOOL_COUNT_WARN_THRESHOLD = 100` — 超过时警告 prompt budget 压力

### RegistryMixin（`engine/mcp/_mixin_registry.py`）

注册表与查询 Mixin。

- **核心方法**：
  - `attach_and_register()` — 绑定 ToolRegistry 并批量注册适配器（幂等）
  - `get_logs()` — 获取服务器日志（含脱敏）
  - `list_servers()` — 列出所有服务器状态（含运行时遥测）
  - `get_status()` — 获取单个服务器状态
  - `get_session()` — 获取 ClientSession
  - `list_mcp_prompts()` — 列出所有已连接服务器的 prompts
  - `get_prompt_text()` — 执行 prompts/get
  - `get_prompt_text_threadsafe()` — 线程安全版本（用于 worker 线程）
  - `connection_summary()` — 连接摘要统计
- **日志脱敏**：`_redact_logs()` — 脱敏环境变量/参数/header 值

### AdapterMixin（`engine/mcp/_mixin_adapter.py`）

适配器构建 Mixin。

- **核心方法**：`_build_adapters_for_session()`
  - 遍历 `_group.tools`，匹配 session → 构建 MCPToolAdapter
  - 跳过含 `:` 的工具名（ValueError catch）

### DeclarativeMCPManager（`engine/mcp/_declarative.py`）

纯声明式 MCP 管理器，仅提供状态查询，不实际连接。

- **类**：`DeclarativeMCPManager`
- **适用场景**：远程 MCP 服务器（由外部平台管理连接）
- **支持接口**：attach_and_register（空操作）、list_servers、get_status、connection_summary、is_ready、restart_server（始终失败）、get_logs（空）、shutdown（空）

### CompositeMCPManager（`engine/mcp/_composite.py`）

复合 MCP 管理器，聚合多个子管理器。

- **类**：`CompositeMCPManager`
- **核心方法**：
  - `_find_manager()` — 按服务器名查找所属子管理器
  - `attach_and_register()` / `list_servers()` / `get_status()` / `connection_summary()` / `is_ready()` / `wait_ready()` / `restart_server()` / `get_logs()` / `get_session()` / `list_mcp_prompts()` / `get_prompt_text_threadsafe()` / `note_tool_decision()` / `note_tool_result()` / `shutdown()`
- 所有方法委托给对应子管理器，实现多源聚合

### McpNetworkAcl（`engine/proxy/mcp_network_acl.py`）

§6.3 MCP 网络 ACL 白名单中心。

- **类**：`McpNetworkAcl`
- **构造参数**：`default_policy: str = "deny"` — 默认 deny-all
- **核心方法**：
  - `add_server()` — 注册 per-server 域名白名单
  - `add_global_domains()` — 添加全局白名单
  - `is_allowed()` — 检查 host 是否在白名单中（fnmatch 通配符匹配）
  - `list_allowed_domains()` — 列出白名单域名
- **内置默认白名单**：
  - `his` → `["his.hospital.com"]`
  - `rms` → `["rms.lawfirm.com"]`
  - `email` → `["smtp.office365.com"]`
- **Fail-Closed**：未注册 server → 返回 False
- **匹配顺序**：全局白名单 → server-specific 白名单 → 未指定 server 时扫描所有已注册域名

### ConnectorRegistry（`engine/mcp/connectors.py`）

`backend/configs/mcp.json` 的唯一连接器注册表视图，也是启用判定的唯一来源。

- **类**：`ConnectorRegistry`
- **配置文件**：`backend/configs/mcp.json`，结构 `{"connectors": {<name>: {...}}}`
- **只读视图**：`servers` 属性只返回 `enabled=true` 的条目；`has_enabled` / `settings()` 供 `app/_lifespan.py` 决定是否创建 `MCPClientManager`
- **不变量**：未启用的 connector 不连接、不注册工具

### McpSettings / McpServerConfig（`engine/mcp/settings.py` + `schema/services/mcp_settings.py`）

MCP 配置模型；运行期 `McpSettings` 由 `ConnectorRegistry.settings()` 构造。

- **类**：`McpSettings`
  - `enabled: bool` — 是否存在启用连接器（派生值）
  - `servers: dict[str, McpServerConfig]` — 已启用连接器配置
- **类**：`McpServerConfig`
  - `enabled: bool` / `transport: str`（stdio / sse / streamable-http / http）
  - `command: str | None` / `args: list[str]` / `env: dict[str, str]`
  - `url: str | None` / `headers: dict[str, str]`
  - `connect_timeout_seconds: float` / `call_timeout_seconds: float`
  - `forced_tool_class: Literal["read", "write"] | None`
  - `trust_annotations: bool` / `server_read_only: bool` / `deferred: bool`
  - `sandbox: McpSandboxConfig`
- **类**：`McpSandboxConfig`
  - `enabled: bool` / `filesystem: dict` / `network: dict` / `env_allowlist: list[str]`
- **环境变量解析**：`_substitute_env_placeholder()` — `{env:VAR_NAME}` 占位符
  - resolved / literal / missing-env-var 三种状态

### _types（`engine/mcp/_types.py`）

MCP 类型定义。

- **数据类**：
  - `MCPRuntimeTelemetry`（total_tool_calls, total_denied_calls, total_failed_calls, total_timed_out_calls, last_tool_name, last_tool_call_at, ...）
  - `ServerStatus`（name, status, tool_count, last_connect_at, reason, runtime）
  - `RestartResult`（ok, tool_count, reason）
  - `ConnectionSummary`（n_connected, n_failed, n_pending, n_disabled, total_tools）
- **类型别名**：
  - `ServerStatusLiteral` = Literal["connected", "failed", "disabled", "pending"]
  - `_SupervisorCommand` = tuple[str, str, asyncio.Future | None]
- **常量**：
  - `_STDERR_BUFFER_LINES = 200`
  - `_TOOL_COUNT_WARN_THRESHOLD = 100`

## 关键数据流

### 1. 服务器启动连接流

```
app._lifespan() → MCPClientManager(settings, task_manager)
  → start_background()
    → _run_supervisor()
      → connect_all()
        → for each enabled server:
          → _connect_one(name, config)
            → [placeholder check] _is_unfilled_placeholder()
            → [env resolve] _substitute_env_placeholder()
            → _build_server_params() (stdio/sse/http)
            → [sandbox wrap] sandbox_policy.wrap_mcp_stdio()
            → group.connect_to_server(server_params)
            → _build_adapters_for_session()
            → registry.register(adapter)
            → ServerStatus(connected)
      → _log_startup_summary()
  → ready.set()
```

### 2. 工具调用流

```
LLM 调用 "filesystem:read_file"
  → ToolRegistry 查找 MCPToolAdapter
  → adapter.execute(args, context)
    → asyncio.timeout(call_timeout_seconds)
    → group.call_tool(self._sdk_key, args)  [NOT self.name!]
    → 结果转换: _convert_content_block()
    → structuredContent → UiArtifact
    → _note_result() → runtime_observer
    → 返回 ToolResult
  → [异常处理]
    → TimeoutError → is_error ToolResult
    → Dead server → MCPServerDeadError
    → McpError connection loss → MCPServerDeadError
    → Other McpError → is_error ToolResult
```

### 3. 服务器重启流

```
restart_server(name)
  → [supervisor running] → put ("restart", name, future) to queue
    → _restart_server_impl()
      → unregister old adapters
      → _connect_one(name, config)
      → on success: new adapters registered
      → on failure: old adapters already removed (safer-by-default)
  → [no supervisor] → _restart_server_impl() directly
  → 返回 RestartResult(ok, tool_count, reason)
```

### 4. 服务器死亡处理流

```
tool execute → MCPServerDeadError
  → _handle_server_death(name)
    → unregister all adapters for this server
    → _sessions.pop(name)
    → ServerStatus(failed, reason="server died mid-call")
    → note_tool_result(outcome="server_dead")
```

### 5. 网络 ACL 检查流

```
MCP 代理请求 → McpNetworkAcl.is_allowed(host, server_name)
  → 全局白名单 fnmatch 匹配
  → server-specific 白名单 fnmatch 匹配
  → 未注册 server → Fail-Closed → DENIED
  → 未指定 server_name → 扫描所有已注册域名
  → 返回 True/False
```

## 对外接口

| 接口 | 来源 | 说明 |
|------|------|------|
| `ConnectorRegistry.servers` / `settings()` | `connectors.py` | 只读连接器视图与运行时 McpSettings |
| `MCPClientManager.start_background()` | `client_manager.py` | 非阻塞启动 |
| `MCPClientManager.is_ready()` | `_mixin_lifecycle.py` | 就绪检查 |
| `MCPClientManager.wait_ready()` | `_mixin_lifecycle.py` | 等待就绪 |
| `MCPClientManager.attach_and_register()` | `_mixin_registry.py` | 绑定注册表 |
| `MCPClientManager.list_servers()` | `_mixin_registry.py` | 服务器列表 |
| `MCPClientManager.get_status()` | `_mixin_registry.py` | 单服务器状态 |
| `MCPClientManager.connection_summary()` | `_mixin_registry.py` | 连接摘要 |
| `MCPClientManager.restart_server()` | `_mixin_connection.py` | 重启服务器 |
| `MCPClientManager.get_logs()` | `_mixin_registry.py` | 获取日志 |
| `MCPClientManager.list_mcp_prompts()` | `_mixin_registry.py` | 列出 prompts |
| `MCPClientManager.get_prompt_text()` | `_mixin_registry.py` | 获取 prompt 文本 |
| `MCPClientManager.get_prompt_text_threadsafe()` | `_mixin_registry.py` | 线程安全获取 |
| `MCPClientManager.shutdown()` | `_mixin_lifecycle.py` | 关闭 |
| `MCPClientManager.note_tool_decision()` | `_mixin_config.py` | 工具决策记录 |
| `MCPClientManager.note_tool_result()` | `_mixin_config.py` | 工具结果记录 |
| `MCPToolAdapter.execute()` | `adapter.py` | 工具执行 |
| `McpNetworkAcl.is_allowed()` | `mcp_network_acl.py` | ACL 检查 |
| `McpNetworkAcl.add_server()` | `mcp_network_acl.py` | 注册域名白名单 |
| `CompositeMCPManager.*()` | `_composite.py` | 复合管理器 |
| `DeclarativeMCPManager.list_servers()` | `_declarative.py` | 声明式查询 |

## 依赖关系

- **ConnectorRegistry**：`engine/mcp/connectors.py` — `backend/configs/mcp.json` 唯一入口，构造运行时 `McpSettings`
- **mcp SDK**：`ClientSessionGroup`、`ClientSession`、`StdioServerParameters`、`SseServerParameters`、`StreamableHttpParameters`、`mcp_types`
- **anyio**：ClosedResourceError / BrokenResourceError 检测
- **SandboxPolicy**：bubblewrap 沙箱包装（`wrap_mcp_stdio()`）
- **ToolRegistry**（`_ToolRegistryProto`）：工具注册/注销
- **TaskManagerProtocol**：后台任务管理
- **MCPServerDeadError**：服务器死亡异常传播
- **_install_mcp_stdio_client_patch()**：SDK stdio client 补丁

## 配置项

配置源：`backend/configs/mcp.json`（部署级连接器注册表，顶层唯一键为 `connectors`）。

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `connectors` | — | 连接器注册表；`ConnectorRegistry` 只暴露 `enabled=true` 的条目 |
| `connectors.<name>.enabled` | true | 单连接器开关；未启用不连接、不注册工具 |
| `connectors.<name>.transport` | stdio | 传输协议（stdio/sse/streamable-http/http） |
| `connectors.<name>.command` | — | stdio 命令 |
| `connectors.<name>.args` | [] | 命令参数 |
| `connectors.<name>.env` | {} | 环境变量（支持 `{env:VAR}` 占位符） |
| `connectors.<name>.url` | — | SSE/HTTP URL |
| `connectors.<name>.headers` | {} | HTTP headers（支持 `{env:VAR}` 占位符） |
| `connectors.<name>.connect_timeout_seconds` | 30.0 | 连接超时 |
| `connectors.<name>.call_timeout_seconds` | 60.0 | 调用超时 |
| `connectors.<name>.forced_tool_class` | None | 强制工具类别 |
| `connectors.<name>.trust_annotations` | true | 按 readOnlyHint 推导工具类别；False 时强制 write |
| `connectors.<name>.server_read_only` | false | 只读连接器：write 类工具对 LLM 不可见 |
| `connectors.<name>.deferred` | true | 工具延迟披露，经 `tool_search` 发现；核心能力 server 可设 false |
| `connectors.<name>.sandbox.enabled` | stdio 且非 npx/uvx 为 true，其余 false | 沙箱隔离开关 |
| `connectors.<name>.sandbox.filesystem` | {} | 文件系统沙箱配置 |
| `connectors.<name>.sandbox.network` | {} | 网络沙箱配置 |
| `connectors.<name>.sandbox.env_allowlist` | — | 环境变量白名单 |
| `McpNetworkAcl.default_policy` | deny | 构造参数（非 `mcp.json` 键）；默认 deny-all |
| `_TOOL_COUNT_WARN_THRESHOLD` | 100 | 工具数警告阈值 |
| `_STDERR_BUFFER_LINES` | 200 | stderr 缓冲行数 |
