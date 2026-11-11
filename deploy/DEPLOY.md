# PrismClaw 部署手册

PrismClaw 采用 B/S 架构：Nginx（前端 + 反向代理 + TLS 终结）→ 多个 Python Worker → PostgreSQL + Redis。
沙箱执行采用双轨：进程级 `bwrap`（多 Worker 部署）与容器化 `Runtime-Agent`（单节点全栈部署）。

---

## 架构概览

PrismClaw 提供两种服务器部署形态，沙箱执行路径随形态切换：

```
┌─────────────────────────────────────────────────────────────────────────┐
│  形态 A: 单节点全栈（docker-compose.yml）                                │
│    客户端 → Nginx → prismclaw:8000                                       │
│      Docker backend 网络                                                 │
│      ├── prismclaw (主应用)                                              │
│      ├── postgres (VectorChord Suite PG17)                               │
│      ├── redis (Redis 7)                                                 │
│      ├── runtime-agent-default:9001  ← SafetyGate STANDARD tier         │
│      └── runtime-agent-restricted:9001 ← SafetyGate STRICT tier         │
│    沙箱: 主应用通过 WorkspaceRuntimeClient（HMAC）远程调用 Runtime-Agent │
│          Runtime-Agent 容器级隔离 + 可选 bwrap 二次隔离                   │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│  形态 B: 多 Worker 生产（docker-compose.multi.yml）                      │
│    客户端                                                                │
│      │                                                                   │
│      ▼                                                                   │
│    Nginx (:443 TLS)                                                      │
│      ├── /           → frontend/dist/ (SPA，nginx 直接服务)              │
│      ├── /api/ws     → Worker1:8001 / Worker2:8002 / Worker3:8003 (WS)   │
│      ├── /api/v1/    → Workers (OpenAI-compat SSE)                       │
│      └── /api/       → Workers (REST API)                                │
│          │                                                               │
│          ▼                                                               │
│    Docker backend 网络                                                   │
│      ├── worker1/2/3 (prismclaw:8000，3 active-active)                   │
│      ├── postgres (VectorChord Suite PG17)                               │
│      └── redis (Redis 7)                                                 │
│    沙箱: 各 Worker 进程内直接调用 BwrapPolicy（bubblewrap 内核级隔离）    │
│          bwrap 缺失时 fail-closed 拒绝启动                               │
└─────────────────────────────────────────────────────────────────────────┘
```

| 部署形态 | 适用场景 | 沙箱后端 | 资源占用 |
|---------|---------|---------|---------|
| 单节点全栈 | 中小规模生产 / 单机私有化 | 容器化 Runtime-Agent | 中（多容器） |
| 多 Worker | 大规模生产 / 高可用 | 进程级 bwrap | 高（多 Worker） |

---

## 一、单节点全栈部署（含 Runtime-Agent 容器化沙箱）

`docker-compose.yml` 提供**整套应用栈**：PostgreSQL + Redis + PrismClaw 主应用 + 2 个 Runtime-Agent 容器（STANDARD / STRICT 双 tier）。Nginx 在宿主机或前置负载均衡。

适合中小规模生产部署，单机即可承载完整生产功能。

### 1.1 前置条件

- Docker Engine ≥ 24.0 + Docker Compose V2
- Nginx（宿主机安装）+ TLS 证书
- 主机内核支持 user namespace（Runtime-Agent 内 bwrap 需要）

### 1.2 配置环境变量

```bash
cp deploy/.env.example deploy/.env
```

编辑 `deploy/.env`，**必须修改**以下变量：

