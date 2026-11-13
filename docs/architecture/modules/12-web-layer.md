# Web 层

## 职责概述

Web 层是 PrismClaw 面向用户的 HTTP/WebSocket 交付层，负责：

- 应用生命周期管理（启动/关闭编排、健康检查、基础设施初始化）
- JWT 认证与用户管理
- WebSocket 双向通信（聊天、中止、工具审批、心跳）
- HTTP REST API（会话管理、文件操作、工作区管理、技能、设置、Office 预览）
- OpenAI 兼容 API（`/api/v1/chat/completions`、`/api/v1/models`）
- 多 Worker 网关路由（会话亲和性、消息转发）
- 安全中间件（CSP、HSTS、速率限制、分布式追踪）
- 聊天命令适配（斜杠命令拦截与插件命令路由）

## 架构总览

**[查看交互式架构图](../diagrams/12-web-layer.html)**

```
┌──────────────────────────────────────────────────────────────────┐
│                     Web 请求处理链架构                            │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐   │
│  │                   WebSocket 通信层                         │   │
│  │  ┌────────────────────┐  ┌──────────────────────────┐    │   │
│  │  │   WebSocket 端点   │  │   Protocol (消息类型)     │    │   │
│  │  │  (/api/ws)         │  │  ClientMsg / ServerEvent  │    │   │
│  │  └────────┬───────────┘  └──────────────────────────┘    │   │
│  │           │ identify → JWT → dispatch                     │   │
│  └───────────┼───────────────────────────────────────────────┘   │
│              │                                                    │
│              ▼                                                    │
│  ┌───────────────────────────────────────────────────────────┐   │
│  │                    Chat 聊天处理层                         │   │
│  │  ┌────────────┐ ┌───────────┐ ┌───────────────────────┐  │   │
│  │  │ ChatRunner │ │ChatAuth   │ │ SessionQueue          │  │   │
│  │  │ (主循环)   │ │(入队/中止)│ │ (per-conv 队列+审批)  │  │   │
│  │  └─────┬──────┘ └───────────┘ └───────────────────────┘  │   │
│  │        │ event stream (delta+done)                            │   │
│  └────────┼──────────────────────────────────────────────────┘   │
│           │                                                       │
│           ▼                                                       │
│  ┌───────────────────────────────────────────────────────────┐   │
│  │                    Auth 认证层                             │   │
│  │  ┌────────────┐ ┌──────────────┐ ┌────────────────────┐  │   │
│  │  │ JWT 签发   │ │ ProfileStore │ │ ModeResolution     │  │   │
│  │  │ / 验证     │ │ (用户配置)   │ │ (模式解析)         │  │   │
│  │  └────────────┘ └──────────────┘ └────────────────────┘  │   │
│  └───────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌───────────────────────────────────────────────────────────┐   │
│  │                  Gateway 网关路由层                        │   │
│  │  ┌─────────────┐ ┌────────────┐ ┌──────────────────┐     │   │
│  │  │GatewayRouter│ │  Affinity  │ │  Forwarder       │     │   │
│  │  │(路由决策)   │ │(会话亲和性)│ │(Pub/Sub跨Worker) │     │   │
│  │  └──────┬──────┘ └────────────┘ └──────────────────┘     │   │
│  └─────────┼─────────────────────────────────────────────────┘   │
│            │ local / forwarded                                     │
│            ▼                                                       │
│  ┌───────────────────────────────────────────────────────────┐   │
│  │                  Routes HTTP 路由层                        │   │
│  │  ┌──────┐ ┌────────┐ ┌────────┐ ┌──────┐ ┌──────────┐   │   │
│  │  │Files │ │Workspace│ │OpenAI  │ │Skills│ │Settings  │   │   │
│  │  │ CRUD │ │ CRUD   │ │Compat  │ │ 管理 │ │ 管理     │   │   │
│  │  └──────┘ └────────┘ └────────┘ └──────┘ └──────────┘   │   │
│  └───────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

## 核心组件

### 应用生命周期（_lifespan.py）

```python
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # 启动编排:
    # 1. load_settings() → resolve_project_root()
    # 2. create_shared_engine(settings.storage) → PostgreSQL 引擎
    # 3. resolve_sandbox_state() → 沙箱策略
    # 4. build_app_infrastructure_bundle() → Redis/锁/SessionStore/TaskManager/WorkerRegistry 等
    # 5. _startup_health_gate() → Redis + PostgreSQL 可达性验证（15 次重试）
    # 6. build_app_runtime_bundle() → RunnerDeps / DreamDispatchConsumer
    # 7. build_app_lifecycle_bundle() → 生命周期管理
    # 8. _init_user_store() → 用户存储（PostgreSQL 优先，降级到配置列表）
    # 9. PgTrajectoryPersistence 初始化
    # 10. Scheduler 系统初始化（RedisSchedulerDispatchQueue + SchedulerEngine + ScheduledRunExecutor）
    #
    # 关闭编排:
    # 1. MCP manager shutdown
    # 2. SchedulerEngine.stop()
    # 3. shutdown_app_runtime()
    # 4. db_engine.dispose()
