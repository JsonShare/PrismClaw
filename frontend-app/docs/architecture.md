# PrismClaw 移动端跨端方案 · 技术架构说明

> 版本：v1.7 ｜ 日期：2026-11-09 ｜ 适用仓库：`PrismClaw`
> 配套交付：`frontend-app/`（Taro 移动端实现，直连真实后端）、`docs/build-and-deploy.md`（编译部署流程）
>
> v1.1 调整：取消底部导航栏；默认首页改为「新对话」；任务列表收敛到左上角侧边抽屉；人机交互 / 待办 / 通知进入具体任务页面处理，不再设独立入口。
>
> v1.2 调整：首页采用 Chat / Work 双模式（新建会话继承当前模式）；明确移动端**直连后端服务器**，不存在连接 PC、选择电脑的配对与中继链路。
>
> v1.3 调整：移动端技术栈整体切换为 **Taro 4.x + React 18 + TypeScript + Sass**；状态管理改为 Zustand、路由改为 `app.config.ts`、App 端改为 Taro 构建 H5 产物 + Capacitor 套壳。业务范围、接口清单、WS 协议（`protocol_version=1`）与导航结构保持不变。
>
> v1.4 调整：「全部任务」由首页侧滑半屏抽屉改为**独立全屏页面** `pages/tasks`，左上角 ☰ 通过 `Taro.navigateTo` 进入，页面内自带返回箭头与 ＋；不再存在遮罩层与覆盖式面板。
>
> v1.5 调整：视觉体系重构（「精密仪器 + 编辑排版」）：暖纸色画布、衬线标题、等宽体元信息、三棱镜渐变色标、发丝分割线替代重阴影；首页新增索引式建议入口，全部任务页新增计数与摘要、快捷入口改为卡片行；同步刷新设计令牌。
>
> v1.6 调整：推翻 v1.5 的装饰性风格，改为「Calm Product」中性专业风格——中性灰画布 `#F6F7F8`、单一品牌蓝 `#2C59BC`、仅系统字体、统一字号/间距/圆角与 8px 基准间距；移除渐变、点阵纹理、衬线/等宽装饰、索引编号与彩色色标；首页问候与建议改为常规层级并垂直居中，输入区去掉内部分割线与原生 textarea 偏差，个人页修复资料卡姓名对比度，登录页去掉开发元信息。
>
> v1.7 调整：设计令牌与 PC 端 `frontend/src/theme/light.css` 完全对齐（画布 `#EBEEF2`、卡片 `#FDFBF7`、边框 `#D7DDE6`、正文 `#1F232B`）；首页建议改为圆角 chips，输入区按 PC `ChatInputBox` 重做（Plus 18 / Sparkles 18 / Mic 18 / 36px 深色圆形 ArrowUp），Chat·Work 与工作目录改为下拉按钮；会话页移除逐条 AI 头像、用户气泡改 10% 品牌色、停止键改危险色；全部任务页移除底部「我的」与表头 ＋，入口移至右上角头像，下箭头取消，右下角新增悬浮新建任务按钮；全端用词统一为「服务器 / 工作目录」，不再出现「云端」。
>
> v1.8 调整：落地《移动端设计系统》`docs/design-system.html`——新增 `$info / $radius-modal / $shadow-modal` 令牌；新增统一弹框组件 `AppDialog`（Alert / Confirm / Danger / Form / Sheet），替换 `Taro.showModal` 与 `Taro.showActionSheet`；补齐删除会话、删除自动化、退出登录、暂停任务、新建自动化、清除缓存、字段校验等交互节点，并为空列表提供标准空状态。

---

## 1. 目标与范围

为现有 B/S 架构的 PrismClaw 增加一套移动端，要求：

| 目标 | 落地方式 |
| --- | --- |
| 一套代码编译 Android / iOS / 微信小程序 / 支付宝小程序 / H5 | Taro 4.x（React 18 + TypeScript + Sass） |
| 与 PC 端功能对齐 | 新对话、会话与工作流、定时任务、产出物、设置；人机交互 / 待办 / 通知在任务详情内处理 |
| 两端数据互通、状态同步 | 复用同一 `session_id` / `conversation_id`，WebSocket 实时事件 + HTTP 兜底拉取 |
| 前后端分离、复用同一后端 | 不新增业务服务，直接调用现有 `/api/*` 与 `/api/ws` |
| 两端均可独立完成全流程 | 移动端支持建会话、发消息、澄清确认、停止生成、下载产出物、启停/触发定时任务 |

\* 待办与通知：现有后端**没有**独立的 REST 资源（见 §10），移动端当前未实现，接入方案单列。

**PC 端不动**：现有 `frontend/` 是 React 19 + Vite 应用，继续作为 PC Web 端存在；移动端是新增的独立 Taro 工程（建议目录 `mobile/`），两者只共享后端接口与设计令牌。

