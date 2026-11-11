"""插件信任层级。

借鉴参考实现 ``hooks.ts`` L180 ``shouldSkipHookDueToTrust`` +
``pluginLoader.ts`` L2000 ``isSourceAllowedByPolicy``。

两层门控：
  1. 文件夹级信任（``resolve_workspace_trusted``）——由 Settings 驱动，
     ``untrusted_workspaces`` 列出的路径视为不受信任，其余默认受信任。
  2. 来源策略（``is_source_allowed_by_policy``）——企业黑白名单。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from prismclaw.engine.plugins.registry.identity import (
    BUILTIN_SOURCE,
    LOCAL_SOURCE,
    MARKETPLACE_SOURCE,
    SESSION_SOURCE,
)

TRUSTED = "trusted"
VERIFIED = "verified"
UNTRUSTED = "untrusted"

# source → trust tier 映射
TRUST_HIERARCHY: dict[str, str] = {
    BUILTIN_SOURCE: TRUSTED,
    MARKETPLACE_SOURCE: VERIFIED,
    SESSION_SOURCE: UNTRUSTED,
    LOCAL_SOURCE: UNTRUSTED,
}


def trust_tier_for_source(source: str) -> str:
    return TRUST_HIERARCHY.get(source, UNTRUSTED)


def is_source_allowed_by_policy(
    source: str,
    *,
    strict_known: set[str] | None = None,
    blocked: set[str] | None = None,
) -> bool:
    """来源策略校验。参考实现 ``isSourceAllowedByPolicy``。

    - ``blocked`` 黑名单优先
    - ``strict_known`` 白名单（非空时只允许白名单内的来源）

    注意：默认（未配置 ``blocked`` / ``strict_known``）返回 ``True``，即
    **fail-open**。它只负责"企业策略层"的显式拦截，不包含文件夹信任。
    """
    if blocked and source in blocked:
        return False
    if strict_known is not None and source not in strict_known:
        return False
    return True


def resolve_workspace_trusted(
    workspace_path: str | Path,
    settings: Any = None,
) -> bool:
    """解析当前工作区的信任状态。

    真实来源：``Settings.workspaces.untrusted_workspaces`` 列表。
    规则：如果 ``workspace_path``（resolved）出现在黑名单中，返回 ``False``；
    否则返回 ``True``（默认信任，向后兼容）。

    这是 ``workspace_trusted`` 的唯一权威来源——所有调用方必须通过此函数
    获取信任状态，禁止硬编码。
    """
    if settings is None:
        return True
    ws_settings = getattr(settings, "workspaces", None)
    if ws_settings is None:
        return True
    untrusted_list: list[str] = getattr(ws_settings, "untrusted_workspaces", []) or []
    if not untrusted_list:
        return True
    try:
        resolved = Path(workspace_path).expanduser().resolve()
    except Exception:
        return True  # 无法解析路径时 fail-open
    for entry in untrusted_list:
        try:
            if Path(entry).expanduser().resolve() == resolved:
                return False
        except Exception:
            continue
    return True


def is_source_trusted_for_load(
    source: str,
    *,
    workspace_trusted: bool,
    plugin_id: str | None = None,
    explicitly_trusted: set[str] | None = None,
) -> bool:
    """插件是否可在当前工作区信任态下被加载。

    对标参考实现 的两层门控：文件夹级信任（``workspace_trusted``）是
    真正的开关，``performStartupChecks`` 在用户信任文件夹后才装/载插件；
    ``isSourceAllowedByPolicy`` 只是企业策略层的显式拦截。

    规则：TRUSTED/VERIFIED 来源（builtin/marketplace）始终可加载；UNTRUSTED
    来源（local/session）需要工作区已被信任，否则拒绝。

    P3-T3 显式信任：若 ``plugin_id``（name@source）落在 ``explicitly_trusted``
    集合中（由 ``/plugins trust <id>`` 写入 Settings），则绕过工作区信任门控——
    用户已对该插件单独背书，即便工作区不受信任也可加载。
    """
    if explicitly_trusted and plugin_id is not None and plugin_id in explicitly_trusted:
        return True
    if trust_tier_for_source(source) == UNTRUSTED and not workspace_trusted:
        return False
    return True


def trusted_plugins_from_settings(settings: object | None) -> set[str]:
    """从 Settings 提取显式信任插件 ID 集合（``plugins.trusted_plugins``）。

    P3-T3：信任归位的唯一读取点。settings 缺 ``plugins`` 组或字段时返回空集。
    """
    plugin_settings = getattr(settings, "plugins", None)
    if plugin_settings is None:
        return set()
    return set(getattr(plugin_settings, "trusted_plugins", []) or [])
