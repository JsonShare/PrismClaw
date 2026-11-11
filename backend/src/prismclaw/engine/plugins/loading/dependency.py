"""插件依赖解析。

对标参考实现 ``pluginLoader.ts`` ``verifyAndDemote`` (L3260)，并增强：

1. 依赖缺失 → 降级（原有行为，保留）
2. 跨 marketplace 依赖阻断：显式限定 marketplace 的依赖（``foo@mkt``）
   若限定名 ≠ 本插件的 source，直接阻断并降级
3. 循环依赖检测：三色标记 DFS，循环成员全部降级（报错而非死循环）
4. 反向依赖记录：``DependencyCheckResult.reverse_dependencies``
5. 传递降级（不动点）：依赖了被降级插件的插件也被降级

降级即 ``enabled=False``。不写 settings（用户通过 /doctor 修复意图）。
单入口 ``verify_and_demote``，无兼容双路径。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from prismclaw.engine.plugins.loading.load_result import (
    LoadedPlugin,
    PluginDependencyError,
    PluginError,
)
from prismclaw.schema.agent.domains import PluginErrorType

# 三色标记
_WHITE, _GRAY, _BLACK = 0, 1, 2


@dataclass(slots=True)
class DependencyCheckResult:
    demoted_sources: set[str] = field(default_factory=set)
    errors: list[PluginError] = field(default_factory=list)
    # 反向依赖：plugin_id -> 依赖它的插件 plugin_id 集合
    reverse_dependencies: dict[str, set[str]] = field(default_factory=dict)


def parse_dependency_ref(ref: str) -> tuple[str, str | None]:
    """解析依赖引用。对标参考实现 ``DependencyRefSchema`` (L1400)。

    3 种格式：
    - 裸名: ``"plugin"`` → ("plugin", None)
    - 限定名: ``"plugin@marketplace"`` → ("plugin", "marketplace")
    - 带版本: ``"plugin@mkt@^1.2"`` → ("plugin", "mkt")  版本静默剥离
    """
    if "@" not in ref:
        return ref, None
    parts = ref.split("@")
    if len(parts) == 2:
        return parts[0], parts[1]
    # 3+ 段：最后一段视为版本，剥离
    return parts[0], parts[1]


def _demote(
    plugin: LoadedPlugin,
    result: DependencyCheckResult,
    *,
    error: PluginError,
) -> None:
    """将插件降级并将构造好的结构化错误写入结果。

    调用方负责构造具体子类(PluginDependencyError / 通用 PluginError)，
    本函数只负责登记降级事实——保持错误类型选择与具体业务上下文
    (缺依赖/循环/跨marketplace) 归属在一起，避免函数内部累积 if/else。
    """
    plugin.enabled = False
    result.demoted_sources.add(plugin.source)
    # 保险:补全 source/plugin 字段(若调用方构造时没填)
    if not error.source:
        object.__setattr__(error, "source", plugin.source)
    if not error.plugin:
        object.__setattr__(error, "plugin", plugin.name)
    result.errors.append(error)


def verify_and_demote(plugins: list[LoadedPlugin]) -> DependencyCheckResult:
    """依赖解析与降级（单入口）。

    阶段：
    1. 解析依赖边（缺失 / 跨 marketplace 在此阶段报错并降级）
    2. 记录反向依赖
    3. 三色 DFS 循环检测（含自依赖），循环成员全部降级
    4. 不动点传递降级：依赖被降级插件的插件也降级
    """
    result = DependencyCheckResult()
    by_name = {p.name: p for p in plugins}
    by_id = {p.plugin_id: p for p in plugins}

    # ---- 阶段 1+2：解析依赖边，记录缺失 / 跨 marketplace / 反向依赖 ----
    edges: dict[str, list[str]] = {p.plugin_id: [] for p in plugins}
    for plugin in plugins:
        deps = plugin.manifest.dependencies if plugin.manifest else None
        if not isinstance(deps, list):
            continue
        for dep_ref in deps:
            if not isinstance(dep_ref, str):
                continue
            dep_name, dep_marketplace = parse_dependency_ref(dep_ref)
            if dep_marketplace is not None:
                if dep_marketplace != plugin.source:
                    _demote(
                        plugin,
                        result,
                        error=PluginError(
                            type=PluginErrorType.CROSS_MARKETPLACE_DEPENDENCY,
                            message=(
                                f"跨市场依赖 {dep_ref} 被阻止 "
                                f"（插件来源：{plugin.source}）"
                            ),
                        ),
                    )
                    continue
                target = by_id.get(f"{dep_name}@{dep_marketplace}")
            else:
                target = by_name.get(dep_name)
            if target is None:
                _demote(
                    plugin,
                    result,
                    error=PluginDependencyError(
                        missing_requirements=[dep_ref],
                        message=f"Dependency {dep_ref} not found",
                    ),
                )
                continue
            edges[plugin.plugin_id].append(target.plugin_id)
            result.reverse_dependencies.setdefault(target.plugin_id, set()).add(plugin.plugin_id)

    # ---- 阶段 3：三色 DFS 循环检测（迭代式，含自依赖） ----
    color: dict[str, int] = {pid: _WHITE for pid in edges}
    cycle_members: set[str] = set()
    for root in edges:
        if color[root] != _WHITE:
            continue
        # 栈元素: (node, 子节点迭代索引)；path 维护当前灰色链
        stack: list[tuple[str, int]] = [(root, 0)]
        path: list[str] = []
        color[root] = _GRAY
        path.append(root)
        while stack:
            node, idx = stack[-1]
            children = edges[node]
            if idx < len(children):
                stack[-1] = (node, idx + 1)
                child = children[idx]
                if color[child] == _GRAY:
                    # 命中循环：path 中从 child 到栈顶的段即循环
                    start = path.index(child)
                    cycle_members.update(path[start:])
                elif color[child] == _WHITE:
                    color[child] = _GRAY
                    path.append(child)
                    stack.append((child, 0))
            else:
                stack.pop()
                path.pop()
                color[node] = _BLACK

    for pid in sorted(cycle_members):
        plugin = by_id[pid]
        if plugin.enabled:
            _demote(
                plugin,
                result,
                error=PluginError(
                    type=PluginErrorType.CIRCULAR_DEPENDENCY,
                    message=("检测到循环依赖：" + " -> ".join(sorted(cycle_members))),
                ),
            )

    # ---- 阶段 4：不动点传递降级 ----
    changed = True
    while changed:
        changed = False
        for plugin in plugins:
            if not plugin.enabled:
                continue
            for dep_id in edges[plugin.plugin_id]:
                if not by_id[dep_id].enabled:
                    _demote(
                        plugin,
                        result,
                        error=PluginDependencyError(
                            missing_requirements=[dep_id],
                            message=f"Dependency {dep_id} is disabled",
                        ),
                    )
                    changed = True
                    break

    return result
