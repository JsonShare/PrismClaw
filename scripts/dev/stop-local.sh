#!/usr/bin/env bash
# 停止 scripts/dev/start-local.sh 启动的本地进程（后端 + PC 端 / 移动端静态托管）。
# 生产后端由 systemd / docker 托管，不走本脚本。
# 用法：./scripts/dev/stop-local.sh [--quiet] [端口...]
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJECT_ROOT"

RUN_DIR="$PROJECT_ROOT/logs/app"
QUIET=false
PORTS=()

for arg in "$@"; do
  case "$arg" in
    -q|--quiet) QUIET=true ;;
    *) PORTS+=("$arg") ;;
  esac
done

log() {
  [ "$QUIET" = true ] || echo "$@"
}

if ! command -v lsof &>/dev/null; then
  echo "lsof not found; cannot stop PrismClaw processes"
  exit 1
fi

# 先按 PID 文件优雅终止（SIGTERM），避免子进程立刻被强杀
stop_pid_file() {
  local name="$1"
  local pid_file="$RUN_DIR/${name}.pid"
  [ -f "$pid_file" ] || return 0
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    log "Stopping ${name} (pid ${pid})"
    kill "$pid" 2>/dev/null || true
    sleep 1
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$pid_file"
}

kill_port() {
  local port="$1"
  local pids
  pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"

  if [ -z "$pids" ]; then
    log "No process found on port ${port}"
    return 0
  fi

  log "Killing processes on port ${port}: ${pids}"
  kill -9 $pids 2>/dev/null || true
  log "Stopped port ${port}"
}

stop_pid_file backend
stop_pid_file pc
stop_pid_file mobile

# 默认端口：后端 / PC 端 / 移动端 / 旧版后端(9000)
if [ ${#PORTS[@]} -eq 0 ]; then
  PORTS=(
    "${PRISMCLAW_PORT:-8000}"
    "${PC_PORT:-5173}"
    "${MOBILE_PORT:-10086}"
    9000
  )
fi

for port in "${PORTS[@]}"; do
  kill_port "$port"
done

log "全部停止完成"

# ── 清理残留 MCP 进程（stdio 子进程随 backend 退出，此处清理孤儿） ──
cleanup_mcp_servers() {
  local mcp_root="$PROJECT_ROOT/mcp_servers"
  [ -d "$mcp_root" ] || return 0
  for dir in "$mcp_root"/*/; do
    [ -d "${dir}.venv" ] || continue
    local main_file
    main_file=$(find "${dir}src" -name "__main__.py" -maxdepth 3 2>/dev/null | head -1)
    [ -n "$main_file" ] || continue
    local module
    module=$(echo "$main_file" | sed "s|${dir}src/||" | sed 's|/|.|g' | sed 's|\.__main__\.py||')
    if [ -f "${dir}.pid" ]; then
      local pid
      pid=$(cat "${dir}.pid" 2>/dev/null || true)
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        log "Killing stale MCP process ${pid} ($(basename "$dir"))"
        kill -9 "$pid" 2>/dev/null || true
      fi
      rm -f "${dir}.pid"
    fi
    local pids
    pids=$(pgrep -f "python -m ${module}" 2>/dev/null || true)
    if [ -n "$pids" ]; then
      log "Killing stale MCP processes: ${pids}"
      echo "$pids" | xargs kill -9 2>/dev/null || true
    fi
  done
}

cleanup_mcp_servers