---

## 2. 技术选型

### 2.1 结论

- **框架**：Taro 4.x + React 18 + TypeScript；小程序端走 Taro 默认的 webpack5 编译链，H5 可选 Vite 编译器
- **状态**：Zustand（多 store 切片，`useSyncExternalStore` 天然适配 React 18 并发渲染）
- **请求**：`Taro.request` 统一封装（不用 axios，避免小程序无 `XMLHttpRequest` 的兼容问题）
- **实时**：`Taro.connectSocket` 单连接多路复用
- **路由**：`app.config.ts` 的 `pages` 数组 + `window` 配置；**不配置 `tabBar`**
- **样式**：Sass（`.scss`）+ 设计令牌 `tokens.scss`；尺寸用 `rpx`（Taro 的 `pxtransform` 换算）
- **富文本**：`markdown-it` + Taro `RichText`（复杂节点降级为纯文本或自绘代码块）
- **UI**：自建设计令牌 + Taro 内置组件（`View` / `Text` / `ScrollView` / `Image`）；不引入重 UI 框架，保证小程序包体积与一致性
- **图标**：小程序不支持内联 `<svg>`，统一用 base64 SVG Data URI + `<Image>` 封装 `Icon` 组件
- **App**：Taro 构建 H5 产物 + Capacitor 套壳（iOS/Android），不走 Taro RN

### 2.2 选型对比

| 维度 | Taro 4 (React) | Flutter | React Native |
| --- | --- | --- | --- |
| 小程序支持 | 支持微信/支付宝等，编译到原生小程序 | 不支持 | 不支持 |
| App + H5 | H5 原生支持；App 走 H5 + Capacitor 套壳 | 支持 | 支持 |
| 与现有 React 复用 | 逻辑层同栈，类型与工具函数可直接复用 | 全部重写 | 全部重写 |
| 生态/招人 | 较成熟 | 独立生态 | 独立生态 |
| 包体积 | 中 | 大 | 大 |
| 结论 | **已采用** | 仅 App | 不满足小程序 |

**结论**：选 Taro 的核心原因是 React 同栈——团队现有 React 经验与类型定义可直接复用到移动端逻辑层；一套源码同时产出微信/支付宝小程序与 H5；App 端用「Taro 构建 H5 + Capacitor 套壳」规避 Taro RN 的实验性路径，原生壳只负责签名、推送与权限。

### 2.3 速度与体积约束

- 无底部 Tabbar，首屏只加载「新对话」；任务详情、我的、设置按需进入。
- 微信小程序主包 ≤ 2 MB、总包 ≤ 20 MB：主包放新对话、任务详情、我的、设置与登录，后续新增能力再拆分包。
- 富文本与代码高亮按需加载；`highlight.js` 只注册常用语言。
- 图片走 CDN；图标不落地 `.svg` 文件，统一以 base64 Data URI 形式内置在 `Icon` 组件中。

---

## 3. 总体架构

```
┌──────────────────────────── 客户端层 ────────────────────────────┐
│  Taro 工程（一套源码）                                            │
│  ├─ src/pages/         新对话 / 任务详情（会话·定时）/ 我的 / 设置 / 登录 │
│  ├─ src/components/    消息气泡、工具时间线、人机交互卡、待办与通知列表、Icon │
│  ├─ src/stores/        auth / session / chat / task / ui（Zustand）│
│  ├─ src/api/           request.ts（Taro.request 封装）             │
│  │                     http/*.ts（按资源分模块）                    │
│  ├─ src/realtime/      socket.ts（Taro.connectSocket + 重连 + 路由）│
│  ├─ src/platform/      h5 / weapp / alipay / app 差异封装          │
│  ├─ src/styles/        tokens.scss（与 PC 同一套色板与圆角）         │
│  ├─ src/app.config.ts  路由与窗口配置（不含 tabBar）                │
│  └─ src/app.ts / app.scss  入口与全局样式                           │
└───────────────┬──────────────────────────────┬───────────────────┘
                │ HTTPS (Bearer JWT)            │ WSS /api/ws
                ▼                               ▼
┌────────────────────────── 接入层 ─────────────────────────────────┐
│ Nginx：/api/ 反向代理、/api/ws 升级、静态资源、HTTPS                │
│ （现有 deploy/nginx.conf 已具备 /api/ws、/api/ 转发与静态托管）      │
└───────────────────────────────┬──────────────────────────────────┘
                                ▼
┌────────────────────────── 服务层（现有，不改） ────────────────────┐
│ FastAPI（prismclaw.app）                                          │
│  ├─ /api/auth/*           登录、注册、邮箱验证码、找回密码           │
│  ├─ /api/sessions/*       会话 CRUD、消息、模型、反馈、轨迹          │
│  ├─ /api/workspaces/*     工作区、产出物                            │
│  ├─ /api/scheduled-tasks  定时任务 CRUD / trigger / runs            │
│  ├─ /api/files/*          上传、下载、预览、搜索                    │
│  ├─ /api/skills/*         技能列表与启用                            │
│  ├─ /api/settings|profile 设置与用户资料                            │
│  ├─ /api/ws               WebSocket（identify / chat.* / task.*）   │
│  └─ /api/speech/stream    语音识别 WebSocket                        │
└───────────────────────────────────────────────────────────────────┘
```