| 变量 | 说明 | 生成方式 |
|------|------|----------|
| `PRISMCLAW_WEB_JWT_SECRET` | JWT 签名密钥 | `python -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `PRISMCLAW_WEB_ADMIN_TOKEN` | 管理接口令牌 | `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `POSTGRES_PASSWORD` | PostgreSQL 密码 | 强随机密码 |
| `HMAC_SECRET` | 主应用 ↔ Runtime-Agent HMAC 签名密钥 | `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `PRISMCLAW_WEB_USER_BOOTSTRAP_ADMIN_PASSWORD` | 首次启动管理员密码 | 强随机密码 |

> **注意**：单节点 compose 中 Redis 未启用密码。生产环境建议自行在 `redis` 服务 `command` 中追加 `--requirepass <strong-password>`，并在主应用环境变量中配置对应 `PRISMCLAW_REDIS_PASSWORD`。

### 1.3 配置应用

仓库只提交脱敏模板，真实配置不入库（已被 `.gitignore` 忽略）。首次部署时从模板生成：

```bash
cd backend/configs
for f in *.example.yaml; do cp "$f" "${f%.example.yaml}.yaml"; done
cd -
```

至少需要修改：

- `backend/configs/models.yaml`：LLM / Embedding / Vision / ASR 端点与 API Key
- `backend/configs/server.yaml`：`channels.web.jwt_secret`、SMTP（注册/找回密码）
- `backend/configs/infra.yaml`：Redis 与 PostgreSQL 连接串

### 1.4 构建镜像

```bash
# 主应用镜像
docker compose -f deploy/docker-compose.yml --env-file deploy/.env build prismclaw

# Runtime-Agent 镜像（两种 tier）
docker compose -f deploy/docker-compose.yml --env-file deploy/.env build runtime-agent-default
docker compose -f deploy/docker-compose.yml --env-file deploy/.env build runtime-agent-restricted
```

或一次性构建全部：

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env build
```

