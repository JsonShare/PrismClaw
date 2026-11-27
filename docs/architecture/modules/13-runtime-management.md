# 13 — 运行时管理 (Runtime Management)

## 职责概述

运行时管理模块是 PrismClaw 的执行基础设施层，负责工作区内的安全沙箱执行、远程/本地通信。核心职责包括：

- **工作区执行**：提供 bash、文件 I/O、HTTP 代理、MCP 服务器管理等执行能力
- **安全隔离**：通过 RuleGate 入口校验 + BwrapPolicy 内核级沙箱实现多层安全防护
- **工作区路由**：按 `template_id` 把请求路由到 compose 固定声明的 Runtime-Agent，
  并由请求级 `workspace_root` 收窄到具体工作区

## 架构总览

**[查看交互式架构图](../diagrams/13-runtime-management.html)**

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         运行时管理模块总览                                │
│                                                                          │
│  ┌───────────────────────┐  ┌───────────────────────┐                   │
│  │ WorkspaceRuntimeClient│  │ InProcessRuntimeClient│                   │
│  │    (HTTP + HMAC)      │  │     (进程内直调)       │                   │
│  └──────────┬────────────┘  └───────────┬───────────┘                   │
│             │                           │                               │
│             ▼                           │                               │
│  ┌─────────────────────┐               │                               │
│  │  Runtime-Agent      │               │                               │
│  │  (FastAPI server)   │               │                               │
│  └──────────┬──────────┘               │                               │
│             │                           │                               │
│             ▼                           ▼                               │
│  ┌──────────────────────────────────────────────┐                       │
│  │            RuntimeExecutor                    │                       │
│  │     RuleGate → BwrapPolicy → subprocess      │                       │
│  └──────────────────────────────────────────────┘                       │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │      compose: runtime-agent-default / -restricted（固定服务）      │   │
│  │         PRISMCLAW_RUNTIME_AGENT_URLS 按 template_id 路由            │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────┘
```

**关键设计原则**：
- 传输-执行分离：生产（HTTP 容器）与开发/测试（进程内）共用 `RuntimeExecutor`，保证**逐字节一致**的执行语义
- 生产环境禁用 `InProcessRuntimeClient`（`PRISMCLAW_DEPLOYMENT_ENV=production` 时构造即抛错）
- 传输层做 HMAC 签名 + workspace_root 严格子路径校验，执行层做 RuleGate + BwrapPolicy 双重校验
- Runtime-Agent 是 compose 静态声明的长生命周期服务；工作区只是 `/workspace` 下的目录，
  不存在按工作区动态创建容器的路径

## 核心组件

### RuntimeExecutor (`agent/executor.py`)

唯一执行核心，提供 bash / 文件 I/O / HTTP / MCP 四类操作：

| 方法 | 功能 |
|------|------|
| `bash(command, tier, soft_timeout, hard_timeout)` | 执行 bash 命令，软超时内同步返回，超时 detach 为后台任务 |
| `read_text(path)` / `read_bytes(path)` | 读取文件（文本/二进制） |
| `write_text(path, content)` / `write_bytes(path, content)` | 写入文件 |
| `write_stream_begin/chunk/finish` | 分片流式写入（session_id 协议） |
| `http_get(url, tier)` | HTTP GET 代理 |
| `mcp_spawn(server_name, transport, args, env)` | 启动 MCP 服务器 |
| `mcp_call(server_name, tool_name, arguments)` | 调用 MCP 工具 |
| `job_status(pid)` / `job_kill(pid)` / `list_jobs()` | 后台任务管理 |

后台任务模型：`_JobEntry`（pid、command、proc、stdout_path、stderr_path、pump_tasks、started_at）

### Runtime-Agent 服务端 (`agent/server.py`)

FastAPI 应用，每个工作区容器运行一个实例。端点：

| 端点 | 方法 | 功能 |
|------|------|------|
| `/bash` | POST | 执行 bash |
| `/bash/jobs` | GET | 列出后台任务 |
| `/bash/jobs/{pid}` | GET | 后台任务状态 |
| `/bash/jobs/{pid}/kill` | POST | 杀掉后台任务 |
| `/read` | POST | 读取文件 |
| `/write` | POST | 写入文件 |
| `/write_stream` | POST | 分片流式写入 |
| `/http_get` | POST | HTTP GET 代理 |
| `/http_post` | POST | HTTP POST 代理 |
| `/mcp/spawn` | POST | 启动 MCP 服务器 |
| `/mcp/call` | POST | 调用 MCP 工具 |
| `/fs/list` | POST | 目录列表 |
| `/fs/glob` | POST | 文件查找 |
| `/fs/grep` | POST | 内容搜索 |
| `/archive/create` | POST | 创建容器归档 |
| `/archive/restore` | POST | 恢复归档 |
| `/archive/list` | GET | 列出归档 |
| `/health` | GET | 健康检查 |

安全中间件：`verify_hmac_middleware` — 除 `/health` 外所有请求需通过 HMAC-SHA256 签名校验 + workspace_id 匹配

### WorkspaceRuntimeClient (`client.py`)

HTTP 通信层，工具与 Runtime-Agent 之间的桥梁：

- HMAC-SHA256 签名：`v{key_version}:sha256-hmac-body`
- 按 `workspace_kind` 路由到不同 Agent URL（`_agent_urls` 映射表）
- 支持分片流式写入：begin → chunk[] → finish，512KB 字节分片
- UTF-8 安全切片：`_cut_utf8_safe()` 确保文本模式分片在字符边界截断

### InProcessRuntimeClient (`in_process_client.py`)

进程内传输，与 `WorkspaceRuntimeClient` 实现完全相同的公开接口：

- 委托给 `RuntimeExecutor`，复用 RuleGate + BwrapPolicy
- 按 `workspace_root` 维护多个 `RuntimeExecutor` 实例（`_executors` 字典）
- 生产环境禁用：`PRISMCLAW_DEPLOYMENT_ENV=production` 时 `_assert_non_production()` 抛错
- 归档操作为 no-op（进程内模式不支持 Docker）

> **部署形态**：生产环境由 compose 静态声明 `runtime-agent-default` /
> `runtime-agent-restricted` 两个服务，经 `PRISMCLAW_RUNTIME_AGENT_URLS` 路由；
> 不按工作区动态创建容器。

### Runtime Mode (`mode.py`)

运行模式快照：`build_runtime_mode_snapshot()` 检测 Redis 可用性，生成模式描述：

- **standard**：Redis 可用，全部功能启用
- **fallback**：Redis 不可用，部分功能降级（working_memory_live_sync、sop_tracking、curator、dream_dispatch、affinity_routing）
- **offline**：Redis 不可用且无降级能力

## 关键数据流

### bash 命令执行流（生产路径）

```
Tool.bash("ls -la")
  → WorkspaceRuntimeClient.bash(command, tier=STANDARD)
    → _sign(body) → HMAC-SHA256 签名
    → _post("/bash", payload, workspace_kind)
      → httpx.AsyncClient.post → Runtime-Agent
        → verify_hmac_middleware 校验
        → executor.bash(command, tier, soft_timeout)
          → RuleGate.check("bash", {"command": ...})
          → BwrapPolicy.check_network_access(tier)
          → BwrapPolicy.wrap_bash_command(command, sandbox_ctx)
          → asyncio.create_subprocess_exec(executable, *args)
          → 软超时内: return {exit_code, stdout, stderr}
          → 软超时: detach → _JobEntry → 硬超时杀手