**关键原则**：移动端不直连数据库、不做业务规则判断；所有状态以服务端为准，客户端只做乐观更新与缓存。

---

## 4. 复用后端接口清单

移动端所需接口全部已存在（除待办/通知），按页面归类：

### 4.1 认证

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/auth/token` | 登录，body `{identifier, password}`，返回 `{token, user_id, nickname}` |
| POST | `/api/auth/email-code` | 发送邮箱验证码（SMTP 关闭时返回 204 但不发信） |
| POST | `/api/auth/register` | 注册（需验证码） |
| POST | `/api/auth/reset-password` | 找回密码（需验证码） |

- JWT：HS256，`Authorization: Bearer <token>`，`jwt_expires` 默认 **2592000 秒（30 天）**，无 refresh token。
- 文件下载兼容 `?_token=<jwt>`（后端 `get_current_user` 支持 query 回退），移动端下载大文件时可用。
- 邮箱验证码相关接口（`email-code` / `register` / `reset-password`）依赖 SMTP；未配置 SMTP 时后端直接返回 503。移动端**未实现**注册与找回密码表单，登录页的「立即注册 / 忘记密码」仅提示联系管理员创建账号或重置密码——与上述部署约束一致。

### 4.2 会话与消息

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/sessions?limit=10&offset=0` | 会话列表（`items[]`、`has_more`） |
| POST | `/api/sessions` | 新建会话 `{workspace_id, title}` → `{session_id}` |
| GET | `/api/sessions/{session_id}` | 会话详情 |
| PATCH | `/api/sessions/{session_id}` | 重命名 `{title}` |
| DELETE | `/api/sessions/{session_id}` | 删除 |
| GET | `/api/sessions/{session_id}/messages?offset=0&limit=50` | 消息历史 |
| GET | `/api/sessions/{session_id}/model` | 可用模型与当前模型 |
| PUT | `/api/sessions/{session_id}/model` | 切换模型 |
| POST | `/api/sessions/{session_id}/feedback` | 点赞/点踩 |
| GET | `/api/sessions/{session_id}/trajectory` | 执行轨迹 |
| GET | `/api/sessions/{session_id}/trajectory/cells?after_seq=` | 轨迹增量 |
| GET | `/api/conversations/{session_id}/run` | 运行态快照 |
| GET | `/api/conversations/running` | 用户级「正在运行」会话列表（跨工作区） |

### 4.3 定时任务 / 文件 / 其他

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/scheduled-tasks` | 任务列表 |
| POST | `/api/scheduled-tasks` | 新建任务 |
| PATCH | `/api/scheduled-tasks/{id}` | 更新（启停、名称、cron） |
| DELETE | `/api/scheduled-tasks/{id}` | 删除 |
| POST | `/api/scheduled-tasks/{id}/trigger` | 立即触发 |
| GET | `/api/scheduled-tasks/{id}/runs` | 运行记录 |
| GET | `/api/workspaces` | 工作区列表 |
| GET | `/api/workspaces/outputs?workspace_id=` | 产出物列表 |
| POST | `/api/files/upload` | 附件上传（multipart） |
| GET | `/api/files/download`、`/raw` | 下载 / 原文件 |
| GET | `/api/files/list`、`/text`、`/search` | 文件浏览 |
| GET | `/api/skills/list` | 技能列表 |
| GET | `/api/settings`、`/api/profile` | 设置与用户资料 |
| GET | `/api/health` | 健康检查（启动探活） |

> 说明：`session_id` 形如 `web:{user_id}:{workspace_id}:{conversation_id}`，由后端返回，移动端**原样透传**，不要自行拼接。

---

## 5. 认证与会话

### 5.1 登录时序

```
App/小程序               后端
   │  POST /api/auth/token  │
   │───────────────────────>│  校验 identifier/password
   │<───────────────────────│  {token, user_id, nickname}
   │ Taro.setStorageSync     │
   │  连接 /api/ws           │
   │  identify {token, v:1}  │
   │───────────────────────>│  verify_jwt + 版本/连接数校验
   │<───────────────────────│  ready {conversations, runtime_mode}