```

```python
async def _startup_health_gate():
    # 验证 Redis + PostgreSQL 可达性
    # max_attempts=15, interval_seconds=2.0
    # 处理 Docker Compose 启动顺序竞争

async def _init_user_store():
    # PostgreSQL 优先 → 降级到配置列表
    # init_schema() → seed_from_config()
    # 支持 lockout_threshold / lockout_window_seconds / password_scheme

async def _worker_heartbeat_loop()      # Worker 心跳循环
async def _dream_dispatch_health_loop()  # Dream 调度健康监控
```

### 认证（auth/）

```python
# auth/auth.py
def create_jwt(user_id, secret, expires_in=86400) → str
    # HS256 JWT: header.payload.signature
    # payload: {sub: user_id, iat, exp}

def verify_jwt(token, secret) → str | None
    # HMAC 验证 + 过期检查 → 返回 user_id

def get_current_user(request: Request) → str
    # FastAPI 依赖：Bearer token / _token 查询参数 → user_id
```

### WebSocket 协议（protocol/）

```python
# 客户端消息
@dataclass IdentifyMessage:     type="identify", token, protocol_version
@dataclass ChatSendMessage:     type="chat.send", conversation_id, workspace_id, content, confirmation_decision, attachments, mode, trajectory
@dataclass ChatAbortMessage:    type="chat.abort", conversation_id
@dataclass PongMessage:         type="pong"
@dataclass ToolApproveMessage:  type="tool.approve", approval_id, tool_call_id, approved, reason

# 服务端事件常量
SERVER_HELLO = "hello"
SERVER_READY = "ready"
SERVER_CHAT_DELTA = "chat.delta"
SERVER_CHAT_DONE = "chat.done"
SERVER_CHAT_QUEUED = "chat.queued"
SERVER_REFERENCE = "event:reference"
SERVER_QUESTIONS = "event:questions"
SERVER_OUTPUTS = "event:outputs"
SERVER_FILE_CHANGES = "event:file_changes"
SERVER_TASK_RUNTIME = "task.runtime"
SERVER_TASK_TIMELINE = "task.timeline"
SERVER_PING = "ping"
SERVER_ERROR = "error"

def parse_client_message(data) → ClientMessage | None
```

### WebSocket 端点（websocket.py）

```python
@ws_router.websocket("/api/ws")
async def websocket_endpoint(websocket):
    # 1. accept() → 发送 SERVER_HELLO
    # 2. 等待 IdentifyMessage（30s 超时 → close 4001）
    # 3. JWT 验证 → close 4003（无效 token）
    # 4. 协议版本检查 → close 4005
    # 5. 连接数检查 → close 4004（max_connections_per_user）
    # 6. ConnectionRegistry 注册 → 发送 SERVER_READY
    # 7. Affinity force_claim → 工作网关路由
    # 8. 心跳循环 → _heartbeat_loop()
    # 9. 消息分发循环 → _dispatch_loop()

async def _dispatch_loop(state, settings, task_manager):
    # chat.send → _on_chat_send (含命令拦截)
    # chat.abort → _on_chat_abort
    # tool.approve → _on_tool_approve
    # pong → 更新 last_pong

async def broadcast_to_user(app, user_id, event_type, data):
    # 向用户所有活跃 WebSocket 连接广播事件（调度器通知用）
