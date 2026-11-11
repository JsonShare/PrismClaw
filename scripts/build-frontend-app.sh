#!/usr/bin/env bash
# 构建移动端 H5 生产产物 → frontend-app/dist/h5（由 nginx 按部署约定的子路径托管）。
# 只做编译，不启动任何进程；启动见 scripts/start.sh、scripts/dev/start-local.sh。
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT/frontend-app"

if [ ! -d "node_modules" ]; then
  echo "📦 安装移动端依赖（首次）..."
  npm install
fi

echo "🔨 构建移动端 H5..."
rm -rf dist/h5
npm run build:h5

echo "✅ 产物：frontend-app/dist/h5"