```

### 5.2 存储与续期

- Token 存 `Taro.setStorageSync('pc_token', token)`；App 端（Capacitor）可叠加生物识别解锁。
- 无 refresh token：收到 HTTP 401 或 WS close `4003` 时，清理本地态并跳转登录页；重新登录后恢复会话。
- H5 端注意 XSS：不在 URL 中携带 token（下载除外），不把 token 写进日志。

---

## 6. 实时同步机制（核心）

### 6.1 WebSocket 协议（现有，移动端需完全兼容）

- 地址：`wss://<host>/api/ws`（H5 同源可 `wss://`；小程序必须 wss 且域名白名单）
- 首帧必须在 30 秒内发送 `identify`，否则服务端 `close(4001)`：

```json
{ "type": "identify", "token": "<jwt>", "protocol_version": 1 }
```

- 服务端先发 `hello`（含心跳间隔），鉴权通过后发 `ready`。
- 心跳：服务端发 `{"type":"ping"}`，客户端必须回 `{"type":"pong"}`；超时 `close(4006)`。
- 关闭码：`4001` 未 identify、`4002` 首帧非 identify、`4003` token 无效、`4004` 连接数超限、`4005` 协议版本不符、`4006` 心跳超时。

**客户端 → 服务端**

| type | 关键字段 | 用途 |
| --- | --- | --- |
| `identify` | token, protocol_version | 建连鉴权 |
| `chat.send` | conversation_id, workspace_id, content, client_message_id, mode(`chat`/`work`), attachments | 发送消息 |
| `chat.steer` | conversation_id, content, client_message_id | 运行中追加指令 |
| `chat.cancel` | conversation_id | 取消排队 |
| `chat.abort` | conversation_id | 中止执行 |
| `tool.clarify` | conversation_id, clarify_id, tool_call_id, answer | 人机交互回答 |
| `pong` | — | 心跳应答 |

**服务端 → 客户端**

| type | 关键字段 | 处理 |
| --- | --- | --- |
| `chat.start` | conversation_id | 标记流式开始 |
| `chat.delta` | text, segmentId | 追加文本；`segmentId` 变化时清空当前段 |
| `chat.done` | final_message, message_id, usage, aborted | 结束流式、落库、取消「执行中」 |
| `chat.queued` | position | 显示排队位次 |
| `task.runtime` / `task.timeline` | 运行态 / 时间线 | 工具调用卡片 |
| `tool.clarify_request` | clarify_id, tool_call_id, questions | 提问并等待用户回复 |
| `event:outputs` | message_id, files | 追加产出物卡片 |
| `error` | message, code | 错误提示 + 可重试 |

### 6.2 单连接多路复用

小程序 WebSocket 连接数有限（历史上 2 条），移动端必须**全局只保留一条连接**，用 `conversation_id` 路由事件到对应会话 store；页面切换不重建连接。

### 6.3 断线重连与补偿

```
onClose/onError
  → 指数退避 1s / 2s / 4s / 8s / 16s（上限 30s，加 ±20% 抖动）
  → 重新 identify
  → GET /api/conversations/running        恢复「执行中」状态
  → GET /api/sessions/{id}/messages?offset=已加载条数   补齐消息
  → 按 client_message_id 去重
```

- 发送消息先本地乐观插入（状态 `sending`），收到 `chat.done` 后用服务端 `message_id` 替换。
- `chat.delta` 只更新内存，不落库；页面切走再回来以 HTTP 历史为准。

### 6.4 双端一致性

| 场景 | PC 端 | 移动端 | 同步方式 |
| --- | --- | --- | --- |
| 新建会话 | 调 `POST /api/sessions` | 同 | 服务端为唯一事实源；另一端刷新列表 |
| 发消息 | WS `chat.send` | 同 | 同会话 ID，消息落库后两端可见 |
| 流式回复 | WS `chat.delta` | 同 | 服务端向该用户所有连接广播 |
| 定时任务启停 | REST PATCH | 同 | 另一端下次拉取或收到事件后刷新 |
| 产出物 | REST | 同 | 文件在工作区共享 |
| 运行态恢复 | `GET /conversations/running` | 同 | 冷启动/重连必查 |

> 注意：`PATCH` 类操作没有服务端广播，移动端需在页面 `useDidShow` 时重新拉取；若后续要求「秒级一致」，可在后端为 `scheduled_task.updated` 增加广播事件（不影响现有协议）。

---

## 7. Taro 工程结构（建议）

