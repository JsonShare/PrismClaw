#!/usr/bin/env bash
# 本地测试启动：后端 + 本地静态托管（scripts/dev/static-server.mjs 模拟 nginx），
# 直接访问已构建好的前端产物。本脚本不编译任何前端——先跑构建脚本：
#   ./scripts/build-frontend.sh      → frontend/dist       (:5173)
#   ./scripts/build-frontend-app.sh  → frontend-app/dist/h5 (:10086)
# 日志与 PID 在 logs/app/（日志按日期 + 大小滚动，见 backend/infra/logging_setup.py 与 scripts/dev/logger.mjs），
# 停止用 ./scripts/dev/stop-local.sh
set -euo pipefail

source "$(dirname "$0")/../lib/common.sh"

PC_DIST="$PROJECT_ROOT/frontend/dist"
MOBILE_DIST="$PROJECT_ROOT/frontend-app/dist/h5"

missing=0
if [ ! -f "$PC_DIST/index.html" ]; then
  echo "✗ 缺少 PC 端产物，请先运行：./scripts/build-frontend.sh"
  missing=1
fi
if [ ! -f "$MOBILE_DIST/index.html" ]; then
  echo "✗ 缺少移动端产物，请先运行：./scripts/build-frontend-app.sh"
  missing=1
fi
[ "$missing" -eq 0 ] || exit 1

if ! command -v node >/dev/null 2>&1; then
  echo "✗ 未找到 node，无法启动静态托管"
  exit 1
fi

# 先停掉上一次启动的进程，避免旧进程占端口
"$PROJECT_ROOT/scripts/dev/stop-local.sh" --quiet || true
BACKEND_LOG="$RUN_DIR/log.log"
PC_LOG="$RUN_DIR/frontend.log"
MOBILE_LOG="$RUN_DIR/frontend-app.log"
# 后端日志由 logging_setup 落盘（log.log / error.log），关掉控制台副本避免重复行；
# stdout/stderr 仍重定向到 log.log，用于兜住 logging 安装前的启动异常。
export PRISMCLAW_LOG_CONSOLE=off

for log in "$BACKEND_LOG" "$PC_LOG" "$MOBILE_LOG"; do
  : >"$log"
done

echo "🚀 启动后端 (uvicorn :${BACKEND_PORT})..."
backend_pid="$(spawn_detached "$BACKEND_LOG" \
  "$PYTHON_BIN" -m uvicorn prismclaw.app:create_app --factory \
  --host "$BACKEND_HOST" --port "$BACKEND_PORT")"
echo "$backend_pid" >"$RUN_DIR/backend.pid"

# 本地没有 nginx：用静态服务器托管产物，并把 /api、/v1 反代到后端
start_static() {
  local name="$1" dir="$2" port="$3" log="$4" pid
  echo "🧱 托管 ${name} 静态产物 (:${port})..."
  pid="$(spawn_detached "$log" \
    node "$PROJECT_ROOT/scripts/dev/static-server.mjs" \
    --dir "$dir" --port "$port" --api "http://127.0.0.1:${BACKEND_PORT}" --log "$log")"
  echo "$pid" >"$RUN_DIR/${name}.pid"
}

start_static pc "$PC_DIST" "$PC_PORT" "$PC_LOG"
start_static mobile "$MOBILE_DIST" "$MOBILE_PORT" "$MOBILE_LOG"

echo ""
wait_port "$BACKEND_PORT" "后端 API" "$BACKEND_LOG"
wait_port "$PC_PORT" "PC 端" "$PC_LOG"
wait_port "$MOBILE_PORT" "移动端" "$MOBILE_LOG"

LAN_IP="$(lan_ip)"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  PrismClaw 本地测试环境已启动（nginx 由静态服务模拟）"
echo ""
echo "  PC 端       http://localhost:${PC_PORT}"
[ -n "$LAN_IP" ] && echo "              http://${LAN_IP}:${PC_PORT}"
echo "  移动端      http://localhost:${MOBILE_PORT}"
[ -n "$LAN_IP" ] && echo "              http://${LAN_IP}:${MOBILE_PORT}"
echo "  API    http://localhost:${BACKEND_PORT}"
echo ""
echo "  日志        logs/app/{log,error,frontend,frontend-app}.log"
echo "  停止        ./scripts/dev/stop-local.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