```

### 连接管理（connection.py）

```python
@dataclass
class ConnectionState:
    ws: Any
    ws_session_id: str
    user_id: str | None
    seq: int                      # 消息序列号
    last_pong: float              # 最后 pong 时间
    authenticated: bool
    ws_dead: bool                 # 死 socket 标记（置 True 后永不回退）
    msg_handlers: dict[str, MsgHandler]   # 消息处理器注册表
    get_session_queue: Callable    # session queue 解析器

class ConnectionRegistry:
    # user_id → set[WebSocket] 映射
    def connect / disconnect / count / get_connections / clear

async def send_event(state, event_type, conversation_id, data) → (seq, sent):
    # 源 socket 死亡（ws_dead=True）→ 帧直接改投该用户的活跃 peer 连接
    # （_fanout_to_peer_connections），快速重连零投递间隙；用户完全
    # 不可达（无 peer、网关路由也无接收方）才返回 sent=False。无冷却机制。
```

### 聊天运行器（chat_runner.py）

```python
async def _run_chat(state: ConnectionState, msg: ChatSendMessage, settings: WebSettings):
    # 1. session_id 解析: "web:{user_id}:{conversation_id}"
    # 2. workspace_id 校验（会话绑定一致性）
    # 3. resolve_profile_and_mode() → 用户配置与模式
    # 4. 构建 RunRequest
    # 5. chat_service.start_conversation() → 异步事件流
    # 6. 事件处理:
    #    - IterationBoundary: 迭代分隔 + full_transcript_parts 分段
    #    - TextChunk: 即时推送 chat.delta（所有轮次，零缓冲）+ 累计 full_transcript_parts
    #    - ReasoningChunk: 时间线推理增量推送
    #    - ToolCallStart: 工具执行开始 + 时间线 step/orphan 卡片
    #    - ToolCallEnd: 工具执行结束 + 时间线更新
    #    - ToolApprovalRequest: 审批请求
    #    - HeartbeatEvent: 阶段心跳
    #    - Done: final_message = "\n\n".join(full_transcript_parts)（全转录）+ chat.done
    #    - ErrorEvent: 错误处理
    # 7. 输出文件收集 + 交付物确定
    # 8. 文件变更通知
    # 9. 追问生成 + 引用推送
```

关键机制：
- **单 pass 实时正文（PrismClaw 模式）**：所有轮次的 `TextChunk` 即时推送 `chat.delta`，token 级零缓冲；`Done` 时提交 `final_message = "\n\n".join(full_transcript_parts)` 作为全转录权威提交源（与实时流逐字节相等，I3 不变量）。中间轮过程文本保留在正文，不再撤回/归入思考区。
- **chat.done 指数退避**：覆盖 WS 短暂断开（退避序列 0.5, 1, 2, 4, 6, 8, 10, 15s，共 8 次）
- **交付物确定策略**：仅 LLM 通过 `present_files` 主动声明的文件才展示；自动扫描 `outputs/` 仅用于验证

### Session 队列（chat_protocol.py）

```python
class SessionQueue:
    # per-conversation 消息队列与消费者
    _queues: dict[str, asyncio.Queue]
    _consumers: dict[str, str]          # conversation_id → task_id
    _busy: dict[str, bool]
    _abort_events: dict[str, asyncio.Event]
    _run_controls: dict[str, RunControl]
    _approval_runtime: _ApprovalRuntime
    _web_runtime: dict[str, ConversationWebRuntimeProjection]

    async def enqueue(conversation_id, msg, handler) → int  # 返回队列位置
    def get_run_control(conversation_id) → RunControl
    def record_subagent_progress(conversation_id, batch)
    def finish_web_runtime(conversation_id)

class _ApprovalRuntime:
    # 审批/澄清等待机制
    def create_pending()           # 创建待审批记录
    def wait_for_decision()        # 等待用户决策（超时→auto:timeout）
    def submit_user_decision()     # 提交用户决策
    def mark_consumed / mark_stale
    def mark_stale_for_conversation  # abort 时批量标记

class ConversationWebRuntimeProjection:
    loading_active: bool
    subagent_items: dict
    approval_pending_tool_call_ids: set
    timeline_snapshot: dict | None
    tool_calls: list[dict]