```
mobile/
├── package.json
├── tsconfig.json
├── babel.config.js
├── project.config.json            # 微信开发者工具项目配置（appid、setting）
├── project.alipay.json            # 支付宝小程序项目配置
├── config/
│   ├── index.ts                   # Taro 编译配置（别名、Sass、分包、输出目录）
│   ├── dev.ts
│   └── prod.ts
├── capacitor.config.ts            # App 套壳配置：webDir 指向 H5 产物
├── src/
│   ├── app.ts                     # 入口：挂载全局 store、初始化查询参数
│   ├── app.config.ts              # 路由（pages 数组）+ window 配置，不配 tabBar
│   ├── app.scss                   # 全局样式
│   ├── styles/
│   │   └── tokens.scss            # 颜色/字号/圆角/阴影（与 PC 对齐）
│   ├── api/
│   │   ├── request.ts             # Taro.request 封装、401 处理、重试
│   │   ├── auth.ts
│   │   ├── sessions.ts
│   │   ├── scheduled.ts
│   │   ├── files.ts
│   │   └── workspace.ts
│   ├── realtime/
│   │   ├── socket.ts              # 连接、identify、心跳、重连、事件分发
│   │   └── events.ts              # 协议类型定义
│   ├── stores/
│   │   ├── auth.ts                # Zustand store（create + 选择器）
│   │   ├── session.ts
│   │   ├── chat.ts
│   │   ├── task.ts
│   │   └── ui.ts
│   ├── components/
│   │   ├── MessageBubble.tsx
│   │   ├── ToolTimeline.tsx
│   │   ├── ClarifyCard.tsx         # 人机交互（tool.clarify）
│   │   ├── TodoList.tsx           # 任务内待办区块
│   │   ├── NotificationList.tsx   # 任务内通知区块
│   │   ├── Icon.tsx               # base64 SVG Data URI + <Image>
│   │   ├── TaskCard.tsx
│   │   ├── AppDialog.tsx          # 统一弹框（Alert/Confirm/Danger/Form/Sheet）
│   │   ├── BottomSheet.tsx
│   │   └── EmptyState.tsx
│   ├── platform/
│   │   ├── index.ts               # 平台能力统一出口
│   │   ├── download.ts            # H5 / 小程序 / App 下载差异
│   │   ├── recorder.ts            # 录音
│   │   └── push.ts                # 推送/订阅消息
│   └── pages/
│       ├── login/index.tsx        # 每个页面目录内附 index.scss
│       ├── home/index.tsx         # 默认首页：新对话
│       ├── chat/index.tsx         # 任务详情 · 会话
│       ├── scheduled/index.tsx    # 任务详情 · 定时任务
│       ├── me/index.tsx           # 我的（待办 / 通知汇总）
│       └── settings/index.tsx
└── capacitor/                     # 可选：Capacitor 生成的 ios/ android 原生壳
```

> 说明：可运行的移动端实现在 `frontend-app/`，页面划分与目录结构与此一致；所有数据均来自真实后端 `/api`，登录后建立 WebSocket 接收流式事件。

### 7.1 `app.config.ts` 关键配置

**不配置 `tabBar`**；首页即新对话，任务列表是独立页面 `pages/tasks`，从左上角 ☰ 进入，不是覆盖层。

```ts
// src/app.config.ts
export default defineAppConfig({
  pages: [
    'pages/home/index',
    'pages/tasks/index',
    'pages/chat/index',
    'pages/scheduled/index',
    'pages/me/index',
    'pages/settings/index',
    'pages/login/index'
  ],
  window: {
    navigationStyle: 'custom',
    backgroundColor: '#EBEEF2'
  }
})
```

> `pages` 数组顺序即入口顺序：`pages/home/index` 是默认落地页。会话详情、定时任务详情与全部任务各自独立页面，便于小程序返回栈与分享；☰ 进入全部任务会新增一层返回栈，返回即回首页。各页面自定义导航栏，统一由 `View` 绘制（不使用小程序原生导航栏）。

### 7.2 请求封装要点

```ts
// src/api/request.ts
import Taro from '@tarojs/taro'

const BASE = process.env.TARO_APP_API_BASE || ''

export async function request<T>(options: {
  url: string
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  data?: Record<string, unknown>
  auth?: boolean
}): Promise<T> {
  const token = Taro.getStorageSync('pc_token')
  const res = await Taro.request({
    url: BASE + options.url,
    method: options.method || 'GET',
    data: options.data,
    header: options.auth === false ? {} : { Authorization: `Bearer ${token}` },
    timeout: 20000
  })
  if (res.statusCode === 401) {
    Taro.removeStorageSync('pc_token')
    Taro.reLaunch({ url: '/pages/login/index' })
    throw new Error('unauthorized')
  }
  if (res.statusCode >= 400) throw new Error((res.data as any)?.detail || 'request_failed')
  return res.data as T
}
```

### 7.3 WebSocket 封装要点

