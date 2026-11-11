"""插件生命周期状态机。

PrismClaw 特有设计（Python ``importlib`` 需要 load/activate 分离）。

状态流转::

    [uninstalled] ──install()──► [installed]
    [installed]   ──load()─────► [loaded]
    [loaded]      ──activate()─► [active]
    [active]      ──deactivate()─► [inactive]
    [inactive]    ──activate()─► [active]
    [active|inactive|loaded] ──unload()─► [installed]
    [installed]   ──uninstall()─► [uninstalled]
"""
from __future__ import annotations

from enum import Enum


class PluginLifecycleState(str, Enum):
    UNINSTALLED = "uninstalled"
    INSTALLED = "installed"
    LOADED = "loaded"
    ACTIVE = "active"
    INACTIVE = "inactive"


# 合法状态转移表
_TRANSITIONS: dict[PluginLifecycleState, frozenset[PluginLifecycleState]] = {
    PluginLifecycleState.UNINSTALLED: frozenset({PluginLifecycleState.INSTALLED}),
    PluginLifecycleState.INSTALLED: frozenset({
        PluginLifecycleState.LOADED,
        PluginLifecycleState.UNINSTALLED,
    }),
    PluginLifecycleState.LOADED: frozenset({
        PluginLifecycleState.ACTIVE,
        PluginLifecycleState.INSTALLED,
    }),
    PluginLifecycleState.ACTIVE: frozenset({
        PluginLifecycleState.INACTIVE,
        PluginLifecycleState.INSTALLED,
    }),
    PluginLifecycleState.INACTIVE: frozenset({
        PluginLifecycleState.ACTIVE,
        PluginLifecycleState.INSTALLED,
    }),
}


class IllegalStateTransitionError(Exception):
    """非法状态转移。"""


def can_transition(current: PluginLifecycleState, target: PluginLifecycleState) -> bool:
    return target in _TRANSITIONS.get(current, frozenset())


def assert_transition(current: PluginLifecycleState, target: PluginLifecycleState) -> None:
    if not can_transition(current, target):
        raise IllegalStateTransitionError(
            f"Cannot transition plugin from {current.value} to {target.value}"
        )