```

### 命令适配器（command_adapter.py）

```python
async def try_handle_slash_command(state, msg) → ChatSendMessage | None:
    # 1. _SLASH_COMMAND_RE 匹配 /command_name args
    # 2. ExtensionManager.get_plugin_command_runtime(command_name)
    # 3. runtime.build_invocation_prompt(args)
    # 4. 返回修改后的 ChatSendMessage（content → invocation_prompt）
    # 5. 注入 _command_identity（skill_name + plugin_name）
    # 未匹配 → 返回 None（走正常聊天流程）
```

### 网关路由（gateway/）

```python
# router.py
class GatewayRouter:
    async def route(session_key, event_payload) → RouteResult  # "local" | "forwarded"
    # 路由逻辑:
    # 1. affinity.resolve(session_key) → owner
    # 2. is_mine(owner) → renew → "local"
    # 3. owner is None → claim → "local" / resolve → "forwarded"
    # 4. forwarder.forward(owner, payload)
    # 5. forward 失败 → force_claim → "local"

# affinity.py
class AffinityRegistry:
    # Redis-backed 会话→Worker 亲和性
    async def resolve(session_key) → str | None
    async def claim(session_key) → bool      # NX + TTL
    async def renew(session_key)              # 刷新 TTL
    async def release(session_key)            # Lua 原子释放
    async def force_claim(session_key)        # 强制抢占

# forwarder.py
class ForwardPublisher:
    # Redis Pub/Sub 发布
    async def forward(target_worker_id, event_payload) → bool  # subscribers > 0

class ForwardConsumer:
    # Redis Pub/Sub 订阅
    async def start()  # 带指数退避重连

# worker_registry.py
class WorkerRegistry:
    # Redis Sorted Set 心跳注册
    async def register()            # ZADD + ZREMRANGEBYSCORE 清理
    async def heartbeat()           # 等同 register
    async def deregister()          # ZREM
    async def active_workers()      # healthy/stale/dead 分类
```

### WebDeps 依赖聚合（deps.py）

```python
@dataclass
class WebDeps:
    # 核心依赖
    session_store: SessionStore
    session_router: SessionRouter
    workspace_base: Path
    runner_deps: AgentDeps
    session_queue: SessionQueue
    connection_registry: ConnectionRegistry
    settings_full: Settings
    # 可选依赖
    redis_client: Any
    memory_store: Any
    task_manager: Any
    sandbox_policy: Any
    scheduler_context: dict | None
    web_settings: Any              # WebSettings
    gateway_router: Any
    mcp_manager: Any
    user_repository: Any
    # 函数引用（消除 delivery 层直接 import engine 内部）
    apply_settings_fn: Any
    cleanup_working_dir_fn: Any
    upload_fns: Any                # LocalSink / upload_files
    chat_service: Any              # ChatService Protocol
    # ... 更多函数引用

def get_web_deps(request: Request) → WebDeps
def user_root(workspace_base, user_id) → Path  # workspace_base / f"web_{user_id}"
```

### HTTP 路由（routers/）

```python
# files.py — files_router (prefix="/files")
GET  /files/list           # 目录列表（分页、过滤）
GET  /files/text           # 文件文本内容（200KB 上限）
GET  /files/raw            # 原始文件下载（25MB 上限，CSP）
GET  /files/office_preview # Office 转 PDF 预览（LibreOffice headless）
POST /files/upload         # 批量上传（LocalSink + upload_files 管道）
POST /files/mkdir          # 创建目录
POST /files/write_text     # 写入文本
POST /files/delete         # 删除路径
POST /files/move           # 移动/重命名
GET  /files/search         # 文件名搜索（rglob）
GET  /files/export         # ZIP 导出 outputs/ 目录

# workspace.py — workspace_router
GET    /workspaces           # 列出用户工作区
POST   /workspaces           # 创建工作区
DELETE /workspaces/{id}      # 删除工作区
GET    /workspaces/outputs   # 聚合交付物文件

# openai_compat.py — openai_router (prefix="/api/v1")
POST /api/v1/chat/completions   # OpenAI 兼容聊天（stream/non-stream）
GET  /api/v1/models             # 模型列表
```

### 中间件（_middleware.py）

```python
def _install_metrics(app):           # Prometheus FastAPI Instrumentator
    # PRISMCLAW_ENABLE_METRICS (default=True)
    # /metrics 端点

