# PrismClaw 移动端 · 编译与部署流程

> 配套文档：[技术架构说明](./architecture.md) ｜ 工程根目录：`frontend-app/`
> 目标：同一套 Taro 源码（React 18 + TypeScript + Sass）编译出微信小程序、支付宝小程序、H5，App 端复用 H5 产物 + Capacitor 套壳产出 Android、iOS。

---

## 1. 环境准备

| 工具 | 版本建议 | 用途 |
| --- | --- | --- |
| Node.js | 18 LTS / 20 LTS | 运行 Taro CLI 与构建 |
| pnpm | 8+ | 依赖管理（npm 亦可） |
| Taro CLI | 4.x（`@tarojs/cli`） | 工程初始化、编译（小程序端 webpack5） |
| Capacitor CLI | 6.x（`@capacitor/cli`） | App 套壳：生成并同步 iOS/Android 原生工程 |
| 微信开发者工具 | 最新稳定版 | 微信小程序调试与上传 |
| 支付宝小程序开发者工具 | 最新稳定版 | 支付宝小程序调试与上传 |
| Android Studio | 最新稳定版 + JDK 17 | Android 打包（Capacitor 工程） |
| Xcode | 15+ | iOS 打包 / 上架（仅 macOS） |
| 后端服务 | Python 3.12（conda `pyclaw`） | 提供 `/api/*` 与 `/api/ws` |

> 后端本地启动（仓库根目录）：
>
> ```bash
> conda activate pyclaw
> cd backend
> uvicorn prismclaw.app:create_app --factory --reload --port 8000
> ```
>
> 生产端口以 `backend/configs/server.yaml` 的 `server.port`（当前 `8017`）为准；前端开发代理默认指向 `8000`，两者不一致时以实际启动端口为准。

---

## 2. 初始化移动端工程

```bash
cd /path/to/PrismClaw
npx @tarojs/cli@latest init mobile
cd mobile
pnpm install
```

初始化时选择：**React** + **TypeScript** + **Sass** + **Webpack5**（小程序端编译链；H5 需要时可切换到 Vite 编译器）。按架构文档 §7 补充 `src/api`、`src/realtime`、`src/stores`、`src/components`、`src/platform` 等目录。

安装业务依赖（建议版本跟随模板锁文件，以下不写死版本）：

```bash
# 状态管理 + 富文本
pnpm add zustand markdown-it
pnpm add -D sass @types/markdown-it
# 可选：代码高亮（按需注册语言，控制包体积）
pnpm add highlight.js
# App 套壳（仅在需要打包 App 时安装）
pnpm add @capacitor/core
pnpm add -D @capacitor/cli @capacitor/ios @capacitor/android
```

---

## 3. 环境变量与代理

### 3.1 `.env` 约定

Taro 只注入 `TARO_APP_` 前缀的变量，代码中以 `process.env.TARO_APP_*` 读取。

`.env.development`

```dotenv
TARO_APP_API_BASE=http://localhost:8000
TARO_APP_WS_BASE=ws://localhost:8000
```

`.env.production`

```dotenv
TARO_APP_API_BASE=https://prismclaw.example.com
TARO_APP_WS_BASE=wss://prismclaw.example.com
```

> 不要在环境变量中放任何密钥；AppSecret、证书密码只存在于打包机或 CI Secret。

### 3.2 H5 开发代理（`config/index.ts`）

```ts
import type { UserConfigExport } from '@tarojs/cli'

export default {
  projectName: 'prismclaw-taro',
  date: '2026-11-9',
  designWidth: 750,
  sourceRoot: 'src',
  outputRoot: `dist/${process.env.TARO_ENV}`,
  plugins: [],
  defineConstants: {},
  alias: {
    '@': require('path').resolve(__dirname, '..', 'src')
  },
  sass: {
    resource: ['src/styles/tokens.scss']
  },
  mini: {
    postcss: {
      pxtransform: { enable: true, config: {} },
      cssModules: { enable: false }
    }
  },
  h5: {
    publicPath: '/',
    staticDirectory: 'static',
    router: { mode: 'browser' },
    devServer: {
      port: 5174,
      proxy: {
        '/api': {
          target: 'http://localhost:8000',
          changeOrigin: true,
          ws: true // 同时代理 /api/ws 的 WebSocket 升级
        }
      }
    }
  }
} satisfies UserConfigExport
```

