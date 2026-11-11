"""三源合并 + managed 锁定。

参考实现 ``pluginLoader.ts`` L3060 ``mergePluginSources``。

优先级：session > marketplace > local > builtin
managed 不可被 session 覆盖。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from prismclaw.engine.plugins.loading.load_result import LoadedPlugin, PluginError


@dataclass(slots=True)
class MergedResult:
    """合并结果。"""

    plugins: list[LoadedPlugin] = field(default_factory=list)
    errors: list[PluginError] = field(default_factory=list)


def merge_plugin_sources(
    *,
    session: list[LoadedPlugin],
    marketplace: list[LoadedPlugin],
    builtin: list[LoadedPlugin],
    local: list[LoadedPlugin] | None = None,
    managed_names: set[str] | None = None,
) -> MergedResult:
    """按 session > marketplace > local > builtin 优先级合并。

    参考实现 ``mergePluginSources`` (L3060)。
    managed 不可被 session 覆盖（对标参考实现 managedPluginNames 逻辑）。
    """
    errors: list[PluginError] = []
    managed = managed_names or set()
    local_plugins = local or []

    # Step 1: managed 不可被 session 覆盖
    session_plugins: list[LoadedPlugin] = []
    for plugin in session:
        if plugin.name in managed:
            errors.append(
                PluginError(
                    type="generic_error",
                    source=plugin.source,
                    plugin=plugin.name,
                    message=(
                        f'已忽略来自 --plugin-dir 的 "{plugin.name}" 副本：'
                        "该插件已被托管设置锁定"
                    ),
                )
            )
            continue
        session_plugins.append(plugin)

    # Step 2: session 覆盖 marketplace/local 同名插件
    session_names = {p.name for p in session_plugins}
    marketplace_plugins = [p for p in marketplace if p.name not in session_names]
    local_plugins = [p for p in local_plugins if p.name not in session_names]

    # Step 3: marketplace 覆盖 local 同名插件
    marketplace_names = {p.name for p in marketplace_plugins}
    local_plugins = [p for p in local_plugins if p.name not in marketplace_names]

    # Step 4: 最终顺序 — session first, then marketplace, then local, then builtin
    # 下游 first-match 消费者先看到 session 插件
    return MergedResult(
        plugins=[*session_plugins, *marketplace_plugins, *local_plugins, *builtin],
        errors=errors,
    )