def _install_security_headers(app):  # 安全头注入
    # CSP: default-src 'self'; script-src 'self'; ...
    # X-Content-Type-Options: nosniff
    # X-Frame-Options: DENY
    # Referrer-Policy: no-referrer
    # HSTS (HTTPS only): max-age=31536000; includeSubDomains

def _install_rate_limiter(app):      # slowapi 速率限制
    # PRISMCLAW_ENABLE_RATE_LIMIT (default=True)
    # 100/minute per IP

def _install_tracing(app):           # OpenTelemetry 分布式追踪
    # PRISMCLAW_ENABLE_TRACING (default=True)
    # W3C traceparent 响应头
```

## 关键数据流

### WebSocket 聊天完整流

```
客户端 → ws://host/api/ws
  → websocket_endpoint()
    → accept → SERVER_HELLO
    → IdentifyMessage(token) → JWT 验证 → SERVER_READY
    → ChatSendMessage → _dispatch_loop
      → _on_chat_send
        → try_handle_slash_command()（斜杠命令拦截）
        → enqueue_chat()
          → SessionQueue.enqueue()
          → _consume() → _run_chat()
            → RunRequest 构建
            → chat_service.start_conversation()
            → 事件流处理:
              TextChunk → SERVER_CHAT_DELTA（即时推送，零缓冲）+ 累计 full_transcript_parts
              ToolCallStart → 时间线 step/orphan 卡片
              ToolCallEnd → 时间线更新 + SERVER_TASK_RUNTIME
              ReasoningChunk → 时间线推理增量
              Done → SERVER_CHAT_DONE（final_message=全转录，指数退避重试）
                → 输出文件收集 + SERVER_OUTPUTS
                → 文件变更 + SERVER_FILE_CHANGES
                → 追问生成 + SERVER_QUESTIONS
                → 引用推送 + SERVER_REFERENCE
            → _persist_execution_timeline()
```

### 多 Worker 会话路由流

```
GatewayRouter.route(session_key, event_payload)
  → AffinityRegistry.resolve(session_key)
    → 已绑定本 Worker → renew → "local"
    → 无绑定 → claim → "local" / 重新 resolve
    → 绑定其他 Worker → ForwardPublisher.forward(owner, payload)
      → Redis Pub/Sub → 目标 Worker ForwardConsumer
      → 无订阅者 → force_claim → "local"
```

### OpenAI 兼容 API 流

```
POST /api/v1/chat/completions
  → get_current_user() → user_id
  → resolve_profile_and_mode() → mode
  → SessionRouter.resolve_or_create(f"openai:{user_id}")
  → RunRequest 构建
  → stream=True:
    → _stream_sse()
      → SSE: chat.completion.chunk（TextChunk → delta.content）
      → SSE: reasoning_content（ReasoningChunk 透传）
      → SSE: [DONE]
  → stream=False:
    → _complete_response()
      → 收集 TextChunk/ReasoningChunk → 合并响应
```

### 文件上传流

```
POST /files/upload
  → get_current_user()
  → _resolve_workspace_root()
  → LocalSink(root, rel_dir)
  → upload_files(files, sink)
    → 扩展名阻止列表检查
    → 噪声过滤（.DS_Store / __MACOSX）
    → 磁盘预检
    → 路径安全校验
    → 分块流式写入
    → 返回 uploaded + skipped 列表
```

### 应用启动编排流

```
_lifespan(app)
  → load_settings() + resolve_project_root()
  → create_shared_engine() → PostgreSQL
  → resolve_sandbox_state()
  → build_app_infrastructure_bundle()
    → Redis / LockManager / SessionStore / TaskManager / WorkerRegistry
    → WorkspaceStore / MemoryStore / DreamDispatchQueue / MCPClientManager
  → _startup_health_gate()  # Redis + PG 可达性
  → build_app_runtime_bundle() → RunnerDeps
  → build_app_lifecycle_bundle()
  → _init_user_store()  # PG → 配置降级
  → PgTrajectoryPersistence 初始化
  → Scheduler 系统初始化
    → SchedulerEngine + ScheduledRunExecutor + SchedulerDispatchConsumer
  → yield（应用运行）
  → finally: shutdown_app_runtime()