H5 开发时把 `TARO_APP_API_BASE` 留空即可走同源代理；真机/小程序必须填完整域名。

---

## 4. AppID 与 Taro 配置

Taro 没有统一的应用清单文件，各端 AppID 分别落在对应的项目配置文件里。

`project.config.json`（微信小程序）

```json
{
  "miniprogramRoot": "dist/weapp/",
  "projectname": "prismclaw-taro",
  "appid": "wxXXXXXXXXXXXXXXXX",
  "setting": {
    "urlCheck": true,
    "es6": true,
    "enhance": true,
    "minified": true
  },
  "compileType": "miniprogram"
}
```

`project.alipay.json`（支付宝小程序）

```json
{
  "miniprogramRoot": "dist/alipay/",
  "projectname": "prismclaw-taro",
  "appid": "2021XXXXXXXXXXXX",
  "compileOptions": { "component2": true }
}
```

`capacitor.config.ts`（App 套壳，webDir 指向 H5 产物）

```ts
import type { CapacitorConfig } from '@capacitor/cli'

const config: CapacitorConfig = {
  appId: 'com.prismclaw.mobile',
  appName: 'PrismClaw',
  webDir: 'dist/h5',
  server: { androidScheme: 'https' }
}

export default config
```

- `appid`（微信 / 支付宝）：对应平台创建的小程序 AppID。
- `appId`（Capacitor）：Android 包名 / iOS Bundle ID 的基准，发布前不可随意变更。
- **正式域名必须配置**：
  - 微信公众平台 → 开发管理 → 服务器域名：request、socket、uploadFile、downloadFile 均加入 `https://prismclaw.example.com` / `wss://prismclaw.example.com`。
  - 支付宝开放平台 → 开发设置 → 服务器域名白名单。
- 小程序安全要求 HTTPS + 已备案域名 + 有效证书；`ws://` 与自签证书无法上线。

---

## 5. 开发调试

| 端 | 命令 | 说明 |
| --- | --- | --- |
| H5 | `pnpm dev:h5` | 浏览器访问 `http://localhost:5174`，支持热更新 |
| 微信小程序 | `pnpm dev:weapp` | 用微信开发者工具导入 `dist/weapp` |
| 支付宝小程序 | `pnpm dev:alipay` | 用支付宝小程序 IDE 导入 `dist/alipay` |
| App | `pnpm build:h5 && npx cap sync && npx cap run ios`（或 `android`） | 首次需先执行 `npx cap add ios` / `npx cap add android`；Capacitor 拉起原生模拟器/真机调试 |

联调检查顺序：

1. `GET /api/health` 返回 `status: ok`；
2. `POST /api/auth/token` 能拿到 JWT；
3. 浏览器/开发者工具 Network 中 `wss://.../api/ws` 建立成功，收到 `hello` → `ready`；
4. 发送消息后收到 `chat.start` / `chat.delta` / `chat.done`。

> 微信开发者工具可在「详情 → 本地设置」勾选「不校验合法域名」用于本地联调，**上线前必须取消**。

---

## 6. 编译命令

```bash
# H5
pnpm build:h5            # 产物：dist/h5

# 小程序
pnpm build:weapp         # 产物：dist/weapp
pnpm build:alipay        # 产物：dist/alipay

# App：先出 H5，再同步进 Capacitor 原生工程
pnpm build:h5 && npx cap sync
```

若模板脚本名不同，使用等价的 Taro CLI 命令：

```bash
npx taro build --type h5
npx taro build --type weapp
npx taro build --type alipay
```

> 输出目录由 `config/index.ts` 的 `outputRoot: dist/${process.env.TARO_ENV}` 控制，所以小程序与 H5 产物不会互相覆盖。

构建后自检：

- `dist/weapp` 主包体积 ≤ 2 MB（开发者工具「代码依赖分析」查看）；
- 搜索产物中是否残留 `localhost` / `ws://`；
- H5 的 `index.html` 资源路径为相对或 CDN 正确路径；
- `capacitor.config.ts` 的 `webDir` 与实际 H5 产物目录一致。

---

## 7. 各端打包与发布

### 7.1 微信小程序

1. `pnpm build:weapp`；
2. 微信开发者工具导入 `dist/weapp`（或直接打开 `mobile/`，由 `project.config.json` 的 `miniprogramRoot` 指向产物）；
3. 真机预览，验证登录、流式消息、文件下载、订阅消息；
4. 「上传」→ 微信公众平台提交审核 → 发布。

