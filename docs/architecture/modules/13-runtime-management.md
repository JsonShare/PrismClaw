# 运行时管理

## 职责概述

运行时管理模块是 PrismClaw 的执行基础设施层，负责工作区容器的生命周期管理、安全沙箱执行、远程/本地通信以及资源调度。核心职责包括：

- **工作区执行**：提供 bash、文件 I/O、HTTP 代理、MCP 服务器管理等执行能力
- **安全隔离**：通过 RuleGate 入口校验 + BwrapPolicy 内核级沙箱实现多层安全防护
- **容器编排**：管理每个工作区的容器（Docker Swarm / K8s / Firecracker microVM）
- **预热池**：基于历史启动量的动态伸缩，减少冷启动延迟
- **高可用代理**：3-replica 代理集群 + VIP 故障切换

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
│  │              WorkspaceContainerManager (编排层)                   │   │
│  │                        │ ContainerBackend Protocol              │   │
│  │         ┌──────────────┼──────────────┐                          │   │
│  │         ▼              ▼              ▼                          │   │
│  │  ┌───────────┐  ┌───────────┐  ┌────────────────┐              │   │
│  │  │SwarmBackend│  │K8sJobBackend│ │FirecrackerBackend│             │   │
│  │  └─────┬─────┘  └─────┬─────┘  └───────┬────────┘              │   │
│  └────────┼───────────────┼────────────────┼────────────────────────┘   │
│           ▼               ▼                ▼                            │
│    Docker Swarm API  Kubernetes API  Firecracker microVM                │
│                                                                          │
│  ┌────────────────┐                                                     │
│  │  PreWarmPool   │── record_startup() / adjust_pool()                 │
│  └────────────────┘                                                     │
└──────────────────────────────────────────────────────────────────────────┘
```

**关键设计原则**：
- 传输-执行分离：生产（HTTP 容器）与开发/测试（进程内）共用 `RuntimeExecutor`，保证**逐字节一致**的执行语义
- 生产环境禁用 `InProcessRuntimeClient`（`PRISMCLAW_DEPLOYMENT_ENV=production` 时构造即抛错）
- 传输层做 HMAC 签名 + workspace_id 校验，执行层做 RuleGate + BwrapPolicy 双重校验
- 容器编排通过 `ContainerBackend` Protocol 解耦，支持 Swarm / K8s / Firecracker 切换
- PreWarmPool 基于历史启动量动态伸缩，减少冷启动延迟

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

### ContainerBackend Protocol (`container_backend.py`)

容器运行时后端协议：

```python
class ContainerBackend(Protocol):
    def create_workspace(workspace_id, tag, *, hmac_secret, hmac_key_version) -> Any
    def destroy_workspace(workspace_id) -> bool
    def get_workspace(workspace_id) -> Any | None
    def list_workspaces() -> list[Any]
    async def health_check(workspace_id) -> bool
