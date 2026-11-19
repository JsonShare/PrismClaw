"""运行时只读根的单一权威源。

``~/.prismclaw/assets/`` 是启动时由 repo 同步过来的静态能力目录
（skills / plugins / agents / IDENTITY.md / PREFERENCES.md）。agent 的
读操作需要能看到它，写操作永远不能。

**只放行 assets，绝不放行整个 ``~/.prismclaw``**：同一层下还有
``workspaces/``（其它用户的数据）与 ``data/`` / ``plugins-data/``
（凭据与插件持久数据）。整根放行等于把跨用户隔离与凭据隔离一起交出去。

本模块不依赖任何执行器或沙箱实现，供 ``RuntimeExecutor`` 与
``BwrapPolicy`` 共用，避免两处各写一份、逐渐漂移。
"""
from __future__ import annotations

from pathlib import Path


def runtime_readonly_roots() -> tuple[Path, ...]:
    """返回静态能力根（只读）。目录不存在时返回空元组。"""
    assets = Path.home() / ".prismclaw" / "assets"
    try:
        return (assets.resolve(),) if assets.is_dir() else ()
    except OSError:
        return ()
