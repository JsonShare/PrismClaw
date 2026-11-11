# PrismClaw 系统架构

> 企业级 B/S 架构的 AI Agent 平台：员工用浏览器打开即用，IT 用一套镜像完成私有化交付。医疗、法律、办公、通用四类领域能力以领域包按需装配。

**[交互式架构图](diagrams/system-architecture.html)**

---

## 1. 一张图看全系统

```
浏览器（React SPA，零客户端安装）
        │  HTTPS / WSS
        ▼
┌──────────────────────────────────────────────────────┐
│ Nginx：TLS 终结 · 静态资源 · 反向代理 · 限流 · 安全头 │
└───────────────────────┬──────────────────────────────┘
                        │ ip_hash 会话粘性
                        ▼
┌──────────────────────────────────────────────────────┐
│ FastAPI Worker × N（无状态，可水平扩展）              │
│  /api/ws   WebSocket 对话流（流式推送、中断、接管）   │
│  /api/*    REST：会话 / 工作区 / 文件 / 技能 / 设置   │
│  /api/v1/* OpenAI 兼容接口（SSE），对接企业既有系统   │
│  /health   /metrics                                   │
└───────┬──────────────────────┬───────────────────────┘
        │                      │
        ▼                      ▼
 PostgreSQL 17           Redis 7                  沙箱执行
 会话/记忆/审计/轨迹      会话/锁/亲和/队列          bwrap（进程级）
 VectorChord 向量检索                             Runtime-Agent（容器级）
```

单节点部署可省掉 Nginx：Worker 内置 SPA 托管（`SPAStaticFiles`），一个容器就是完整系统。

浏览器里只有六个入口，学习成本一次性：**对话**（主工作区）、**技能**、**定时任务**、**成果**（输出文件与预览）、**领域工作台**（医疗/法律/办公按开通显示）、**设置**。左侧会话按工作区分组，右侧可直接预览与编辑文档，不需要切到别的系统。

---

## 2. 一次对话的处理链路

```
浏览器 ──WSS──▶ /api/ws ─▶ ChatRunner ─▶ ChatService ─▶ Agent Loop
                                                      ├─ LLM 流式输出
                                                      ├─ 工具调用 → SafetyGate 门控 → 沙箱执行
                                                      └─ 技能调度 / 子代理 / MCP 外部工具
        ◀── chat.delta 逐字上屏 ────────────────────────┘
        ◀── chat.done 归档（与流式内容逐字节一致）
```

要点：正文单 pass 实时推送，不做二次缓冲；客户端刷新后可按事件重建会话（Event Sourcing）。

---

## 3. 为什么这么设计

| 决策 | 做法 | 企业收益 |
|------|------|---------|
| **B/S 而非桌面端** | React SPA + FastAPI，浏览器直接访问 | 员工零安装零升级；版本只随服务端发布，多分支交付不失控 |
| **模组化单体而非微服务** | 五层单向依赖 + Protocol 注入，进程内组装 | 单机与集群同一份代码；排障在一个进程内完成，不引入服务网格和分布式事务 |
| **水平扩展靠无状态 Worker** | Redis 会话亲和 + 网关转发 + 目标不可达时强制接管 | 加机器即扩容；滚动升级、单点重启对用户透明，会话不丢 |
| **沙箱与业务进程分离** | bwrap 进程级 / Runtime-Agent 容器级；未配置即拒绝启动 | 不可信代码执行被限制在边界内，满足等保与内审取证要求 |
| **单一事实源** | PostgreSQL 承载会话、记忆、审计、轨迹，pgvector 类检索内置 | 备份、迁移、审计只面对一个库；不额外引入向量库运维 |
| **Redis 只做易失协调** | 只存会话状态、锁、亲和、队列 | Redis 故障只影响并发协调，不丢业务数据 |
| **领域能力按需装配** | 领域包 + `/api/domains/manifests` 下发菜单与页面 | 同一套前端按客户开通医疗 / 法律 / 办公，不为客户分支代码 |
| **OpenAI 兼容入口** | `/api/v1/chat/completions`、`/api/v1/models` | 企业已有应用与 SDK 改一个 base_url 即可接入 |

---

## 4. 分层：五层单向依赖

```
app      (L4) 通信层：HTTP/WS 路由、JWT 认证、网关、SPA 托管
  ↓
assembly (L3) 编配层：依赖注入、Bundle 启动、插件装配
  ↓
runtime  (L2) 运行时层：会话编排、能力刷新、engine 门面（facade）
  ↓
engine   (L1) 引擎层：Agent 循环、工具、记忆、沙箱、领域逻辑
  ↓
schema   (L0) 契约层：Protocol + 数据类型 + 配置，零依赖
```