```ts
// src/realtime/socket.ts
import Taro from '@tarojs/taro'

let task: Taro.SocketTask | null = null
let heartbeatTimer: ReturnType<typeof setTimeout> | null = null
let retry = 0

function connect() {
  task = Taro.connectSocket({ url: process.env.TARO_APP_WS_BASE + '/api/ws' })

  task.onOpen(() => {
    retry = 0
    task!.send({
      data: JSON.stringify({
        type: 'identify',
        token: Taro.getStorageSync('pc_token'),
        protocol_version: 1
      })
    })
  })

  task.onMessage(({ data }) => {
    const msg = JSON.parse(data as string)
    if (msg.type === 'ping') {
      task!.send({ data: JSON.stringify({ type: 'pong' }) })
      return
    }
    if (msg.type === 'hello') {
      heartbeatTimer = setTimeout(() => task?.close({ code: 4006 }), msg.data.heartbeat_interval + 5000)
      return
    }
    dispatch(msg) // 按 conversation_id 路由到 store
  })

  task.onClose((res) => {
    task = null
    if (res.code === 4003) return Taro.reLaunch({ url: '/pages/login/index' })
    scheduleReconnect()
  })
  task.onError(() => scheduleReconnect())
}

function scheduleReconnect() {
  const delay = Math.min(30000, 1000 * 2 ** retry++) * (0.8 + Math.random() * 0.4)
  setTimeout(connect, delay)
}
```

> 心跳：服务端 ping 间隔由 `hello` 给出；客户端除回 `pong` 外，还需在超过 `pong_timeout` 无消息时主动重连，避免半死连接。连接实例放在模块级单例中，不随 React 组件挂载/卸载创建，页面切换不重建连接。

---

## 8. 页面与功能映射（移动端直连服务器）

**双模式：Chat / Work**

| 模式 | 定位 | 首页文案 | 发起示例 | 会话行为 |
| --- | --- | --- | --- | --- |
| Chat | 轻量对话：问答、解释、总结、对比 | 随时聊聊，从这里开始 | 解读检验报告 / 核对用药禁忌 | 单轮或多轮对话，结果直接在消息流返回 |
| Work | 任务执行：多步处理、产出文件、定时调度 | 把任务交给我，在云端执行 | 整理门诊病历质控 / 汇总随访名单 | 展示工具时间线、产出物、人机交互卡，可转定时任务 |

- 模式在**输入框下方**切换，与「工作区」选择并排；新建会话继承当前模式；已有会话按自身的 `mode` 在详情页顶部标注。
- 任务一旦开始执行（进入会话 / 正在流式输出），首页的 Chat / Work 与工作区切换即隐藏，避免执行中改意图。
- 模式只是同一后端上的会话意图，**不改变连接方式**：移动端始终直连 PrismClaw 服务器，没有「连接哪台 PC」「选择项目」的配对步骤。
- 模式切换不写路由、不重连 WebSocket；仅影响首页引导语、输入框占位与新建会话的初始意图。

**导航结构（冻结）**

| 位置 | 内容 |
| --- | --- |
| 默认首页 | 「新对话」页面（☰ + 问候与两行引导语 + 底部输入坞；输入框下方是 Chat / Work 与工作目录下拉按钮） |
| 左上角 ☰ | 跳转到全屏「全部任务」页：按空间分组的任务列表（会话 + 定时任务），当前空间默认展开 |
| 右上角头像 | 进入「我的」页（资料、统计、待办与通知） |
| 任务详情 | 会话与定时任务各自一个页面，返回即回首页 |
| 无底部导航 | 不配置 `tabBar`；核心入口只有「全部任务」与「我的」 |

| PC 端已有功能（`frontend/src`） | 移动端落点 | 主接口 | 交互差异 |
| --- | --- | --- | --- |
| 新建任务 | `pages/home`（默认首页） | `/api/sessions` | 发送即建会话并原地进入详情，新建时带上当前 Chat / Work 模式 |
| 会话列表 | `pages/tasks`（☰ 进入的全屏页） | `/api/sessions`、`/api/scheduled-tasks` | 会话与定时任务混排，带待回复/状态角标 |
| 聊天 / 轨迹 / 澄清 | `pages/chat` | `/api/ws`、`/messages` | 语音输入、附件、停止生成；澄清以人机交互提问卡内嵌在消息流 |
| 定时任务 | `pages/scheduled`（从全部任务页进入） | `/api/scheduled-tasks` | 启停、立即运行、运行记录；待办与通知同页展示 |
| 能力扩展 | 不进入移动端 | `/api/skills/list` | 技能安装涉及文件写入，引导至 PC 端 |
| 产出物 | 任务详情内消息卡 | `/api/files/download` | 不做独立列表，按任务就近下载 |
| 设置 | `pages/me` → `pages/settings` | `/api/settings`、`/model` | 开关 + 只读信息 |
| 待办（PC 暂无独立页） | 任务详情区块 + 「我的」汇总 | 待接入 | 不设独立入口；勾选后写回来源任务 |
| 通知（PC 暂无独立页） | 任务详情区块 + 「我的」汇总 | 待接入 | 不设独立入口；已读状态随任务上下文 |

