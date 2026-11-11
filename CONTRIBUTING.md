# 贡献指南

感谢参与 PrismClaw。提交代码前请先阅读本指南与 [`docs/architecture/`](./docs/architecture/)。

## 开发环境

- Python 3.12+、[uv](https://docs.astral.sh/uv/)
- Node.js 18+
- Redis 7、PostgreSQL 16+（含 pgvector）

```bash
# 后端依赖
cd backend && uv sync && cd ..

# 本地配置（真实配置不入库）
cd backend/configs
for f in *.example.yaml; do cp "$f" "${f%.example.yaml}.yaml"; done
cd ../..

# 前端依赖 + 本地启动
./scripts/build-frontend.sh
./scripts/build-frontend-app.sh
./scripts/dev/start-local.sh
```

## 目录约定

| 目录 | 说明 |
|------|------|
| `backend/src/prismclaw/schema` | L0 契约层，零依赖 |
| `backend/src/prismclaw/engine` | L1 业务逻辑（无状态） |
| `backend/src/prismclaw/runtime` | L2 运行时门面与服务 |
| `backend/src/prismclaw/assembly` | L3 DI 与 Bundle 组装 |
| `backend/src/prismclaw/app` | L4 HTTP / WebSocket 入口 |
| `frontend/` | PC 端 SPA（Vite + React） |
| `frontend-app/` | 移动端 H5（Taro） |
| `deploy/` | 容器编排与部署文档 |

依赖方向严格单向：`schema → engine → runtime → assembly → app`，`app` 不得直连 `engine`。

## 提交前检查

```bash
make test                      # 后端单测
cd frontend && npm run build   # PC 端类型检查 + 构建
cd frontend-app && npm run build:h5
```

- 不要提交 `node_modules/`、`dist/`、`logs/`、`__pycache__/` 等产物。
- 配置只提交 `*.example.yaml` 模板，禁止提交真实密钥、内部 IP 或账号密码。
- 新增配置项时，同步更新对应的示例文件与 README / 部署文档。

## 提交信息与 PR

- 提交信息说明「为什么改」，中英文均可；一个提交只做一件事。
- PR 请描述变更动机、影响范围与验证方式，并附上相关 Issue。
- 涉及安全边界、权限、沙箱、密钥处理的改动，请在 PR 中显式说明。

## 安全问题

请勿通过公开 Issue 报告漏洞，流程见 [`SECURITY.md`](./SECURITY.md)。
