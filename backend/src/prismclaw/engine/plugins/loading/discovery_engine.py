"""插件发现引擎。

对标参考实现 ``pluginLoader.ts`` L1900 ``loadPluginsFromMarketplaces`` +
L3230 ``assemblePluginLoadResult``。

配置驱动发现，文件系统扫描作为补充。
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prismclaw.engine.plugins.registry.identity import (
    BUILTIN_SOURCE,
    LOCAL_SOURCE,
    PluginID,
    SESSION_SOURCE,
)
from prismclaw.engine.plugins.ingest.path_remap import parse_remap_rules
from prismclaw.engine.plugins.loading.install_store import InstalledPluginsStore
from prismclaw.engine.plugins.loading.load_result import (
    LoadedPlugin,
    ManifestParseError,
    PluginError,
    PluginLoadBatch,
    PluginLoadResult,
)
from prismclaw.engine.plugins.loading.load_strategy import (
    PluginLoadStrategy,
)
from prismclaw.engine.plugins.loading.merge import merge_plugin_sources
from prismclaw.engine.plugins.loading.dependency import verify_and_demote
from prismclaw.engine.plugins.trust import (
    is_source_allowed_by_policy,
    is_source_trusted_for_load,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PluginDiscoveryResult:
    """发现结果。"""

    to_activate: list[PluginID] = field(default_factory=list)
    to_deactivate: list[PluginID] = field(default_factory=list)
    errors: list[PluginError] = field(default_factory=list)


@dataclass(slots=True)
class PluginDirectorySource:
    """插件目录来源描述。"""

    path: Path
    source: str  # builtin | marketplace | session | local
    priority: int


class PluginDiscoveryEngine:
    """配置驱动的插件发现，文件系统扫描作为补充。

    对标参考实现 ``loadPluginsFromMarketplaces`` (L1900)。
    """

    def __init__(
        self,
        *,
        install_store: InstalledPluginsStore,
        enabled_plugin_ids: list[str] | None = None,
        managed_names: set[str] | None = None,
        blocked_sources: set[str] | None = None,
        strict_known_sources: set[str] | None = None,
        trusted_plugins: set[str] | None = None,
        directory_sources: list[PluginDirectorySource] | None = None,
        strategies: list[PluginLoadStrategy],
    ) -> None:
        self._install_store = install_store
        self._enabled_plugin_ids = enabled_plugin_ids or []
        self._managed_names = managed_names
        self._blocked_sources = blocked_sources
        self._strict_known_sources = strict_known_sources
        self._trusted_plugins = trusted_plugins or set()
        self._directory_sources = directory_sources or []
        self._strategies = strategies
        # marketplace.json 声明的 marketplace 级 porting 上下文，按子插件名索引：
        # {plugin_name: {"remap_rules": list[PathRemapRule], "marketplace_name": str}}。
        # 由 _scan_directory 在解析 marketplace.json 时通用填充（不针对任何特定插件）。
        self._marketplace_meta: dict[str, dict[str, Any]] = {}

    def discover(self, *, workspace_trusted: bool = True) -> PluginDiscoveryResult:
        """发现需要激活/停用的插件。

        ``workspace_trusted`` 是文件夹级信任开关（对标参考实现
        ``performStartupChecks``）：未信任工作区时，UNTRUSTED 来源
        （local/session）的插件被跳过。默认 ``True`` 保持既有行为。
        """
        result = PluginDiscoveryResult()
        seen_names: set[str] = set()

        # Step 1: 配置驱动的插件
        for raw_id in self._enabled_plugin_ids:
            try:
                plugin_id = PluginID.parse(raw_id)
            except (ValueError, TypeError) as exc:
                result.errors.append(
                    ManifestParseError(
                        source="config",
                        plugin=raw_id,
                        file_path="(settings.plugins.enabled)",
                        line=0,
                        message=f"插件 ID '{raw_id}' 无效：{exc}",
                    )
                )
                continue

            if not is_source_allowed_by_policy(
                plugin_id.source,
                strict_known=self._strict_known_sources,
                blocked=self._blocked_sources,
            ):
                result.errors.append(
                    PluginError(
                        type="marketplace_blocked_by_policy",
                        source=plugin_id.source,
                        plugin=plugin_id.name,
                        message=f"Source {plugin_id.source} blocked by policy",
                    )
                )
                continue

            if not is_source_trusted_for_load(
                plugin_id.source,
                workspace_trusted=workspace_trusted,
                plugin_id=plugin_id.canonical,
                explicitly_trusted=self._trusted_plugins,
            ):
                result.errors.append(
                    PluginError(
                        type="source_not_trusted",
                        source=plugin_id.source,
                        plugin=plugin_id.name,
                        message=(
                            f"来源 {plugin_id.source} 需要可信工作区才能加载"
                        ),
                    )
                )
                continue

            result.to_activate.append(plugin_id)
            seen_names.add(plugin_id.name)

        # Step 2: 目录扫描补充（标记为 auto_discovered）
        for dir_source in self._directory_sources:
            if not is_source_allowed_by_policy(
                dir_source.source,
                strict_known=self._strict_known_sources,
                blocked=self._blocked_sources,
            ):
                continue
            # 信任门控下沉到逐插件级：不受信任工作区不再整体跳过目录，
            # 否则显式信任（/plugins trust <id>）的插件永远扫不到。
            scanned = self._scan_directory(dir_source)
            for plugin_id in scanned:
                if plugin_id.name in seen_names:
                    continue
                if not is_source_trusted_for_load(
                    plugin_id.source,
                    workspace_trusted=workspace_trusted,
                    plugin_id=plugin_id.canonical,
                    explicitly_trusted=self._trusted_plugins,
                ):
                    continue
                seen_names.add(plugin_id.name)
                result.to_activate.append(plugin_id)

        return result

    def _scan_directory(self, dir_source: PluginDirectorySource) -> list[PluginID]:
        """扫描目录发现插件。

        v4：兼容 marketplace.json 形式（对标参考实现 目录扫描）。
        1. 递归扫描每个子目录中的 ``.plugin/marketplace.json``
        2. 否则直接扫描子目录
        """
        if not dir_source.path.exists() or not dir_source.path.is_dir():
            return []
        discovered: list[PluginID] = []
        seen_names: set[str] = set()
        # 1. 扫描每个子目录中的 marketplace.json
        try:
            for entry in sorted(dir_source.path.iterdir(), key=lambda p: p.name):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                marketplace_json = entry / ".plugin" / "marketplace.json"
                if marketplace_json.exists():
                    try:
                        data = json.loads(marketplace_json.read_text(encoding="utf-8"))
                        if isinstance(data, dict):
                            # marketplace 级 porting 上下文（通用）：remap 规则由
                            # marketplace.json 的 porting.remap 声明，name 用作共享
                            # 数据目录键。无任何插件特定逻辑。
                            porting = data.get("porting")
                            remap_cfg = (
                                porting.get("remap") if isinstance(porting, dict) else None
                            )
                            remap_rules = parse_remap_rules(remap_cfg)
                            mp_name = data.get("name")
                            marketplace_name = (
                                mp_name if isinstance(mp_name, str) and mp_name else entry.name
                            )
                            plugins = data.get("plugins") or []
                            if isinstance(plugins, list):
                                for sub in plugins:
                                    if not isinstance(sub, dict):
                                        continue
                                    name = sub.get("name")
                                    if not isinstance(name, str) or not name:
                                        continue
                                    self._marketplace_meta[name] = {
                                        "remap_rules": remap_rules,
                                        "marketplace_name": marketplace_name,
                                        "marketplace_description": data.get("description"),
                                    }
                                    if name in seen_names:
                                        continue
                                    seen_names.add(name)
                                    try:
                                        discovered.append(
                                            PluginID(name=name, source=dir_source.source)
                                        )
                                    except ValueError:
                                        pass
                    except Exception as exc:
                        logger.debug("Failed to parse marketplace.json %s: %s", marketplace_json, exc)
        except OSError as exc:
            logger.debug("Failed to scan %s: %s", dir_source.path, exc)

        # 2. 直接扫描子目录（fallback）
        try:
            for entry in sorted(dir_source.path.iterdir(), key=lambda p: p.name):
                if not entry.is_dir() or entry.name.startswith("."):
                    continue
                for strategy in self._strategies:
                    if strategy.can_handle(entry):
                        # 用目录名作为 plugin_name
                        if entry.name in seen_names:
                            break
                        seen_names.add(entry.name)
                        try:
                            discovered.append(
                                PluginID(name=entry.name, source=dir_source.source)
                            )
                        except ValueError:
                            pass
                        break
        except OSError as exc:
            logger.debug("Failed to scan %s: %s", dir_source.path, exc)
        return discovered

    def marketplace_porting_context(self, plugin_name: str) -> dict[str, Any] | None:
        """返回子插件继承的 marketplace 级 porting 上下文，独立插件返回 None。

        结构 ``{"remap_rules": list[PathRemapRule], "marketplace_name": str}``，
        由 ``_scan_directory`` 解析 marketplace.json 的 ``porting.remap`` 与 ``name``
        通用填充。加载器据此应用插件声明的重映射规则、并派生共享数据目录。
        """
        return self._marketplace_meta.get(plugin_name)

    def resolve_install_path(self, plugin_id: PluginID) -> Path | None:
        """根据 ID 查找安装路径。

        1. 查 installed_plugins.json 的 install_path (对标参考实现 installEntry.installPath)
        2. marketplace.json 声明的 source 相对路径
        3. Fallback 到目录扫描
        """
        record = self._install_store.get(plugin_id)
        if record is not None:
            path = Path(record.install_path)
            if path.exists():
                return path

        # 2. marketplace.json 中的 source 相对路径（递归扫描子目录）
        for dir_source in self._directory_sources:
            if dir_source.source != plugin_id.source:
                continue
            if not dir_source.path.exists() or not dir_source.path.is_dir():
                continue
            try:
                for entry in sorted(dir_source.path.iterdir(), key=lambda p: p.name):
                    if not entry.is_dir() or entry.name.startswith("."):
                        continue
                    marketplace_json = entry / ".plugin" / "marketplace.json"
                    if not marketplace_json.exists():
                        continue
                    try:
                        data = json.loads(marketplace_json.read_text(encoding="utf-8"))
                        if not isinstance(data, dict):
                            continue
                        plugins = data.get("plugins") or []
                        if not isinstance(plugins, list):
                            continue
                        for sub in plugins:
                            if not isinstance(sub, dict):
                                continue
                            if sub.get("name") == plugin_id.name:
                                rel = sub.get("source")
                                if isinstance(rel, str) and rel:
                                    candidate = (entry / rel).resolve()
                                    if candidate.exists():
                                        return candidate
                    except Exception as exc:
                        logger.debug("Failed to parse marketplace.json %s: %s", marketplace_json, exc)
            except OSError as exc:
                logger.debug("Failed to scan %s: %s", dir_source.path, exc)

        # 3. marketplace 级回退：利用 _marketplace_meta 中已解析的
        # marketplace_name 推导子插件目录。当 marketplace.json 的 plugins
        # 条目未显式声明 source 时（默认子目录名即插件名），此路径可正确定位。
        meta = self._marketplace_meta.get(plugin_id.name)
        if meta is not None:
            marketplace_name = meta.get("marketplace_name")
            if isinstance(marketplace_name, str) and marketplace_name:
                for dir_source in self._directory_sources:
                    if dir_source.source != plugin_id.source:
                        continue
                    candidate = (dir_source.path / marketplace_name / plugin_id.name).resolve()
                    if candidate.exists() and candidate.is_dir():
                        return candidate

        # 4. Fallback: 目录扫描
        for dir_source in self._directory_sources:
            if dir_source.source != plugin_id.source:
                continue
            candidate = dir_source.path / plugin_id.name
            if candidate.exists() and candidate.is_dir():
                return candidate
        return None


class PluginLoadOrchestrator:
    """三源并行加载 + 合并。

    对标参考实现 ``assemblePluginLoadResult`` (L3230)。
    """

    def __init__(
        self,
        *,
        discovery: PluginDiscoveryEngine,
        strategies: list[PluginLoadStrategy],
        plugin_configs: dict[str, dict[str, Any]] | None = None,
        managed_names: set[str] | None = None,
        data_base_dir: Path | None = None,
    ) -> None:
        self._discovery = discovery
        self._strategies = strategies
        self._plugin_configs = plugin_configs or {}
        self._managed_names = managed_names
        self._data_base_dir = data_base_dir

    async def assemble_plugin_load_result(
        self,
        *,
        cache_only: bool = False,
        workspace_trusted: bool = True,
    ) -> PluginLoadResult:
        """三源并行加载 + 合并。参考实现 ``assemblePluginLoadResult`` (L3230)。

        流程：
        1. 发现引擎 → to_activate
        2. 按 source 分组，并行加载
        3. merge_plugin_sources 合并
        4. verify_and_demote 依赖检查
        5. 返回 PluginLoadResult
        """
        discovery_result = self._discovery.discover(workspace_trusted=workspace_trusted)
        # 按 source 分组
        groups: dict[str, list[PluginID]] = {}
        for plugin_id in discovery_result.to_activate:
            groups.setdefault(plugin_id.source, []).append(plugin_id)

        # 并行加载各源（对标参考实现 Promise.allSettled）
        load_tasks = []
        for source, ids in groups.items():
            load_tasks.append(self._load_group(source, ids))
        group_results = await asyncio.gather(*load_tasks, return_exceptions=False)

        builtin_batch = PluginLoadBatch.empty()
        marketplace_batch = PluginLoadBatch.empty()
        session_batch = PluginLoadBatch.empty()
        local_batch = PluginLoadBatch.empty()
        for source, batch in zip(groups.keys(), group_results):
            if source == BUILTIN_SOURCE:
                builtin_batch = batch
            elif source == "marketplace":
                marketplace_batch = batch
            elif source == SESSION_SOURCE:
                session_batch = batch
            elif source == LOCAL_SOURCE:
                local_batch = batch

        # 三源合并（对标参考实现 mergePluginSources L3060）
        merged = merge_plugin_sources(
            session=session_batch.plugins,
            marketplace=marketplace_batch.plugins,
            builtin=builtin_batch.plugins,
            local=local_batch.plugins,
            managed_names=self._managed_names,
        )

        # 依赖检查（对标参考实现 verifyAndDemote L3260）
        dep_result = verify_and_demote(merged.plugins)

        enabled = [p for p in merged.plugins if p.enabled]
        disabled = [p for p in merged.plugins if not p.enabled]

        errors = [
            *discovery_result.errors,
            *builtin_batch.errors,
            *marketplace_batch.errors,
            *session_batch.errors,
            *local_batch.errors,
            *merged.errors,
            *dep_result.errors,
        ]

        return PluginLoadResult(enabled=enabled, disabled=disabled, errors=errors)

    async def _load_group(
        self, source: str, plugin_ids: list[PluginID]
    ) -> PluginLoadBatch:
        """并行加载一组插件。对标参考实现 ``Promise.allSettled``。"""
        tasks = [self._load_one(pid) for pid in plugin_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        batch = PluginLoadBatch()
        for plugin_id, result in zip(plugin_ids, results):
            if isinstance(result, Exception):
                batch.errors.append(
                    PluginError(
                        type="generic_error",
                        source=plugin_id.source,
                        plugin=plugin_id.name,
                        message=str(result),
                    )
                )
                continue
            loaded, errors = result
            batch.errors.extend(errors)
            if loaded is not None:
                batch.plugins.append(loaded)
        return batch

    async def _load_one(
        self, plugin_id: PluginID
    ) -> tuple[LoadedPlugin | None, list[PluginError]]:
        """加载单个插件。"""
        install_path = self._discovery.resolve_install_path(plugin_id)
        if install_path is None:
            return None, [
                PluginError(
                    type="plugin_not_found",
                    source=plugin_id.source,
                    plugin=plugin_id.name,
                    message=f"未找到 {plugin_id} 的安装路径",
                )
            ]
        for strategy in self._strategies:
            if strategy.can_handle(install_path):
                return await strategy.load(
                    plugin_id,
                    install_path,
                    enabled=True,
                    plugin_configs=self._plugin_configs,
                    data_base_dir=self._data_base_dir,
                    porting_context=self._discovery.marketplace_porting_context(
                        plugin_id.name
                    ),
                )
        return None, [
            PluginError(
                type="plugin_not_found",
                source=plugin_id.source,
                plugin=plugin_id.name,
                message=f"没有可处理 {install_path} 的加载策略",
            )
        ]
