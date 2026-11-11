# PrismClaw — multi-stage production Dockerfile.
#
# Stage 1: Build frontend (Node.js)
# Stage 2: Install Python deps into /install prefix
# Stage 3: Runtime (non-root, minimal)
#
# Build:
#   docker build -t prismclaw:latest .
#
# 私有化部署裁剪（只编译启用的域）：
#   docker build --build-arg PRISMCLAW_DOMAINS=medical -t prismclaw:medical .
#   docker build --build-arg PRISMCLAW_DOMAINS=legal -t prismclaw:legal .
#
# Run (via docker-compose):
#   docker compose -f deploy/docker-compose.multi.yml up -d
#
# 仓库三目录布局（backend/ · frontend/ · mcp_servers/）：容器内镜像此布局，
# 使 resolve_project_root() 的 backend/ 前缀 markers 在 dev 与容器中行为一致。

# Pin Python patch version to avoid :slim (mutable) tag drift between builds.
# Bump deliberately after scanning with `docker scout cves`.
ARG PYTHON_VERSION=3.12.7

# ── 域裁剪参数 ──────────────────────────────────────────────────────
# 空值 = 全量编译（读取 scripts/compile/domains.yaml）;
# 逗号分隔 = 仅编译指定域，例如 PRISMCLAW_DOMAINS=medical,legal
ARG PRISMCLAW_DOMAINS=""

# ── Stage 1: Frontend build ───────────────────────────────────────────
FROM node:20-alpine AS frontend-builder
ARG PRISMCLAW_DOMAINS
WORKDIR /app

# 复制配置和生成脚本，根据域裁剪配置生成前端映射
COPY scripts/compile/domains.yaml ./scripts/compile/domains.yaml
COPY scripts/compile/domains-registry.mjs ./scripts/compile/domains-registry.mjs
COPY frontend/subpackages/ ./frontend/subpackages/
RUN PRISMCLAW_DOMAINS=${PRISMCLAW_DOMAINS} node scripts/compile/domains-registry.mjs

# 安装前端依赖并构建（_lazy_map.generated.ts 已在 frontend/src/domains/ 下）
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/src/ ./src/
COPY frontend/public/ ./public/
COPY frontend/index.html frontend/vite.config.ts frontend/tsconfig.json frontend/tsconfig.app.json frontend/tsconfig.node.json ./
RUN npm run build

# ── Stage 2: Python dependency build (main + domain modules) ─────────
FROM python:${PYTHON_VERSION}-slim AS builder
ARG PRISMCLAW_DOMAINS
WORKDIR /build

# 从 Stage 1 复制域裁剪变量（Stage 1 有 Node.js 已执行生成脚本）
COPY --from=frontend-builder /app/scripts/compile/out/.domains.env ./scripts/compile/out/.domains.env

# 安装主包（核心，域包依赖此）。源码稍后复制以利用 Docker 层缓存分离依赖与代码。
COPY backend/pyproject.toml ./backend/pyproject.toml
RUN pip install --no-cache-dir --prefix=/install "./backend[postgres]"

# 复制主包源码（域模块安装依赖此结构：PRISMCLAW_BACKEND_PATHS 形如 backend/src/subpackages/<域>）
COPY backend/src/ ./backend/src/

# 根据 .domains.env 只安装启用的域模块（各域有自己的 pyproject.toml 声明 entry-point + 依赖）
RUN set -a && . scripts/compile/out/.domains.env && set +a && \
    IFS=','; for path in $PRISMCLAW_BACKEND_PATHS; do \
      if [ -d "$path" ]; then \
        echo "  ↳ Installing domain: $(basename $path)"; \
        pip install --no-cache-dir --prefix=/install "$path"; \
      fi; \
    done

# ── Stage 3: Runtime ──────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim
WORKDIR /app

# Runtime system deps: tini for signal forwarding, curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends tini curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps from builder (includes domain modules)
COPY --from=builder /install /usr/local

# Application source — 镜像仓库 backend/ 布局，使 resolve_project_root() markers 一致
COPY backend/src/ ./backend/src/
# 配置不打包进镜像：真实配置含密钥，由 docker-compose 以只读方式挂载到
# /app/backend/configs。镜像内不放 *.yaml，避免密钥随镜像分发。

# Built frontend from Stage 1 — 镜像仓库 frontend/ 布局
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# Non-root runtime user (UID 10001 to avoid clashing with host users)
RUN groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid 10001 --home-dir /app/home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/home /app/workspaces \
    && chown -R appuser:appuser /app
USER appuser
ENV HOME=/app/home
# 主包源码位于 backend/src/，域模块也在此路径下可直接 import
ENV PYTHONPATH=/app/backend/src

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --retries=3 --start-period=20s \
    CMD curl -f http://localhost:8000/health || exit 1

# Run under tini for proper signal handling; bind to 0.0.0.0 inside container.
CMD ["tini", "--", "python", "-m", "uvicorn", \
     "prismclaw.app:create_app", \
     "--factory", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--no-access-log"]
