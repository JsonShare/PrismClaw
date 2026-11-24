from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


class ToolRegistration:
    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description
        self.func: Callable[..., Any] | None = None


class HookRegistration:
    def __init__(self, event_name: str) -> None:
        self.event_name = event_name
        self.func: Callable[..., Any] | None = None


# Global registries for the currently loading plugin
_CURRENT_TOOLS: list[ToolRegistration] = []
_CURRENT_HOOKS: list[HookRegistration] = []


def tool(name: str, description: str = "") -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to expose a function as a PrismClaw tool."""

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        reg = ToolRegistration(name, description)
        reg.func = func
        _CURRENT_TOOLS.append(reg)

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            return func(*args, **kwargs)

        return wrapper

    return decorator


def hook(event_name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to subscribe a function to a PrismClaw lifecycle event."""

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        reg = HookRegistration(event_name)
        reg.func = func
        _CURRENT_HOOKS.append(reg)

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            return func(*args, **kwargs)

        return wrapper

    return decorator
