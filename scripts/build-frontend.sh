#!/usr/bin/env bash
# 构建 PC 端生产产物 → frontend/dist（由 nginx 托管在站点根路径 /）。
# 只做编译，不启动任何进程；启动见 scripts/start.sh、scripts/dev/start-local.sh。
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ── 生成域分包注册表（scripts/compile/domains.yaml 是唯一真实来源） ──
if command -v node >/dev/null 2>&1 && [ -f "$PROJECT_ROOT/scripts/compile/domains.yaml" ]; then
  if [ -n "${PRISMCLAW_DOMAINS:-}" ]; then
    echo "⚠️  PRISMCLAW_DOMAINS='${PRISMCLAW_DOMAINS}' 已设置，domains.yaml 的 enabled 标记将被覆盖"
  fi
  echo "🔧 生成域注册表（domains.yaml → 前端懒加载映射 / 后端 manifest / .domains.env）..."
  PRISMCLAW_DOMAINS="${PRISMCLAW_DOMAINS:-}" node "$PROJECT_ROOT/scripts/compile/domains-registry.mjs"
fi

cd "$PROJECT_ROOT/frontend"

if [ -d "subpackages" ]; then
  domain_dirs="$(find subpackages -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')"
  echo "📦 业务域分包 ${domain_dirs} 个：$(ls -1 subpackages/ 2>/dev/null | tr '\n' ' ')"
fi

if [ ! -d "node_modules" ]; then
  echo "📦 安装 PC 端依赖（首次）..."
  npm install
fi

echo "🔨 构建 PC 端..."
rm -rf dist
npm run build

echo "✅ 产物：frontend/dist（$(ls -1 dist/assets 2>/dev/null | wc -l | tr -d ' ') 个 assets 文件）"