```

## 对外接口

| 接口 | 协议 | 说明 |
|------|------|------|
| `ws://host/api/ws` | WebSocket | 主通信通道（聊天/审批/心跳） |
| `POST /api/v1/chat/completions` | HTTP | OpenAI 兼容聊天 API |
| `GET /api/v1/models` | HTTP | 模型列表 |
| `GET/POST /api/files/*` | HTTP | 文件操作（列表/读取/上传/搜索/导出等） |
| `GET/POST/DELETE /api/workspaces*` | HTTP | 工作区管理 |
| `GET /api/workspaces/outputs` | HTTP | 聚合交付物文件 |
| `GET /metrics` | HTTP | Prometheus 指标 |

## 依赖关系

```
Web 层
  ├── fastapi / uvicorn              — Web 框架
  ├── slowapi                        — 速率限制
  ├── prometheus_fastapi_instrumentator — 指标
  ├── opentelemetry                  — 分布式追踪
  ├── assembly.runtime_coordinator     — 基础设施/运行时/生命周期 bundle
  ├── engine.services.llm            — LLM 调用
  ├── engine.services.chat_service   — ChatService Protocol
  ├── engine.subagent.runtime        — 并行任务管理
  ├── engine.trajectory.timeline     — 执行时间线
  ├── engine.security.sandbox        — 沙箱策略
  ├── engine.integrations.mcp        — MCP 客户端管理
  ├── engine.dream.dispatch          — Dream 调度
  ├── engine.scheduler               — 调度系统
  ├── engine.skills                  — 技能管理
  ├── persistence.session            — Session 存储
  ├── persistence.user               — 用户存储（PostgreSQL）
  ├── persistence.lock               — 分布式锁
  ├── persistence.trajectory         — Trajectory 审计
  ├── persistence.scheduler          — 调度器存储
  ├── schema.agent                   — RunRequest, AgentDeps
  ├── schema.session                 — SessionTree
  ├── schema.services                — ChatService, SkillManifest
  ├── schema.protocol                — 协议类型
  ├── infra.redis                    — Redis 客户端
  ├── infra.db                       — PostgreSQL 引擎
  ├── infra.settings                 — 配置
  └── infra.task_manager             — 任务管理
```

## 配置项

| 配置 | 位置 | 说明 | 默认值 |
|------|------|------|--------|
| `PRISMCLAW_ENABLE_METRICS` | `_middleware.py` | Prometheus 指标开关 | `True` |
| `PRISMCLAW_ENABLE_RATE_LIMIT` | `_middleware.py` | 速率限制开关 | `True` |
| `PRISMCLAW_ENABLE_TRACING` | `_middleware.py` | 分布式追踪开关 | `True` |
| 速率限制 | `_middleware.py` | 每 IP 速率 | `100/minute` |
| JWT 过期时间 | `auth/auth.py` | Token 有效期 | `86400` (24h) |
| WS 识别超时 | `websocket.py` | 首条消息超时 | `30s` |
| 心跳间隔 | `WebSettings` | 心跳周期 | — |
| Pong 超时 | `WebSettings` | Pong 响应超时 | — |
| `max_connections_per_user` | `WebSettings` | 每用户最大 WS 连接数 | — |
| 文本预览上限 | `files.py` | 文件文本读取上限 | `200KB` |
| 原始文件上限 | `files.py` | 原始文件下载上限 | `25MB` |
| Office 预览超时 | `files.py` | LibreOffice 转换超时 | `60s` |
| 搜索结果上限 | `files.py` | 文件搜索结果上限 | `2000` |
| 目录列表上限 | `files.py` | 目录列表条目上限 | `5000` |
| chat.done 退避序列 | `chat_runner.py` | 发送失败重试间隔 | `[0.5,1,2,4,6,8,10,15]s` |
| 发送失败冷却 | `connection.py` | WS 发送失败冷却期 | `5s` |
| Affinity TTL | `affinity.py` | 会话亲和性过期 | `300s` |
| Worker 心跳间隔 | `worker_registry.py` | 心跳周期 | `30s` |
| Worker stale 阈值 | `worker_registry.py` | Worker 过期判定 | `90s` |