```bash
# 可选：CI 中使用 miniprogram-ci 自动上传
npx miniprogram-ci upload \
  --pp ./dist/weapp \
  --pkp ./private.wxXXXX.key \
  --appid wxXXXXXXXXXXXXXXXX \
  --uv 0.9.2 \
  --ud "PrismClaw 移动端"
```

注意事项：
- 语音需 `scope.record` 授权，首次使用前弹窗说明用途；
- 文件下载需在 `downloadFile` 白名单内，且 `Taro.openDocument` 仅支持有限格式；
- 小程序单次 `Taro.uploadFile` 上限 10 MB，大文件需分片或引导到 H5/PC；
- 图标一律走 `Icon` 组件（base64 SVG Data URI），小程序不支持内联 `<svg>`。

### 7.2 支付宝小程序

1. `pnpm build:alipay`；
2. 支付宝小程序 IDE 导入 `dist/alipay`，配置 AppID；
3. 真机预览 → 上传 → 开放平台提审发布。

差异点：胶囊按钮、授权弹窗文案、`Taro.downloadFile` 的保存行为与微信略有不同，已在 `src/platform/` 层统一封装。

### 7.3 Android App（Taro H5 + Capacitor）

前置：`capacitor.config.ts` 中 `webDir: 'dist/h5'`、`appId: 'com.prismclaw.mobile'`。

1. `pnpm build:h5`；
2. 首次执行 `npx cap add android`，在 `mobile/` 下生成 `android/` 原生工程；
3. `npx cap sync android` 把 H5 产物与 Capacitor 插件同步进原生工程；
4. `npx cap open android` 用 Android Studio 打开；
5. 配置签名（`.keystore`）与 `minSdk/targetSdk`、`applicationId`；
6. `./gradlew assembleRelease` 产出 APK，`bundleRelease` 产出 AAB；
7. 内测分发（蒲公英/企业分发）或上架应用商店。

签名证书生成：

```bash
keytool -genkeypair -v \
  -keystore prismclaw.keystore \
  -alias prismclaw \
  -keyalg RSA -keysize 2048 -validity 36500
```

> 不建议走 Taro RN 打包 App：Taro 的 RN 输出仍属实验路径，本项目统一用 H5 产物 + Capacitor 套壳。

### 7.4 iOS App（Taro H5 + Capacitor）

1. `pnpm build:h5`；
2. 首次执行 `npx cap add ios` 生成 `ios/` 工程，`npx cap sync ios` 同步产物；
3. `npx cap open ios` 用 Xcode 打开，配置 Team、Bundle ID（`com.prismclaw.mobile`）、签名证书与 `.mobileprovision` 描述文件；
4. Product → Archive 导出 `.ipa`；
5. 使用 Transporter 上传 App Store Connect → TestFlight 内测 → 提交审核。

要求：
- Apple 开发者账号（$99/年）；
- Bundle ID 与描述文件一致；
- 若使用推送，需在 Apple Developer 配置 APNs Key，并接入 Capacitor Push Notifications 插件；
- 隐私清单（Privacy Manifest）与权限用途描述必须齐全，否则审核被拒。

### 7.5 H5 部署

1. `pnpm build:h5`，产物在 `dist/h5`；
2. 上传到 Nginx 静态目录（可与 PC 端同域，如 `https://prismclaw.example.com/m/`）；
3. Nginx 配置（仓库已有 `deploy/nginx.conf` 的 `/api/`、`/api/ws` 转发，可直接复用）：

```nginx
# 移动端 H5（history 路由）
location /m/ {
    alias /usr/share/nginx/html/mobile/;
    try_files $uri $uri/ /m/index.html;
    add_header Cache-Control "no-cache";
}

# 静态资源长缓存（Taro H5 的 staticDirectory 产物）
location /m/static/ {
    alias /usr/share/nginx/html/mobile/static/;
    expires 30d;
    add_header Cache-Control "public, immutable";
}

# API 与 WebSocket（若与 PC 不同域，需在移动端站点下同样配置）
location /api/ws {
    proxy_pass http://prismclaw;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
}

location /api/ {
    proxy_pass http://prismclaw;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

4. 配置 HTTPS（证书 + 强制跳转），并确保 `TARO_APP_WS_BASE` 为 `wss://`；
5. 若移动端 H5 与 API 不同域，需在 FastAPI 的 CORS 白名单中加入移动端域名（`backend/configs/server.yaml` 的 `cors_origins`）。

