from __future__ import annotations

import functools
from typing import Any, Callable

class ToolRegistration:
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.func: Callable[..., Any] | None = None

class HookRegistration:
    def __init__(self, event_name: str):
        self.event_name = event_name
        self.func: Callable[..., Any] | None = None

# Global registries for the currently loading plugin
_CURRENT_TOOLS: list[ToolRegistration] = []
_CURRENT_HOOKS: list[HookRegistration] = []

def tool(name: str, description: str = "") -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to expose a function as a PrismClaw tool."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        reg = ToolRegistration(name, description)
        reg.func = func
        _CURRENT_TOOLS.append(reg)
        
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)
        return wrapper
    return decorator

def hook(event_name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to subscribe a function to a PrismClaw lifecycle event."""
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        reg = HookRegistration(event_name)
        reg.func = func
        _CURRENT_HOOKS.append(reg)
        
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)
        return wrapper
    return decorator
