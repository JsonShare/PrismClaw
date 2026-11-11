#!/usr/bin/env bash
# 本地开发：PC 端 Vite 开发服务器（:5173，热更新），/api 代理到 :8000。
# 需另起后端；查看已构建产物用 scripts/dev/start-local.sh。
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJECT_ROOT/frontend"

# 确保依赖安装完整（新增/变更包时自动补装）
if [ ! -d "node_modules" ]; then
  echo "📦 Installing frontend dependencies..."
  npm install
else
  npm install --prefer-offline 2>/dev/null || npm install
fi

echo "🌐 Starting Vite dev server on http://localhost:5173"
echo "   (proxies API requests to http://localhost:8000)"
exec npm run dev