---

## 8. CI/CD 建议

```yaml
# .github/workflows/mobile.yml（示例）
name: mobile
on:
  push:
    tags: ['mobile-v*']

jobs:
  build:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        target: [h5, weapp, alipay]
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
        with: { version: 8 }
      - uses: actions/setup-node@v4
        with: { node-version: 20, cache: pnpm, cache-dependency-path: mobile/pnpm-lock.yaml }
      - run: pnpm install --frozen-lockfile
        working-directory: mobile
      - run: pnpm build:${{ matrix.target }}
        working-directory: mobile
        env:
          TARO_APP_API_BASE: https://prismclaw.example.com
          TARO_APP_WS_BASE: wss://prismclaw.example.com
      - uses: actions/upload-artifact@v4
        with:
          name: mobile-${{ matrix.target }}
          path: mobile/dist/${{ matrix.target }}
```

- Android/iOS 建议单独流水线：先 `pnpm build:h5` 再 `npx cap sync`，macOS runner 做 iOS 签名，证书与描述文件放 Secrets；
- 小程序上传用 `miniprogram-ci`，密钥只放在 CI Secret；
- 发布前自动检查产物中是否包含 `localhost`、`ws://`、测试 AppID；
- 小程序与 H5 是两条编译链（webpack5 / 可选 Vite），CI 必须全部跑过。

---

## 9. 上线验收清单

**功能**
- [ ] 账号密码登录成功，token 正确存储，401 自动回登录页
- [ ] 会话列表、历史消息与 PC 端一致
- [ ] 发送消息可收到流式回复，停止生成生效
- [ ] 澄清/人机交互回复可回传（`tool.clarify`）
- [ ] 定时任务可查看、启停、立即触发，运行结果可见
- [ ] 产出物可下载/打开
- [ ] 断网重连后运行态与消息能补齐

**多端**
- [ ] H5、微信、支付宝、Android、iOS 五端均可登录与收发消息
- [ ] 小程序 request/socket/upload/download 域名白名单已配置
- [ ] iPhone 底部安全区、小程序胶囊无遮挡
- [ ] 语音授权拒绝后有降级提示

**工程**
- [ ] 主包体积达标，无打包警告
- [ ] 产物无 `localhost` / `ws://` / 测试 AppID
- [ ] 隐私政策、权限说明、用户协议齐全
- [ ] 版本号、图标、启动页、应用名称正确

---

## 10. 常见问题

| 现象 | 排查 |
| --- | --- |
| 小程序请求失败 | 未配置 request 合法域名；证书链不完整；`urlCheck` 未关闭仅限开发 |
| WebSocket 连不上 | 未使用 `wss`；socket 域名未加白名单；Nginx 未透传 `Upgrade/Connection`；`proxy_read_timeout` 过短 |
| 连接 30 秒后断开 | 未在首帧发送 `identify`，或未回 `pong` 心跳 |
| 收到 `close(4003)` | JWT 过期或无效，需重新登录（无 refresh token） |
| `chat.delta` 不显示 | `segmentId` 变化时未重置当前段缓冲；或未按 `conversation_id` 路由事件 |
| 下载文件打不开 | 小程序需 `Taro.downloadFile` + `Taro.openDocument`，且格式受支持；大文件改 H5/PC |
| H5 刷新 404 | Nginx 未配置 `try_files ... /index.html`（history 路由） |
| CORS 报错 | 后端 `cors_origins` 未加入移动端 H5 域名 |
| 主包超 2 MB | 未分包；富文本/高亮库全量引入；图片未走 CDN |
| iOS 审核被拒 | 缺少隐私清单、权限用途说明，或使用了未声明的能力 |

---

## 11. 与现有仓库的关系

| 目录 | 作用 | 是否改动 |
| --- | --- | --- |
| `backend/` | FastAPI 服务，提供全部接口与 WebSocket | 本期不改（待办/通知按方案 A 时再扩展） |
| `frontend/` | PC Web（React 19 + Vite） | 不改 |
| `frontend-app/` | 本方案的可运行设计原型（Taro 工程根目录，含小程序 / H5 / App 套壳） | 已交付 |
| `frontend-app/docs/` | 架构与编译部署文档 | 已交付 |

复用原则：移动端与 PC 端只通过 `/api/*` 与 `/api/ws` 通信，不共享前端代码、不复制业务规则，保证两端行为一致且可独立发布。