> 私有化裁剪详见 [§五 私有化部署裁剪](#五私有化部署裁剪)。

### 1.5 启动服务

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d
```

验证：

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env ps
```

应看到 6 个服务（prismclaw + postgres + redis + 2 个 runtime-agent + backup profile）。
关键服务（prismclaw / postgres / redis / runtime-agent-*）应均为 `healthy`。

主应用通过环境变量自动配置 Runtime-Agent 路由：

```
PRISMCLAW_RUNTIME_AGENT_URLS={"default-v1":"http://runtime-agent-default:9001","restricted-v1":"http://runtime-agent-restricted:9001"}
```

工具调用流程：
```
Tool → WorkspaceRuntimeClient.bash("ls", tier=STANDARD)
     → HMAC-SHA256 签名 + HTTP POST
     → Runtime-Agent /bash
     → RuntimeExecutor（容器级隔离 + 可选 bwrap）
     → CommandResult
```

### 1.6 配置 Nginx（单节点 upstream 切换）

`deploy/nginx.conf` 默认 upstream 指向多 Worker 端口 8001/8002/8003。**单节点部署必须切换**为 8000：

```nginx
upstream prismclaw {
    # ip_hash: same client IP → same worker (pairs with PrismClaw affinity gateway)
    # ip_hash;            # 单节点无需 ip_hash，注释掉
    server 127.0.0.1:8000 max_fails=2 fail_timeout=10s;
    keepalive 32;
}
```

详细 Nginx 配置流程见 [§2.6 配置 Nginx](#26-配置-nginx)。

### 1.7 验证部署

```bash
# 健康检查（通过 Nginx 或直接访问主应用 8000）
curl -k https://localhost/health
curl http://localhost:8000/health

# Runtime-Agent 健康检查
docker compose -f deploy/docker-compose.yml exec runtime-agent-default \
  curl -s http://localhost:9001/health
docker compose -f deploy/docker-compose.yml exec runtime-agent-restricted \
  curl -s http://localhost:9001/health

# 主应用通过 WorkspaceRuntimeClient 调用 Runtime-Agent（需 admin token）
curl -k -H "x-admin-token: ${PRISMCLAW_WEB_ADMIN_TOKEN}" \
  https://localhost/api/admin/cluster | jq
```

### 1.8 停止

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env down      # 停止
docker compose -f deploy/docker-compose.yml --env-file deploy/.env down -v   # 停止 + 清空数据卷
```

---

## 二、多 Worker 生产部署

3 个 active-active Worker + 共享 PostgreSQL + Redis。Nginx 在宿主机运行。
沙箱采用进程级 bwrap（每个 Worker 进程内直接调用 BwrapPolicy）。

### 2.1 前置条件

- Docker Engine ≥ 24.0 + Docker Compose V2
- Nginx（宿主机安装）
- TLS 证书（自签名或 Let's Encrypt）
- **bubblewrap 已安装**（Worker 容器需 `security_opt: seccomp=unconfined`，详见 [§4.3](#43-安装-bwrap)）

### 2.2 配置环境变量

```bash
cp deploy/.env.example deploy/.env
```

编辑 `deploy/.env`，**必须修改**以下变量：

| 变量 | 说明 | 生成方式 |
|------|------|----------|
| `PRISMCLAW_WEB_JWT_SECRET` | JWT 签名密钥 | `python -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `PRISMCLAW_WEB_ADMIN_TOKEN` | 管理接口令牌 | `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `POSTGRES_PASSWORD` | PostgreSQL 密码（用户 `prismclaw`） | 强随机密码 |
| `REDIS_PASSWORD` | Redis 密码 | 强随机密码 |
| `PRISMCLAW_WEB_USER_BOOTSTRAP_ADMIN_PASSWORD` | 首次启动管理员密码 | 强随机密码 |

> **注意**：多 Worker compose 中 PostgreSQL 用户名为 `prismclaw`（与单节点的 `postgres` 不同），DSN 已在 compose 中固化。

### 2.3 配置应用

```bash
cd backend/configs
for f in *.example.yaml; do cp "$f" "${f%.example.yaml}.yaml"; done
cd -
```

编辑 `backend/configs/models.yaml`（模型端点与 API Key）、`server.yaml`（JWT / SMTP）、`infra.yaml`（Redis / PostgreSQL）。

### 2.4 构建镜像

```bash
docker build -t prismclaw:latest -f deploy/Dockerfile .
```

> 私有化裁剪详见 [§五 私有化部署裁剪](#五私有化部署裁剪)。

### 2.5 启动服务

```bash
docker compose -f deploy/docker-compose.multi.yml --env-file deploy/.env up -d
```

验证：

```bash
docker compose -f deploy/docker-compose.multi.yml --env-file deploy/.env ps
```

应看到 5 个服务（worker1/2/3 + postgres + redis）均为 `healthy`。

### 2.6 配置 Nginx

```bash
# 拷贝前端静态文件
sudo mkdir -p /var/www/prismclaw
sudo cp -r frontend/dist/* /var/www/prismclaw/

# 拷贝 Nginx 配置
sudo cp deploy/nginx.conf /etc/nginx/sites-available/prismclaw
sudo ln -sf /etc/nginx/sites-available/prismclaw /etc/nginx/sites-enabled/

# 测试配置
sudo nginx -t

# 启动/重载
sudo systemctl restart nginx
```

**upstream 选择**（`/etc/nginx/sites-available/prismclaw`）：
- 多 Worker：保留默认 `server 127.0.0.1:8001/8002/8003` 三行 + `ip_hash`
- 单节点：注释三行 + `ip_hash`，启用 `server 127.0.0.1:8000;`

### 2.7 配置 TLS 证书

```bash
# 创建证书目录
sudo mkdir -p /etc/nginx/ssl

# 方式 A：Let's Encrypt（推荐）
sudo certbot certonly --standalone -d your-domain.com
sudo ln -s /etc/letsencrypt/live/your-domain.com/fullchain.pem /etc/nginx/ssl/prismclaw.crt
sudo ln -s /etc/letsencrypt/live/your-domain.com/privkey.pem /etc/nginx/ssl/prismclaw.key

# 方式 B：自签名（仅内部测试环境）
sudo openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout /etc/nginx/ssl/prismclaw.key \
  -out /etc/nginx/ssl/prismclaw.crt
```

### 2.8 验证部署

```bash
# 健康检查
curl -k https://localhost/health

# Worker 状态
curl -k -H "x-admin-token: ${PRISMCLAW_WEB_ADMIN_TOKEN}" \
  https://localhost/api/admin/cluster | jq

# Session affinity（在宿主机执行，需 PYTHONPATH 指向 backend/src）
cd /path/to/PrismClaw
PYTHONPATH=backend/src python deploy/scripts/affinity_status.py
```

> **注意**：`affinity_status.py` / `redis_clean.py` 通过 `prismclaw.infra.settings.load_settings()` 读取 `backend/configs/` 下的 YAML 配置，**只能在宿主机执行**，不能在 Worker 容器内执行（容器内没有 `deploy/scripts/` 目录）。

---

## 三、运维操作

### 3.1 查看日志

```bash
# 单节点全栈
docker compose -f deploy/docker-compose.yml --env-file deploy/.env logs -f prismclaw
docker compose -f deploy/docker-compose.yml --env-file deploy/.env logs -f runtime-agent-default

# 多 Worker
docker compose -f deploy/docker-compose.multi.yml --env-file deploy/.env logs -f
docker compose -f deploy/docker-compose.multi.yml --env-file deploy/.env logs -f worker1

# Nginx
sudo tail -f /var/log/nginx/prismclaw-access.log
sudo tail -f /var/log/nginx/prismclaw-error.log
```

### 3.2 扩缩容（仅多 Worker）

修改 `docker-compose.multi.yml`，增减 worker 数量，同步更新 `nginx.conf` 的 upstream 块：

```bash
# 扩展到 5 个 Worker（在 docker-compose.multi.yml 中添加 worker4/5）
# 然后在 nginx.conf 的 upstream 块中添加对应端口
sudo nginx -s reload
```

### 3.3 更新应用

```bash
# 重新构建镜像
docker build -t prismclaw:latest -f deploy/Dockerfile .

# 多 Worker 滚动重启
docker compose -f deploy/docker-compose.multi.yml --env-file deploy/.env up -d --no-deps worker1
# 等待 worker1 healthy 后
docker compose -f deploy/docker-compose.multi.yml --env-file deploy/.env up -d --no-deps worker2
docker compose -f deploy/docker-compose.multi.yml --env-file deploy/.env up -d --no-deps worker3

# 单节点全栈重启
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --no-deps prismclaw
```

### 3.4 更新前端

```bash
cd frontend && npm run build
sudo cp -r frontend/dist/* /var/www/prismclaw/
# 无需重启 nginx（静态文件即时生效）
```

### 3.5 数据库备份

**方式 A：Docker compose 备份 profile（推荐，仅单节点全栈）**

`docker-compose.yml` 已内置 backup profile，基于 `scripts/pg_backup.sh`：

```bash
# 触发一次性备份容器（写入 pg-backups 卷，可选 S3 上传）
docker compose -f deploy/docker-compose.yml --env-file deploy/.env \
  --profile backup run --rm backup

# 启用 S3 上传：在 .env 中设置
# BACKUP_S3_BUCKET=s3://my-bucket/prismclaw
# AWS_REGION=us-east-1
```

backup profile 会自动完成：pg_dump 压缩 → 本地保留 7 天（`find -mtime +7 -delete`）→ 可选 `aws s3 cp` 上传。

**方式 B：手动 pg_dump**

```bash
# 单节点（用户 postgres）
docker compose -f deploy/docker-compose.yml --env-file deploy/.env exec postgres \
  pg_dump -U postgres prismclaw | gzip > prismclaw_$(date +%Y%m%d).sql.gz

# 多 Worker（用户 prismclaw）
docker compose -f deploy/docker-compose.multi.yml --env-file deploy/.env exec postgres \
  pg_dump -U prismclaw prismclaw | gzip > prismclaw_$(date +%Y%m%d).sql.gz
```

### 3.6 Redis 清理

```bash
# 在宿主机执行（脚本通过 load_settings() 读 backend/configs/infra.yaml）
cd /path/to/PrismClaw
PYTHONPATH=backend/src python deploy/scripts/redis_clean.py
```

> `redis_clean.py` 不接受 `--host` / `--password` 参数，所有配置来自 `backend/configs/infra.yaml` 的 `redis` 段。

### 3.7 查询 Session Affinity 状态

```bash
# 在宿主机执行
cd /path/to/PrismClaw
PYTHONPATH=backend/src python deploy/scripts/affinity_status.py
```

输出 `prismclaw:affinity:*` 键及 `prismclaw:workers` ZSET 状态。

---

## 四、沙箱安全

### 4.1 双轨沙箱架构

PrismClaw 沙箱执行支持两条路径，由部署形态决定：

| 部署形态 | 沙箱后端 | 执行路径 |
|---------|---------|---------|
| 单节点全栈 | 容器化 Runtime-Agent | Worker → HMAC HTTP → Runtime-Agent 容器（+ 可选 bwrap） |
| 多 Worker | 进程级 BwrapPolicy | Worker 进程内直接调用 bwrap |

```
形态 A（单节点）:
  Worker 主进程
    → WorkspaceRuntimeClient.bash(cmd, tier)
      → HMAC-SHA256 签名 + HTTP POST
        → Runtime-Agent (独立容器，PRISMCLAW_WORKSPACE_ID 隔离)
          → RuntimeExecutor（容器级隔离 + 可选 BwrapPolicy）

形态 B（多 Worker）:
  Worker 主进程
    → ToolRuntime.bash(cmd, tier)
      → BwrapPolicy.wrap_bash_command() ← 直接生成 bwrap 参数
        → bwrap 子进程（per-user workspace_root 挂载）
```

### 4.2 进程级 BwrapPolicy（多 Worker 部署）

基于 Linux bubblewrap 的进程级沙箱，在 Worker 进程内直接 fork bash/MCP：

```
Worker 进程（多实例，共享存储 /app/workspaces/）
  │
  ├─ 文件工具（Read/Write/Edit/Grep/Glob）
  │   → WorkspaceResolver 路径边界校验 + sandbox_policy.validate_path()
  │
  ├─ HTTP API（/fs/list /fs/glob /fs/grep）
  │   → _validate_path_within_workspace() 路径遍历防护
  │
  ├─ Bash 工具（RunCommand/Bash）
  │   → 三层安全检查：
  │     1. RuleGate 静态规则（路径逃逸、敏感路径、SSRF）
  │     2. check_network_access() 网络策略检查
  │     3. BwrapPolicy.wrap_bash_command() → bwrap 内核级隔离
  │   → bwrap --bind /app/workspaces/web_{user_id} /workspace
  │     → 只看到自己的工作区，无法访问其他用户数据
  │
  └─ MCP 进程
      → sandbox_policy 守卫 + wrap_mcp_stdio bwrap 包装
```

### 4.3 安装 bwrap

```bash
# Debian / Ubuntu
apt-get update && apt-get install -y bubblewrap

# RHEL / CentOS
yum install -y bubblewrap

# 验证安装
bwrap --version
```

**Docker 镜像中安装**：在 Dockerfile 中添加一行：

```dockerfile
RUN apt-get update && apt-get install -y bubblewrap && rm -rf /var/lib/apt/lists/*
```

**注意**：Docker 容器内运行 bwrap 需要以下安全选项之一：

```yaml
# docker-compose.multi.yml
services:
  worker1:
    security_opt:
      - seccomp=unconfined    # 方式 A：放宽 seccomp 限制（推荐）
    # 或
    privileged: true           # 方式 B：特权模式（不推荐）
```

> 当前 `docker-compose.multi.yml` 未显式配置 `security_opt`，部署时如启用 `PRISMCLAW_SANDBOX_ENFORCE=true` 需自行添加。或参照 `docker-compose.yml` 的单节点全栈方案，将 bash 执行委托给独立 Runtime-Agent 容器（其 Dockerfile 可独立配置安全选项）。

### 4.4 沙箱配置

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `PRISMCLAW_SANDBOX_ENFORCE` | `true` | `true`=强制 bwrap 隔离；`false`=Passthrough（无隔离，不推荐生产使用） |

**生产环境**：`PRISMCLAW_SANDBOX_ENFORCE=true`（默认），bwrap 缺失时服务拒绝启动（fail-closed）。

### 4.5 容器化 Runtime-Agent（单节点全栈部署）

主应用通过 `WorkspaceRuntimeClient`（HMAC-SHA256 签名）远程调用 Runtime-Agent 容器，每个工作区获得独立容器实例：

| Runtime-Agent 容器 | template_id | SandboxTier | 网络策略 | 适用场景 |
|---|---|---|---|---|
| `runtime-agent-default` | `default-v1` | STANDARD | 共享网络 + 代理白名单 | 通用办公 |
| `runtime-agent-restricted` | `restricted-v1` | STRICT | 完全断网（`PRISMCLAW_ALLOW_NET=false`） | 医疗 PHI / 高敏感 |

**HMAC 配置**：

| 环境变量 | 说明 |
|---|---|
| `PRISMCLAW_RUNTIME_HMAC_SECRET` | 主应用与 Runtime-Agent 共享的 HMAC 签名密钥 |
| `PRISMCLAW_RUNTIME_HMAC_KEY_VERSION` | 密钥版本（默认 `1`，支持 90 天轮换 + 7 天共存窗口） |
| `PRISMCLAW_RUNTIME_AGENT_URLS` | JSON 路由表 `{"template_id":"http://agent:9001"}` |
| `PRISMCLAW_WORKSPACE_ID` | 容器内绑定的工作区 ID（Runtime-Agent 端校验请求 workspace_id 一致） |

**template_id 路由**：主应用根据工作区 `kind` 选择对应 `template_id`，从 `PRISMCLAW_RUNTIME_AGENT_URLS` 查询对应 Runtime-Agent URL，HMAC 签名后 HTTP POST。

**双层隔离**：Runtime-Agent 容器自身通过 Docker namespace 实现文件系统/进程隔离（`PRISMCLAW_WORKSPACE_ID` 限定请求范围）；容器内 `RuntimeExecutor` 还会调用 `BwrapPolicy.wrap_bash_command()` 进行 bwrap 内核级隔离（前提是 Runtime-Agent 镜像已安装 bubblewrap，当前 `Dockerfile.runtime-agent` 默认未安装——若需启用，在 Dockerfile 中追加 `RUN apt-get update && apt-get install -y --no-install-recommends bubblewrap && rm -rf /var/lib/apt/lists/*`，并配置 `security_opt: - seccomp=unconfined`）。未安装 bwrap 时 Runtime-Agent 自动降级为容器级隔离（`_resolve_sandbox_policy()` 返回 `None`，记 warning）。

### 4.6 workspace_root 传递链

多 Worker 共享存储场景下，每个用户的 bash 命令必须只挂载自己的工作区：

```
HTTP 请求（含 user_id / workspace_id）
  → chat_runner 从 session state 获取 workspace_root
    → RunRequest(sandbox_policy=..., workspace_root="/data/workspaces/web_alice/default")
      → runner 初始化 ToolContext(workspace_root=...)
        → BashTool → runtime.bash(command, workspace_root=...)
          → RuntimeExecutor.bash(command, workspace_root=user_workspace)
            → server.py: _validate_path_within_workspace(workspace_root) ← 防止路径穿越
            → BwrapPolicy.wrap_bash_command(cmd, ctx)
              → _build_bwrap_args(workspace_root, tier)
                → --bind workspace_root /workspace  ← per-user 隔离
```

**安全断言**：`_build_bwrap_args` 在生成 bwrap 参数前会校验 workspace_root 不落在敏感路径内（`/root`、`/home`、`/etc/ssh`、`/etc/shadow`、`/var/lib/docker` 等），如果检测到则抛出 `ValueError` 阻止执行。

### 4.7 隔离级别

| SandboxTier | 文件系统 | 网络策略 | 环境变量 | 适用场景 |
|-------------|---------|---------|---------|---------|
| STANDARD | 工作区读写 + 系统只读 | 共享网络 + 代理白名单 | clearenv + 白名单 | 通用办公 |
| TIGHT | 工作区读写 + 系统只读 | 共享网络 + 代理白名单 | clearenv + 白名单 | 法律案件 |
| STRICT | 工作区读写 + 系统只读 | 完全断网（`--unshare-net`） | clearenv + 白名单 | 医疗 PHI |

### 4.8 网络代理（可选）

STANDARD/TIGHT tier 可通过代理实现域名级网络过滤：

```bash
# 在 Worker 环境变量中配置代理
export PRISMCLAW_SANDBOX_PROXY=http://proxy:3128
```

bwrap 沙箱会将 `https_proxy` / `http_proxy` 注入子进程环境，由代理服务器做域名白名单过滤。

### 4.9 安全效果

| 攻击向量 | 防护层 | 防护机制 |
|---------|--------|---------|
| 文件工具读工作区外 | WorkspaceResolver + validate_path | 应用层阻断 |
| HTTP API 路径穿越 | `_validate_path_within_workspace()` | 服务端 `resolve()` + `relative_to()` 校验，403 拒绝 |
| bash 读其他用户工作区 | BwrapPolicy / Runtime-Agent 容器隔离 | 内核级阻断 — 只挂载自己的 `/workspace` |
| bash 写系统文件 | BwrapPolicy | 内核级阻断 — `/usr` `/lib` `/bin` 只读 |
| bash 读系统敏感文件 | BwrapPolicy | 内核级阻断 — `/root` `/home` `/etc/ssh` 不挂载 |
| bash 网络外泄（STRICT） | BwrapPolicy | 内核级阻断 — `--unshare-net` 或 `PRISMCLAW_ALLOW_NET=false` |
| bash 环境变量密钥泄漏 | BwrapPolicy + env_strip | 内核级 `--clearenv` + 白名单注入 |
| bash 进程逃逸 | BwrapPolicy | 内核级 namespace + seccomp |
| workspace_root 落在敏感路径 | `_HARDCODED_DENY_BIND` 断言 | 启动时 ValueError 阻止 |
| enforce=True 但 bwrap 缺失 | `resolve_sandbox_state()` | fail-closed 拒绝启动 |
| Runtime-Agent 请求伪造 | HMAC-SHA256 + workspace_id 匹配 | 容器外请求被拒（401） |

### 4.10 故障排查

| 症状 | 排查 |
|------|------|
| Worker 启动报 `SandboxStartupError: bwrap binary not found` | 检查 `apt install bubblewrap` 是否成功；检查 `bwrap --version` |
| bash 命令返回 `EPERM` 或 `Permission denied` | bwrap 正常行为——命令尝试访问沙箱边界外的资源 |
| MCP 服务器标记 `failed: sandbox-config` | 检查 MCP 进程是否需要特殊路径或环境变量，在 `default_env_allowlist` 中添加 |
| 容器内 bwrap 报 `Failed to create user namespace` | 添加 `security_opt: - seccomp=unconfined` 到 docker-compose.yml |
| workspace_root 报 `ValueError: falls inside denied path` | workspace_root 配置了敏感路径（如 /root、/home），需改为专用数据目录 |
| Runtime-Agent 健康检查失败 | `docker exec runtime-agent-default curl localhost:9001/health` 检查容器内服务 |
| 主应用报 `Runtime-Agent 401 Unauthorized` | HMAC_SECRET 主应用与 Runtime-Agent 不一致；检查 `.env` 与 compose 一致性 |
| 主应用报 `template_id not found in agent URLs` | `PRISMCLAW_RUNTIME_AGENT_URLS` JSON 缺少对应 template_id 条目 |

---

## 五、私有化部署裁剪

PrismClaw 支持按业务域裁剪镜像，减小私有化交付体积。业务域注册表位于 `scripts/compile/domains.yaml`（唯一真实来源）。

### 5.1 启用域

编辑 `scripts/compile/domains.yaml`，将目标域的 `enabled` 改为 `true`：

```yaml
domains:
  medical:
    enabled: true   # 启用医疗域
  legal:
    enabled: false  # 按需启用
```

### 5.2 构建期裁剪

通过 Docker build-arg `PRISMCLAW_DOMAINS` 覆盖 `domains.yaml`（逗号分隔，空值 = 全量编译）：

```bash
# 仅医疗域
docker build --build-arg PRISMCLAW_DOMAINS=medical -t prismclaw:medical -f deploy/Dockerfile .

# 医疗 + 法律
docker build --build-arg PRISMCLAW_DOMAINS=medical,legal -t prismclaw:medlegal -f deploy/Dockerfile .

# 全量（默认）
docker build -t prismclaw:latest -f deploy/Dockerfile .
```

`Dockerfile` 在 Stage 1（frontend-builder）和 Stage 2（builder）都会读取 `PRISMCLAW_DOMAINS`：

- **Stage 1**：执行 `node scripts/compile/domains-registry.mjs` 生成 `_lazy_map.generated.ts`，前端只打包启用域的 chunk
- **Stage 2**：从 `scripts/compile/out/.domains.env` 读取 `PRISMCLAW_BACKEND_PATHS`，只 `pip install` 启用域的子包

### 5.3 沙箱模板镜像构建

`deploy/templates/build.py` 从 `SandboxTemplate` 规格生成 Dockerfile 并构建沙箱基础镜像：

```bash
# 构建所有内置模板（dry-run 预览）
python deploy/templates/build.py --all --dry-run

# 构建指定模板并推送
python deploy/templates/build.py --template office-v1 --push
```

自定义模板（`TenantTemplate`，基于基础模板叠加 `extra_apt` / `extra_pip` / `extra_npm`）：

```python
from prismclaw.engine.runtime.template import TenantTemplate
# build_tenant_template(tenant_tmpl, base_tmpl, push=True)
```

---

## 六、安全注意事项

| 项目 | 说明 |
|------|------|
| TLS | 生产环境**必须**启用 HTTPS，HTTP 自动 301 跳转 |
| 密码 | 所有 `replace-me-*` 占位符**必须**替换为强随机值 |
| HMAC_SECRET | 主应用 ↔ Runtime-Agent 共享密钥，**必须**设置为强随机值（默认 `dev-secret` 不可用于生产） |
| 端口 | 多 Worker 部署中 PostgreSQL/Redis 不暴露到宿主机（仅 backend 网络内部通信） |
| Redis | 多 Worker 部署已禁用 `FLUSHDB` / `FLUSHALL`，防止误操作清空数据 |
| Rate Limiting | 认证接口 5 req/s，聊天接口 10 req/s，通用 API 30 req/s |
| 管理员密码 | 首次启动后 `BOOTSTRAP_ADMIN_PASSWORD` 即失效，不再使用 |
| 镜像版本 | 生产环境建议将 `tensorchord/vchord-suite:pg17-latest` 锁定到不可变 digest（`@sha256:<digest>`） |

---

## 七、云端模式说明

当用户通过非 `localhost`（如公网域名）访问时：
- 前端自动检测并触发 `CLOUD_MODE_REQUIRE_VIRTUAL_WORKSPACE`
- UI 隐藏"选择本地目录"按钮，改为"新建虚拟工作区"
- 后端 `_is_local_request` 拦截器作为第二道防线，拒绝非本地的物理目录绑定请求（403）

---

## 八、故障排查

| 症状 | 排查 |
|------|------|
| Worker 启动失败 | `docker logs prismclaw-worker1-1` 查看错误日志；检查 `.env` 中必填变量 |
| 数据库连接失败 | `docker exec prismclaw-postgres-1 pg_isready -U postgres`（单节点）或 `-U prismclaw`（多 Worker）；检查 `POSTGRES_PASSWORD` 一致性 |
| Redis 连接失败 | `docker exec prismclaw-redis-1 redis-cli -a "${REDIS_PASSWORD}" ping`（多 Worker）；单节点若启用密码同样使用 `-a`，未启用则 `redis-cli ping` |
| Nginx 502 | 检查 Worker 是否 healthy；检查 upstream 端口与 Worker 端口映射一致（单节点 8000 / 多 Worker 8001-8003） |
| WebSocket 断连 | 检查 Nginx `proxy_read_timeout`（默认 600s）；检查 `proxy_set_header Upgrade` |
| 前端白屏 | 检查 `/var/www/prismclaw/index.html` 是否存在；检查 Nginx `root` 路径 |
| Runtime-Agent 不响应 | `docker compose -f deploy/docker-compose.yml logs runtime-agent-default`；检查 HMAC_SECRET 一致性 |
| bwrap 报 user namespace 错误 | Worker 容器添加 `security_opt: - seccomp=unconfined`，或切换到单节点全栈方案 |
