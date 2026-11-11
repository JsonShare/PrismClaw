#!/usr/bin/env bash
# 停止 scripts/start.sh 启动的后端 API：先 SIGTERM 优雅退出，超时再 SIGKILL。
# systemd / docker 托管的后端请用各自的停止命令，不要用本脚本。
# 用法：./scripts/stop.sh [--quiet] [--force] [端口...]
set -euo pipefail

source "$(dirname "$0")/lib/common.sh"

QUIET=false
FORCE=false
PORTS=()

for arg in "$@"; do
  case "$arg" in
    -q|--quiet) QUIET=true ;;
    -f|--force) FORCE=true ;;
    *) PORTS+=("$arg") ;;
  esac
done
[ ${#PORTS[@]} -gt 0 ] || PORTS=("$BACKEND_PORT")

log() {
  [ "$QUIET" = true ] || echo "$@"
}

if ! command -v lsof >/dev/null 2>&1; then
  echo "✗ 未找到 lsof，无法定位监听进程"
  exit 1
fi

stop_port() {
  local port="$1" pids pid cmd i
  pids="$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)"
  if [ -z "$pids" ]; then
    log "端口 ${port} 无监听进程"
    return 0
  fi
  for pid in $pids; do
    cmd="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    # 端口可能被其他服务占用：默认只停 PrismClaw 后端，避免误杀
    if [ "$FORCE" != true ] && [[ "$cmd" != *prismclaw.app:create_app* ]]; then
      log "跳过端口 ${port} 上的非 PrismClaw 进程 ${pid}（确需停止请加 --force）"
      continue
    fi
    log "停止后端 (pid ${pid}, 端口 ${port})"
    kill -TERM "$pid" 2>/dev/null || true
    for i in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    kill -9 "$pid" 2>/dev/null || true
  done
}

for port in "${PORTS[@]}"; do
  stop_port "$port"
done

log "后端已停止"