支撑包：`persistence`（PostgreSQL 实现）、`infra`（连接/路径/日志/安全）。

硬规则：`schema` 不 import 上层；`app` 不直接 import `engine`，必须经 `runtime.facade`。

这样做的原因：换存储实现不动业务逻辑，换传输层不动引擎，测试可用假实现替换；同时防止仓库随时间腐化成"处处互相引用"的泥球。

---

## 5. 部署形态

| 形态 | 适用 | 组成 | 沙箱 |
|------|------|------|------|
| 单节点全栈 | 中小规模、私有化交付 | `docker-compose.yml`：app + PG + Redis + Runtime-Agent | 容器级 |
| 多 Worker | 高并发、高可用 | `docker-compose.multi.yml`：Nginx + 3×Worker + PG + Redis | 进程级 bwrap |

日常运维只需三条命令：

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d   # 启动 / 升级
curl -fsS http://127.0.0.1:8000/health                                      # 健康检查
docker compose exec postgres pg_dump -U postgres prismclaw > backup.sql     # 备份
```

"操作简单"的具体落点：

- 首次启动自动建表、自动创建管理员（bootstrap 环境变量）
- 前端静态资源由服务端托管，不需要单独部署站点
- 配置收敛在 `backend/configs/*.yaml` + 环境变量，复制一套即可交付新客户
- 领域通过参数或领域包开关启用，不重新编译前端

完整步骤见 [DEPLOY.md](../../deploy/DEPLOY.md)。

---

## 6. 功能模块索引

| # | 模块 | 一句话职责 | 文档 |
|---|------|-----------|------|
| 1 | 执行引擎 | Agent Loop、工具调度、终止与恢复 | [01](modules/01-execution-engine.md) |
| 2 | 技能系统 | 技能发现/激活/调度/遵从度 | [02](modules/02-skill-system.md) |
| 3 | 插件系统 | 插件加载、生命周期、Hook、信任 | [03](modules/03-plugin-system.md) |
| 4 | 沙箱安全 | 门控决策、隔离执行、审计、DLP | [04](modules/04-sandbox-security.md) |
| 5 | 记忆系统 | 四层记忆、写入仲裁、检索降级 | [05](modules/05-memory-system.md) |
| 6 | Dreaming | 三阶段记忆整理与治理 | [06](modules/06-dream-engine.md) |
| 7 | 上下文压缩 | 预算编排、历史压缩、保真校验 | [07](modules/07-context-compression.md) |
| 8 | MCP 集成 | 外部工具协议适配与网络 ACL | [08](modules/08-mcp-integration.md) |
| 9 | 子代理 | 定义、并行执行、结果回收 | [09](modules/09-subagent.md) |
| 10 | 工具集 | 内置 bash/文件/搜索工具 | [10](modules/10-toolkit.md) |
| 11 | LLM 服务 | 多 Provider 适配、流式、回退 | [11](modules/11-llm-service.md) |
| 12 | Web 层 | HTTP/WS、认证、路由、会话 | [12](modules/12-web-layer.md) |
| 13 | 运行时管理 | 工作区容器、预热池、资源分级 | [13](modules/13-runtime-management.md) |
| 14 | 审查系统 | 文档变更审查与内容存储 | [14](modules/14-review-system.md) |
| 15 | 策展器 | 后台扫描与记忆毕业 | [15](modules/15-curator.md) |
| 16 | 调度器 | 定时任务调度与执行 | [16](modules/16-scheduler.md) |

领域设计：医疗 → [medical-domain-design.md](biz_domain/medical/medical-domain-design.md)、法律 → [legal-domain-design.md](biz_domain/legal/legal-domain-design.md)、领域包机制 → [domain-bundle-design.md](biz_domain/_shared/domain-bundle-design.md)

---

## 7. 贯穿全局的设计约束

| 约束 | 含义 |
|------|------|
| **Fail-closed** | 未注册工具按最严分类；关键基础设施未配置则拒绝启动，不降级放行 |
| **只收紧不放松** | 策略覆盖、意图信号只能升级管控，不能绕过基线策略 |
| **终止权单一** | 循环终止只有一处判定，避免多个模块各自打断导致行为不可解释 |
| **检索 fail-open** | 记忆检索超时/异常时走确定性降级，绝不挂起用户回合 |
| **事件即真相** | 会话状态由事件流投影重建，前端与后端逐字节一致 |
| **单一事实源** | 业务数据只落 PostgreSQL；缓存与队列仅做协调 |
| **写侧受控词表** | 记忆类型、置信度、来源阶段在写入时归一，查询侧不做猜测 |
