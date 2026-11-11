"""插件加载缓存层。

对标参考实现 ``pluginLoader.ts`` L3180 ``loadAllPlugins`` memoize +
L3210 ``loadAllPluginsCacheOnly`` + L3270 ``clearPluginCache``。
"""
from __future__ import annotations

import asyncio
import logging

from prismclaw.engine.plugins.loading.discovery_engine import PluginLoadOrchestrator
from prismclaw.engine.plugins.loading.load_result import PluginLoadResult

logger = logging.getLogger(__name__)


class PluginLoadCache:
    """插件加载结果缓存。

    对标参考实现 ``loadAllPlugins`` memoize (L3180) +
    ``loadAllPluginsCacheOnly`` (L3210)。

    两个缓存：
    - full: 完整加载结果（含网络请求）
    - cache_only: 快速启动路径（只读缓存，跳过网络请求）

    信任状态变化时自动 bust 缓存（workspace_trusted 参与 key）。
    """

    def __init__(self, orchestrator: PluginLoadOrchestrator) -> None:
        self._orchestrator = orchestrator
        self._full_cache: PluginLoadResult | None = None
        self._cache_only_cache: PluginLoadResult | None = None
        self._full_lock = asyncio.Lock()
        self._cache_only_lock = asyncio.Lock()
        self._cached_trusted: bool | None = None

    async def load_all(self, *, workspace_trusted: bool = True) -> PluginLoadResult:
        """完整加载（含网络请求）。对标参考实现 ``loadAllPlugins``。

        用 memoize 缓存结果，重复调用返回缓存。
        信任状态变化时自动 invalidate。
        """
        if self._full_cache is not None and self._cached_trusted == workspace_trusted:
            return self._full_cache
        async with self._full_lock:
            if self._full_cache is not None and self._cached_trusted == workspace_trusted:
                return self._full_cache
            result = await self._orchestrator.assemble_plugin_load_result(
                cache_only=False, workspace_trusted=workspace_trusted
            )
            self._full_cache = result
            self._cached_trusted = workspace_trusted
            return result

    async def load_all_cache_only(self, *, workspace_trusted: bool = True) -> PluginLoadResult:
        """快速启动路径。对标参考实现 ``loadAllPluginsCacheOnly`` (L3210)。

        跳过网络请求，直接读 installed_plugins.json 的 install_path。
        信任状态变化时自动 invalidate。
        """
        if self._cache_only_cache is not None and self._cached_trusted == workspace_trusted:
            return self._cache_only_cache
        async with self._cache_only_lock:
            if self._cache_only_cache is not None and self._cached_trusted == workspace_trusted:
                return self._cache_only_cache
            result = await self._orchestrator.assemble_plugin_load_result(
                cache_only=True, workspace_trusted=workspace_trusted
            )
            self._cache_only_cache = result
            self._cached_trusted = workspace_trusted
            return result

    def clear(self, reason: str = "unknown") -> None:
        """清除两个缓存。对标参考实现 ``clearPluginCache`` (L3270)。"""
        if self._full_cache is not None or self._cache_only_cache is not None:
            logger.debug("Clearing plugin load cache: %s", reason)
        self._full_cache = None
        self._cache_only_cache = None
        self._cached_trusted = None

    @property
    def has_full_cache(self) -> bool:
        return self._full_cache is not None

    @property
    def has_cache_only_cache(self) -> bool:
        return self._cache_only_cache is not None
