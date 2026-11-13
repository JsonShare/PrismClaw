#!/usr/bin/env bash
# 供 scripts/*.sh `source` 的公共环境（不设置 set -euo pipefail，由各脚本自行声明）。
# 统一项目根目录、端口、日志/PID 目录与后台进程工具。
# 约定：调用方先激活项目运行环境，脚本只使用当前 PATH 中的 python。

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"

BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
BACKEND_PORT="${PRISMCLAW_PORT:-8000}"
PC_PORT="${PC_PORT:-5173}"
MOBILE_PORT="${MOBILE_PORT:-10086}"

RUN_DIR="$PROJECT_ROOT/logs/app"
mkdir -p "$RUN_DIR"

# 后端 import 根（域分包直接 import subpackages.<domain>）
export PYTHONPATH="$PROJECT_ROOT/backend/src${PYTHONPATH:+:$PYTHONPATH}"
export LITELLM_LOCAL_MODEL_COST_MAP=True
export LITELLM_LOG=ERROR

# bwrap 沙箱：仅 Linux 有；macOS 本地关闭强制
if command -v bwrap >/dev/null 2>&1; then
  export PRISMCLAW_SANDBOX_ENFORCE=true
else
  export PRISMCLAW_SANDBOX_ENFORCE=false
fi

# 使用调用方已激活环境中的 python，不做任何环境探测或回退。
# hash -r：避免激活环境后仍命中激活前缓存的 python 路径。
hash -r 2>/dev/null || true
PYTHON_BIN="$(command -v python || command -v python3 || true)"
if [ -z "$PYTHON_BIN" ]; then
  echo "✗ 未找到 python，请先激活项目运行环境" >&2
  exit 1
fi
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
  echo "✗ 当前 python 不满足 >=3.12：${PYTHON_BIN}（$("$PYTHON_BIN" -V 2>&1)）" >&2
  echo "  请先激活项目运行环境（如：conda activate pyclaw）后重跑" >&2
  exit 1
fi

# 以独立会话启动，避免进程随启动它的终端退出而被清理。
# 用追加模式：日志框架会原地截断滚动，O_APPEND 保证崩溃输出仍落在当前文件里。
spawn_detached() {
  local log="$1"
  shift
  nohup "$PYTHON_BIN" -c '
import os, sys
try:
    os.setsid()
except OSError:
    pass
os.execvp(sys.argv[1], sys.argv[1:])
' "$@" >>"$log" 2>&1 &
  echo $!
}

wait_port() {
  local port="$1" name="$2" log="$3" i
  for i in $(seq 1 90); do
    if lsof -tiTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      echo "  ✓ ${name} 已就绪 (端口 ${port})"
      return 0
    fi
    sleep 1
  done
  echo "  ✗ ${name} 启动超时，请查看 ${log}"
  return 0
}

lan_ip() {
  ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true
}
