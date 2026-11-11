"""Global runtime root resolution.

所有技能/插件共享的运行时根 —— 重资产（node_modules、fonts、pip --target 包）
装在这里，一次安装全 workspace 生效。

布局::

    ~/.prismclaw/runtime/                  # 本地 dev / 用户级
    /runtime/                              # 容器内（prismclaw-runtime 卷挂载）
    ├── skills/<skill_name>/node_modules/
    ├── skills/<skill_name>/site-packages/
    ├── plugins/<plugin_name>/...
    ├── fonts/
    └── tmp/

resolve 优先级 (**三阶段，容器探针在 env 之后**):

1. ``override`` 参数（调用方显式传入，给测试/嵌入式用，不缓存）
2. ``PRISMCLAW_RUNTIME_ROOT`` env 覆盖（用户级 override）
3. 容器探针 ``Path('/runtime').exists()``（容器内 runtime 卷已挂载）
4. ``~/.prismclaw/server.yaml`` 的 ``runtime.default``（用户级配置文件）
5. 硬编码 fallback ``~/.prismclaw/runtime``

这和 plugins-data（~/.prismclaw/plugins-data/）、workspaces（~/.prismclaw/workspaces/）
同属 ``~/.prismclaw/`` 用户级共享根下的并列子目录。

缓存: ``resolve_runtime_root()`` 调用无 override 时结果被模块级 LRU 缓存
（只缓存最后一次无 override 场景），消除 bash tool 每次执行时的 json.load +
Path.home 开销。``override`` 参数不进入缓存。
"""
from __future__ import annotations

import os
from pathlib import Path

from prismclaw.infra.config_io import load_document

__all__ = ["DEFAULT_RUNTIME_DIRNAME", "resolve_runtime_root"]

DEFAULT_RUNTIME_DIRNAME = "runtime"
# 用户级配置文件（yaml 唯一）
_SERVER_CONFIG_PATH = Path("~/.prismclaw/server.yaml").expanduser()
_CONTAINER_RUNTIME_PROBE = Path("/runtime")

# ── 模块级缓存（仅缓存无 override 的场景）─────────────────────────────
# LRU(maxsize=1) 足够：bash.py 每次调 _resolve_runtime_root() 都没 override
# container_backend.py 也不用 override —— 整个进程里 resolve_runtime_root()
# 的无 override 调用每次拿到同一个值，缓存命中 100%。
_CACHED_NO_OVERRIDE: Path | None = None
_CACHE_VERSION: int = 0  # 给测试用，允许手动失效


def _read_server_config_runtime_default() -> str | None:
    if not _SERVER_CONFIG_PATH.is_file():
        return None
    data = load_document(_SERVER_CONFIG_PATH)
    if data is None:
        return None
    raw = data.get("runtime", {}).get("default")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def resolve_runtime_root(override: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the global shared runtime root.

    ``override`` 优先级最高，且不进入缓存；不传 ``override`` 时结果被
    模块级缓存（单个）缓存，后续 bash 调用零开销。目录 demand-driven 创建
    （不在这一步 mkdir）——容器探针只做存在性检测。
    """
    global _CACHED_NO_OVERRIDE, _CACHE_VERSION

    if override is not None:
        return Path(override).expanduser()

    # ── 缓存命中 ──
    if _CACHED_NO_OVERRIDE is not None:
        return _CACHED_NO_OVERRIDE

    # ── 1. PRISMCLAW_RUNTIME_ROOT env ──
    env_val = (os.getenv("PRISMCLAW_RUNTIME_ROOT") or "").strip()
    if env_val:
        result = Path(env_val).expanduser()
        _CACHED_NO_OVERRIDE = result
        return result

    # ── 2. 容器探针：/runtime 存在说明容器内已挂载共享卷 ──
    # 比 ~/.prismclaw/server.yaml 更优先 —— 容器里该文件大概率不存在，
    # 且 env 已在上一步命中 → 这一步只处理 env 缺失但 /runtime 卷已挂上的场景。
    if _CONTAINER_RUNTIME_PROBE.is_dir():
        _CACHED_NO_OVERRIDE = _CONTAINER_RUNTIME_PROBE
        return _CONTAINER_RUNTIME_PROBE

    # ── 3. ~/.prismclaw/server.yaml 的 runtime.default ──
    raw_default = _read_server_config_runtime_default()
    if raw_default is not None:
        result = Path(raw_default).expanduser()
        _CACHED_NO_OVERRIDE = result
        return result

    # ── 4. 硬编码 fallback ──
    result = Path.home() / ".prismclaw" / DEFAULT_RUNTIME_DIRNAME
    _CACHED_NO_OVERRIDE = result
    return result


def _invalidate_cache() -> None:
    """手动失效缓存（给测试用；生产环境一般不需要）。"""
    global _CACHED_NO_OVERRIDE, _CACHE_VERSION
    _CACHED_NO_OVERRIDE = None
    _CACHE_VERSION += 1