```

三个实现：

| 实现 | 说明 |
|------|------|
| `SwarmBackend` | Docker Swarm service，每个工作区一个 Swarm service |
| `K8sJobBackend` | Kubernetes Job + PVC + NetworkPolicy（Phase 4b） |
| `FirecrackerBackend` | Firecracker microVM（P4-OPS-02 骨架） |

工厂函数：`get_container_backend(backend_name)` — 按名称/环境变量创建后端实例

### WorkspaceContainerManager (`container_manager.py`)

编排层，通过 `ContainerBackend` Protocol 委托具体实现：

- 校验 workspace_id 唯一性
- 跟踪活跃工作区（`WorkspaceContainer` 数据类）
- 汇总 PreWarmPool 启动统计（`record_startup`）
- 提供 `adjust_prewarm()` 调整预热池

`WorkspaceContainer` 数据类：workspace_id、service_name、tag、container_id、endpoint_url、status

### PreWarmPool (`prewarm_pool.py`)

预热池动态伸缩，目标池大小 = base_prewarm_count × peak_factor × time_factor：

| 因子 | 规则 |
|------|------|
| 峰值因子 | 月末最后一个工作日 × 1.5，周末 × 0.5 |
| 时段因子 | 工作日 9-11 时 × 1.5，夜间 22-7 时 × 0.3 |
| 利用率因子 | 过低（<30%）× 0.7，过高（>90%）× 1.3，空池 × 1.5 |

关键类：`PoolMetrics`（target_size、current_size、startup_count_1h/24h、utilization_rate、peak_factor、last_adjusted_at）
关键方法：`record_startup()`、`compute_target_size()`、`get_metrics()`、`adjust_pool()`

### ResourceTiers (`resource_tiers.py`)

四档资源配置：

| WorkspaceKind | 名称 | SandboxTier | CPU | 内存 | 网络 | 特殊策略 |
|---|---|---|---|---|---|---|
| GENERAL | office-general | STANDARD | 1.0 | 1g | 允许 | — |
| OFFICE | office-general | STANDARD | 1.0 | 1g | 允许 | — |
| LEGAL_CASE | legal-case | TIGHT | 1.0 | 1g | 域名白名单 | WORM 审计 |
| MEDICAL_GENERAL | medical-general | TIGHT | 1.0 | 1g | 域名白名单 | WORM 审计 |
| MEDICAL_PHI | medical-phi | STRICT | 2.0 | 2g | 完全禁止 | WORM + 四眼 + PID 隔离 |

`ResourceTier` 数据类字段（仅列出核心字段）：name、sandbox_tier、cpu_limit（`str`，如 `"1.0"`）、memory_limit（`str`，如 `"1g"`）、template_id、allow_net、deny_pid_namespace、worm_audit、four_eyes、prewarm_count、cpu_reservation（`str`）、memory_reservation（`str`）、allow_read_workspace、allow_read_user_safe、allow_write_workspace、prewarm_idle_timeout_seconds

### SandboxTemplate & TemplateRegistry (`template.py`, `builtin_templates.py`)

- `SandboxTemplate`：Pydantic 模型，定义容器镜像的预装依赖（apt/pip/npm/bins/mcp_servers）
- `TenantTemplate`：机构定制模板，在基础模板上叠加机构专属依赖
- `TemplateRegistry`：全局单例，存储 template_id → (image_tag, SandboxTemplate) 映射
- `build_template_from_skills()`：从 Skill 清单聚合依赖构建模板
- `compute_build_hash()`：基于模板内容计算确定性 SHA-256 哈希
- 内置模板：office-v1、legal-v1、medical-general-v1、medical-phi-v1

### Runtime Mode (`mode.py`)

运行模式快照：`build_runtime_mode_snapshot()` 检测 Redis 可用性，生成模式描述：

- **standard**：Redis 可用，全部功能启用
- **fallback**：Redis 不可用，部分功能降级（working_memory_live_sync、sop_tracking、curator、dream_dispatch、affinity_routing）
- **offline**：Redis 不可用且无降级能力

### ProxyHA (`proxy_ha.py`)

3-replica 代理集群高可用：

- `ProxyCluster`：管理多副本 + VIP 故障切换
- `VipManager` Protocol：VIP 管理（`DockerVipManager`、`K8sServiceVipManager`）
- 健康检查：后台 `asyncio.Task` 周期性 HTTP GET `/health`
- 故障切换：leader 不健康时自动选举新 leader（round-robin），迁移 VIP
- `FailoverEvent`：故障切换审计记录
- `StressTestFixture`：1000 工作区并发压测

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

### 容器创建流

```
WorkspaceContainerManager.create_workspace(workspace_id, tag)
  → backend.create_workspace(workspace_id, tag, hmac_secret, hmac_key_version)
    → get_resource_tier(tag.kind) → ResourceTier
    → SwarmBackend: docker.services.create(image, env, mounts, resources, labels)
    → K8sJobBackend: 创建 PVC + Job + NetworkPolicy
    → FirecrackerBackend: 生成 VM ID + 配置 jailer + clone rootfs
  → prewarm_pool.record_startup(tag.kind)
  → return WorkspaceContainer
```

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

### 容器管理接口（WorkspaceContainerManager）

| 方法 | 返回类型 | 说明 |
|------|----------|------|
| `create_workspace(workspace_id, tag)` | `WorkspaceContainer` | 创建工作区 |
| `destroy_workspace(workspace_id)` | `bool` | 销毁工作区 |
| `get_workspace(workspace_id)` | `WorkspaceContainer \| None` | 获取工作区信息 |
| `list_workspaces()` | `list[WorkspaceContainer]` | 列出所有工作区 |
| `health_check(workspace_id)` | `bool` | 健康检查 |
| `adjust_prewarm(workspace_kind)` | `int` | 调整预热池 |

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
