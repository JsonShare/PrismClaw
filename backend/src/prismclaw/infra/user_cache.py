"""用户级缓存根目录的单一权威源。

缓存与额度状态属于「用户级」数据，两者都不该落在别处：

* 不属于项目仓库 —— 写进源码树会污染仓库，且落点随项目根/cwd 解析漂移；
* 不属于某个工作区 —— 工作区是任务产物的落点，agent 可直接读写，
  混入运行时缓存会既干扰任务、又可能被交付物打包扫入。

落点与 memory、plugins-data 同级，固定在用户目录下：

    ~/.prismclaw/cache/<namespace>/

命名空间（``literature`` / ``science`` 等）由调用方给出，本模块只负责
根与拼接，不做命名空间的登记表——避免多一处需要同步的清单。
"""
from __future__ import annotations

from pathlib import Path

__all__ = [
    "resolve_user_cache_subdir",
]

# 与 ~/.prismclaw 下其他用户级目录（memory、plugins-data）同级的缓存根名
_CACHE_DIRNAME = "cache"


def resolve_user_cache_subdir(name: str) -> Path:
    """返回 ``~/.prismclaw/cache/<name>``，按需创建目录。

    环境变量覆盖保留在各调用点：历史命名不同（如
    ``PRISMCLAW_SCIENCE_CACHE_DIR`` / ``PRISMCLAW_SCIENCE_STATE_DIR``），
    统一改名会破坏已有离线/云端部署，故不在此收口。
    """
    d = Path.home() / ".prismclaw" / _CACHE_DIRNAME / name
    d.mkdir(parents=True, exist_ok=True)
    return d