```

### 分片流式写入流

```
WorkspaceRuntimeClient.write_stream(path, content, chunk_size=512KB)
  → _post("/write_stream", {action:"begin", path, mode, encoding})
    ← {session_id}
  → 循环: _post("/write_stream", {action:"chunk", session_id, data})
    ← {bytes_written}
  → _post("/write_stream", {action:"finish", session_id})
    ← {total_bytes}
```

### 容器编排流（当前）

```
docker compose up
  → runtime-init（root，一次性）建 /runtime 目录树并 chmod -R a+rwX
  → runtime-agent-default / runtime-agent-restricted（固定服务，长生命周期）
  → app 按 template_id → PRISMCLAW_RUNTIME_AGENT_URLS 路由到对应 agent
```

不存在按工作区动态创建容器的路径：工作区只是 agent 内的一个目录
（`/workspace/web_<user_id>/<workspace_id>/`），由请求级 `workspace_root`
收窄到具体工作区。

## 对外接口

### 工具层接口（WorkspaceRuntimeClient / InProcessRuntimeClient 共用）

| 方法 | 返回类型 | 说明 |
|------|----------|------|
| `bash(command, tier, ...)` | `CommandResult \| dict` | 执行命令 |
| `read_text(path, encoding)` | `FileResult` | 读取文本文件 |
| `read_bytes(path)` | `FileResult` | 读取二进制文件 |
| `write_text(path, content, encoding)` | `FileResult` | 写入文本文件 |
| `write_bytes(path, content)` | `FileResult` | 写入二进制文件 |
| `write_stream(path, content, mode, chunk_size)` | `FileResult` | 分片流式写入 |
| `http_get(url, tier)` | `FileResult` | HTTP GET 代理 |
| `http_post(url, body, headers, ...)` | `FileResult` | HTTP POST 代理 |
| `fs_list(path, recursive, ...)` | `dict` | 目录列表 |
| `fs_glob(pattern, path, ...)` | `dict` | 文件查找 |
| `fs_grep(pattern, path, ...)` | `dict` | 内容搜索 |
| `mcp_spawn(server_name, ...)` | `dict` | 启动 MCP 服务器 |
| `mcp_call(server_name, tool_name, ...)` | `dict` | 调用 MCP 工具 |
| `archive_create(name)` | `dict` | 创建归档 |
| `list_jobs()` | `list[dict]` | 列出后台任务 |
| `job_status(pid)` | `dict \| None` | 查询任务状态 |
| `job_kill(pid)` | `bool` | 杀掉任务 |
| `health()` | `bool` | 健康检查 |

## 依赖关系

- **安全模块**：`prismclaw.engine.security.gate.routing.rule_gate.RuleGate`
- **安全模块**：`prismclaw.engine.security.gate.routing.risk_router.get_tool_safety_meta`
- **安全模块**：`prismclaw.engine.security.sandbox.bwrap.BwrapPolicy`
- **安全模块**：`prismclaw.engine.security.gate.policies.WorkspaceTag`、`WorkspaceKind`
- **Schema**：`prismclaw.schema.content.workspace.SandboxTier`
- **Schema**：`prismclaw.schema.services.env_types.CommandResult`、`FileResult`
- **Schema**：`prismclaw.schema.config.config.SandboxConfig`
- **外部库**：`fastapi`、`httpx`、`docker`（可选）、`kubernetes`（可选）、`aiohttp`（ProxyHA）、`croniter`（模板构建）

## 配置项

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `PRISMCLAW_RUNTIME_HMAC_SECRET` | `dev-secret` | HMAC 签名密钥 |
| `PRISMCLAW_RUNTIME_HMAC_KEY_VERSION` | `1` | HMAC 密钥版本（90 天轮换 + 7 天共存） |
| `PRISMCLAW_WORKSPACE_ID` | `unknown` | 当前容器的工作区 ID |
| `PRISMCLAW_WORKSPACE_ROOT` | `/workspace` | 工作区根目录 |
| `PRISMCLAW_ALLOW_NET` | `true` | 是否允许网络访问 |
| `PRISMCLAW_SANDBOX_ENFORCE` | `true` | 是否强制启用 BwrapPolicy |
| `PRISMCLAW_DEPLOYMENT_ENV` | `development` | 部署环境（production 时禁用进程内传输） |
| `PRISMCLAW_CONTAINER_BACKEND` | `swarm` | 容器后端（swarm/k8s/firecracker） |
| `PRISMCLAW_RUNTIME_AGENT_URLS` | — | workspace_kind → Agent URL 的 JSON 映射 |