移动端页面清单与线框见 [`src/index.html`](../src/index.html)。

---

## 9. 跨端适配策略

| 能力 | H5 | 微信小程序 | 支付宝小程序 | App（iOS/Android） |
| --- | --- | --- | --- | --- |
| 请求域名 | 同源或 CORS 白名单 | 需在后台配置 request 合法域名 | 同 | 无限制 |
| WebSocket | `wss://` | 必须 wss + socket 白名单 | 同 | 无限制 |
| 文件下载 | `a[download]` / 新窗口 | `Taro.downloadFile` + `Taro.openDocument` | 同 | `Taro.downloadFile` + `Taro.openDocument`（Capacitor 可直接交给系统） |
| 文件上传 | `Taro.uploadFile` | 同（单次 10 MB 限制需分片） | 同 | 同 |
| 语音输入 | `MediaRecorder` | `Taro.getRecorderManager` + 授权 | 同 | 同 |
| 推送 | 无（站内通知） | 订阅消息（需用户授权） | 模板消息 | APNs / 厂商通道（Capacitor 插件） |
| 安全区 | CSS `env(safe-area-inset-*)` | 胶囊避让 | 胶囊避让 | iPhone 底部横条 |
| 登录增强 | 无 | `Taro.login` 可选绑定 | 支付宝授权可选绑定 | 生物识别 |

实现上统一走 `src/platform/` 出口，页面层不出现 `process.env.TARO_ENV` 判断：

```ts
// src/platform/download.ts
import Taro from '@tarojs/taro'

export function downloadFile(url: string, name: string) {
  if (process.env.TARO_ENV === 'h5') {
    window.open(url, '_blank')
    return
  }
  Taro.downloadFile({
    url,
    success: ({ tempFilePath }) => Taro.openDocument({ filePath: tempFilePath, showMenu: true })
  })
}
```

**样式规范**：使用 `rpx` 做等比缩放，但字体、圆角、边框用 `px`（避免大屏字体过大）；设计令牌与 PC 端保持一致——画布 `#EBEEF2`、卡片 `#FDFBF7`、次级面 `#F2F5FA`、悬停面 `#EAEEF5`、边框 `#D7DDE6`、发丝线 `#E2E6EE`、正文 `#1F232B`、次级文字 `#575B62`、三级文字 `#838C9E`、品牌蓝 `#2C59BC`；仅使用系统字体（PingFang SC / HarmonyOS Sans SC），不使用衬线或等宽体做装饰；间距以 4/8px 为基准，圆角为 6（控件）/8/10/12（卡片）/16/999（胶囊），阴影仅保留 1px 卡片描边与极轻投影，交互反馈只做背景色变化（150ms），不使用渐变、发光、缩放或纹理。

---

## 10. 待办 / 通知接入方案（当前缺口）

现状：后端没有 `/api/todos`、`/api/notifications`；通知只通过 WebSocket `broadcast_to_user` 推送任务事件，没有持久化与已读状态。

### 方案 A（推荐，需后端配合）

| 新增 | 内容 |
| --- | --- |
| 表 | `todos(id, user_id, workspace_id, session_id, title, due_at, status, source, created_at, updated_at)`；`notifications(id, user_id, kind, title, body, route, read_at, created_at)` |
| REST | `GET/POST /api/todos`、`PATCH /api/todos/{id}`、`GET /api/notifications`、`POST /api/notifications/read-all` |
| 事件 | WS 增加 `todo.updated`、`notification.created`，使 PC 与移动端秒级一致 |
| 写入点 | 会话完成、定时任务完成、人机交互节点分别写入待办/通知 |

### 方案 B（不改后端，过渡）

- 待办：把「待回复/待确认」映射为会话 `tool.clarify_request` 与会话列表中的待处理项；
- 通知：从 WS 实时事件在前端 `Taro.setStorageSync` 维护最近 50 条，已读仅本机生效。
- 局限：换端已读不同步、冷启动丢失、无法分页检索。

> 导航上不设「待办」「通知」独立入口：人机交互、待办、通知都收敛在具体任务页面与「我的」汇总中。原型仅呈现交互，正式开发建议采用方案 A。

---

## 11. 安全、性能与可靠性

**安全**
- 全链路 HTTPS/WSS；小程序域名白名单只放行正式域名。
- Token 30 天有效且无 refresh：App 端退出登录必须清空本地缓存、WS 断开；建议后端后续增加短期 access + refresh。
- 日志不打印 token、消息正文；H5 禁用 `dangerouslySetInnerHTML` 直接渲染未净化内容，富文本渲染前做标签白名单过滤。

