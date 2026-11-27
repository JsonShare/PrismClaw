"""专家包生命周期状态机。

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

from enum import StrEnum


class ExpertLifecycleState(StrEnum):
    UNINSTALLED = "uninstalled"
    INSTALLED = "installed"
    LOADED = "loaded"
    ACTIVE = "active"
    INACTIVE = "inactive"


# 合法状态转移表
_TRANSITIONS: dict[ExpertLifecycleState, frozenset[ExpertLifecycleState]] = {
    ExpertLifecycleState.UNINSTALLED: frozenset({ExpertLifecycleState.INSTALLED}),
    ExpertLifecycleState.INSTALLED: frozenset(
        {
            ExpertLifecycleState.LOADED,
            ExpertLifecycleState.UNINSTALLED,
        }
    ),
    ExpertLifecycleState.LOADED: frozenset(
        {
            ExpertLifecycleState.ACTIVE,
            ExpertLifecycleState.INSTALLED,
        }
    ),
    ExpertLifecycleState.ACTIVE: frozenset(
        {
            ExpertLifecycleState.INACTIVE,
            ExpertLifecycleState.INSTALLED,
        }
    ),
    ExpertLifecycleState.INACTIVE: frozenset(
        {
            ExpertLifecycleState.ACTIVE,
            ExpertLifecycleState.INSTALLED,
        }
    ),
}


class IllegalStateTransitionError(Exception):
    """非法状态转移。"""


def can_transition(current: ExpertLifecycleState, target: ExpertLifecycleState) -> bool:
    return target in _TRANSITIONS.get(current, frozenset())


def assert_transition(current: ExpertLifecycleState, target: ExpertLifecycleState) -> None:
    if not can_transition(current, target):
        raise IllegalStateTransitionError(
            f"Cannot transition expert from {current.value} to {target.value}"
        )
