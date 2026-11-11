#!/usr/bin/env bash
# 生产启动：只启动后端 API（前台运行，信号交给 systemd / docker / supervisor）。
# 前端静态资源由 nginx 独立托管（PC 端与移动端 H5 分开路径），/api 与 /v1
# 反代到本服务，因此生产环境不需要启动或编译前端。
# 前端产物用 build-frontend.sh / build-frontend-app.sh 单独构建。
set -euo pipefail

source "$(dirname "$0")/lib/common.sh"

echo "🚀 后端 API：${BACKEND_HOST}:${BACKEND_PORT}（前端由 nginx 托管）"
exec "$PYTHON_BIN" -m uvicorn prismclaw.app:create_app \
  --factory \
  --host "$BACKEND_HOST" \
  --port "$BACKEND_PORT"