**性能**
- 消息列表虚拟滚动（长会话 > 200 条启用 `ScrollView` + 分页加载）。
- `chat.delta` 用 30–60ms 合并刷新，避免每个 token 触发一次 setState（小程序 `setData` 是性能瓶颈）。
- 首屏只拉 10 条会话、50 条消息；图片懒加载。

**可靠性**
- 所有写操作带 `client_message_id`，服务端与客户端双向去重。
- 冷启动先 `GET /api/health` 探活，再建 WS；失败进入离线态并提示。
- 定时任务触发为异步：点击后只提示「已触发」，结果通过通知/运行记录呈现。

---

## 12. 实施阶段（不含时间承诺）

| 阶段 | 交付 |
| --- | --- |
| P0 方案与原型 | 本文档 + 可交互原型（已完成，v1.1 导航结构） |
| P1 Taro 工程骨架 | Taro 4 工程（React 18 + TypeScript + Sass）、设计令牌、`app.config.ts` 路由、`Taro.request` 封装、Zustand stores、登录、新对话、全屏「全部任务」页、聊天流式 |
| P2 业务对齐 | 任务详情内人机交互卡、定时任务详情、产出物下载、设置、文件上传下载 |
| P3 多端发布 | 微信/支付宝小程序提审、Android/iOS 打包、H5 上线 |
| P4 待办/通知 | 按方案 A 扩展后端与双端同步 |
| P5 体验优化 | 离线缓存、推送、语音、深色模式 |

**风险**
1. 小程序 WebSocket 域名与证书配置错误 → 上线前用真机验证。
2. 富文本/代码块渲染性能 → 限制高亮语言、长消息折叠。
3. 包体积超限 → 富文本按需引入、图片走 CDN，构建后检查主包。
4. 无 refresh token → 30 天后强制重登，需产品提示。
5. 后端 `PATCH` 无广播 → 双端列表可能短暂不一致，需 `useDidShow` 刷新兜底。
6. 小程序不支持内联 `<svg>` → 所有图标统一走 base64 SVG Data URI + `Icon` 组件（`<Image src="data:image/svg+xml;base64,...">`），禁止在 `tsx` 里直接写 `<svg>`；新增图标需同时补 H5/App 的显示校验。
7. 小程序（webpack5）与 H5（可选 Vite）两条编译链并存 → 仅使用 Taro 官方支持的 API 与 PostCSS 能力，CI 必须两端都跑构建，避免只在 H5 通过。
8. 第三方 UI 库对 Taro 适配不完整 → 坚持自带设计令牌 + Taro 内置组件，不引入重组件库。

---

## 13. 实现说明

移动端实现位于 `frontend-app/`（Taro 4 + React 18 + TS）：`npm run dev:h5` 本地开发，`npm run build:h5` / `npm run build:weapp` 分别产出 `dist/h5`、`dist/weapp`。目录结构与 §7 的 Taro 工程一致。

- 页面共 9 个：新对话（首页）、全部任务、任务详情 · 会话、任务详情 · 定时、我的、我的文件、自动化、设置、登录。
- 数据全部来自真实后端：REST 由 `services/request.ts` 统一附带 Bearer token，登录后建立 `/api/ws` 接收流式事件；仅 `401/403` 清理登录态回登录页，网络异常保留登录态由页面重试。
- 首页与「任务详情 · 会话」共用 `components/ConversationPane`：首页发送后原地展开对话（不跳页），返回回到首页输入态。
- 输入区统一为 `components/Composer`：＋菜单支持添加文件、引用文件、专家、技能；语音为真实流式识别（H5 走 AudioWorklet → `/api/speech/stream`，小程序走 RecorderManager）；Chat / Work 切换位于输入框工具栏。
- 已选技能/专家/引用文件序列化为 `<|kind|value|>` 标记随正文发送，由后端 `extract_mentions` 解析；历史消息展示时还原为 `@标题`。
- 附件在选择后随消息上传（`/api/files/upload`）：图片内联 base64，其他走文件块；草稿、卡片、附件等输入态按会话隔离。
- 停止生成后该轮消息标记「已中断」；后端 replay 对 partial 消息补同样标记，刷新后仍可见。
- 选择的工作目录本地持久化；任务列表按空间分组，搜索前先把分页拉全再过滤。
- 定时任务详情支持启停、立即运行、编辑、删除与运行记录，运行记录可跳回对应会话。
- 「我的文件」按来源会话分组，可查看来源会话、下载/打开文件，打开失败会提示。
- 未接入：注册、找回密码、服务条款/隐私政策（账号由管理员创建）；待办与通知（后端无对应资源，接入方案见 §10）。
