# PrismClaw 构建与部署快捷命令
# 运行 `make help` 查看所有可用命令

.PHONY: help \
        dev-cloud build-cloud run-cloud stop-cloud \
        docker-build docker-run docker-stop docker-logs \
        mcp-dev mcp-test mcp-http \
        test test-baseline test-pg

help:
	@echo "PrismClaw 部署管理命令:"
	@echo ""
	@echo "  ================ 云端 Web 服务部署 (B/S) ================"
	@echo "    make dev-cloud         本地开发: 启动 Vite 和 Uvicorn"
	@echo "    make build-cloud       构建前端静态资源到 frontend/dist (同 scripts/build-frontend.sh)"
	@echo "    make run-cloud         启动后端 API (同 scripts/start.sh)"
	@echo "    make stop-cloud        停止后端 API (同 scripts/stop.sh)"
	@echo "    make docker-build      构建 Docker 镜像 (project=prismclaw)"
	@echo "    make docker-run        启动全栈容器 (postgres + redis + app)"
	@echo "    make docker-stop       停止并移除容器"
	@echo "    make docker-logs       跟踪应用日志"
	@echo ""
	@echo "  ================ web-search-mcp ================"
	@echo "    make mcp-test          运行 web-search-mcp 单测"
	@echo "    make mcp-dev           以 stdio 模式启动 (引擎默认接入方式)"
	@echo "    make mcp-http          以 streamable-http 模式本地启动 (:9002)"
	@echo ""
	@echo "  ================ 测试 ================"
	@echo "    make test              全量单测(不含真实 PG 冒烟)"
	@echo "    make test-baseline     重构基线测试(Phase 0 门禁)"
	@echo "    make test-pg           真实 PG 冒烟(W2 回归: DDL+链路, 需 docker PG)"
	@echo ""
	@echo "  Nginx 由宿主机独立部署，参考配置见 deploy/nginx.conf"

# ---------- 测试 ----------

test:
	cd backend && python -m pytest tests/ -q --ignore=tests/test_plugin_loading_run.py --ignore=tests/test_plugin_loading.py --ignore=tests/test_domains.py --ignore=tests/test_pg_smoke.py

# Phase 0 门禁：重构基线测试，不修改生产代码。
# 进入 Phase 1 前必须 green。
test-baseline:
	cd backend && python -m pytest tests/refactor/ -v --tb=short

# W2 回归: 记忆表 DDL 对真实 PG 执行 + store/search/归因/仲裁一条链。
# 依赖: docker compose 的 postgres 已运行; 自动创建/复用 prismclaw_test 库。
test-pg:
	@command -v docker >/dev/null 2>&1 || { echo "✗ 需要 docker (运行 prismclaw-postgres-1)"; exit 1; }
	@docker exec prismclaw-postgres-1 psql -U postgres -tc "SELECT 1 FROM pg_database WHERE datname='prismclaw_test'" | grep -q 1 || \
		docker exec prismclaw-postgres-1 psql -U postgres -c "CREATE DATABASE prismclaw_test"
	PRISMCLAW_TEST_PG_DSN='postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/prismclaw_test' \
		bash -c 'cd backend && python -m pytest tests/test_pg_smoke.py -v'

# ---------- web-search-mcp (独立 MCP 服务) ----------

MCP_DIR := mcp_servers/web-search-mcp

mcp-test:
	cd $(MCP_DIR) && .venv/bin/python -m pytest -q

# stdio 模式由引擎通过 backend/configs/mcp.yaml 自动拉起；此 target 仅用于
# 手工冒烟（JSON-RPC 经 stdin/stdout）。
mcp-dev:
	cd $(MCP_DIR) && .venv/bin/python -m web_search_mcp --transport stdio

# 需要 WSM_TOKEN，例如: WSM_TOKEN=dev-token make mcp-http
mcp-http:
	@if [ -z "$${WSM_TOKEN:-}" ]; then echo "✗ 请先设置 WSM_TOKEN"; exit 1; fi
	cd $(MCP_DIR) && .venv/bin/python -m web_search_mcp --transport http --host 0.0.0.0 --port 9002

# ---------- 云端 Web 部署 (FastAPI + React) ----------

dev-cloud:
	@echo "提示：请在两个终端分别运行:"
	@echo "  终端 1: cd backend && uvicorn prismclaw.app:create_app --factory --reload --port 8000"
	@echo "  终端 2: cd frontend && npm run dev"

build-cloud:
	@echo "→ 正在生成域分包注册表（根据 scripts/compile/domains.yaml 裁剪）..."
	node scripts/compile/domains-registry.mjs
	@echo "→ 正在安装后端业务域模块（仅启用的域）..."
	@set -a && . scripts/compile/out/.domains.env && set +a && \
		IFS=','; for path in $$PRISMCLAW_BACKEND_PATHS; do \
			if [ -f "$$path/pyproject.toml" ]; then \
				echo "  ↳ $$(basename $$path)"; \
				pip install -e "$$path" -q 2>&1 || echo "  ⚠ Failed: $$path"; \
			fi; \
		done
	@./scripts/build-frontend.sh

run-cloud:
	@./scripts/start.sh

stop-cloud:
	@./scripts/stop.sh

docker-build:
	@echo "→ 正在构建 Docker 镜像 (project=prismclaw)..."
	docker compose -f deploy/docker-compose.yml build

docker-run:
	@echo "→ 正在启动 Docker 容器 (project=prismclaw, 端口 8000)..."
	docker compose -f deploy/docker-compose.yml up -d

docker-stop:
	@echo "→ 正在停止 Docker 容器 (project=prismclaw)..."
	docker compose -f deploy/docker-compose.yml down

docker-logs:
	@echo "→ 跟踪日志 (Ctrl+C 退出)..."
	docker compose -f deploy/docker-compose.yml logs -f prismclaw
