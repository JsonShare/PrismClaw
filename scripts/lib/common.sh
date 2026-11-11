#!/usr/bin/env bash
# 供 scripts/*.sh `source` 的公共环境（不设置 set -euo pipefail，由各脚本自行声明）。
# 统一项目根目录、端口、日志/PID 目录、Python 解释器解析与后台进程工具。

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

# 项目自带虚拟环境优先，避免误用全局 Python 导致依赖版本不匹配
resolve_python_bin() {
  if [ -n "${PYTHON_BIN:-}" ] && [ -x "${PYTHON_BIN}" ]; then
    printf '%s\n' "${PYTHON_BIN}"
    return
  fi
  if [ -x "$PROJECT_ROOT/backend/.venv/bin/python" ]; then
    printf '%s\n' "$PROJECT_ROOT/backend/.venv/bin/python"
    return
  fi
  if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "${VIRTUAL_ENV}/bin/python" ]; then
    printf '%s\n' "${VIRTUAL_ENV}/bin/python"
    return
  fi
  if [ -n "${CONDA_PREFIX:-}" ] && [ -x "${CONDA_PREFIX}/bin/python" ]; then
    printf '%s\n' "${CONDA_PREFIX}/bin/python"
    return
  fi
  if command -v uvicorn >/dev/null 2>&1; then
    local uvicorn_bin uvicorn_shebang uvicorn_python
    uvicorn_bin="$(command -v uvicorn)"
    uvicorn_shebang="$(head -n 1 "${uvicorn_bin}" 2>/dev/null || true)"
    if [[ "${uvicorn_shebang}" == '#!'* ]]; then
      uvicorn_python="${uvicorn_shebang#\#!}"
      if [ -x "${uvicorn_python}" ]; then
        printf '%s\n' "${uvicorn_python}"
        return
      fi
    fi
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python
    return
  fi
  printf '%s\n' "python3"
}

PYTHON_BIN="$(resolve_python_bin)"

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
