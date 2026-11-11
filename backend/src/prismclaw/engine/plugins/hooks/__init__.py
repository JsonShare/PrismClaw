"""Hook 系统模块。

对齐参考实现 ``hooks.ts`` 的 HookInput/HookOutput 结构。
"""
from __future__ import annotations

from .schema import HookContext, HookDecision, HookOutcome, HookResult, PermissionDecision
from .handlers import (
    CallbackHookHandler,
    DEFAULT_HOOK_TIMEOUT,
    HookHandler,
    HttpHookHandler,
    MatcherFilteredHandler,
    ShellCommandHookHandler,
)
from .factory import build_hook_handlers, normalize_hooks_config
from .circuit_breaker import (
    CircuitBreakerConfig,
    CircuitState,
    HookCircuitBreaker,
    HookCircuitBreakerManager,
)

__all__ = [
    # schema
    "HookContext",
    "HookDecision",
    "HookOutcome",
    "HookResult",
    "PermissionDecision",
    # handlers
    "CallbackHookHandler",
    "DEFAULT_HOOK_TIMEOUT",
    "HookHandler",
    "HttpHookHandler",
    "MatcherFilteredHandler",
    "ShellCommandHookHandler",
    # factory
    "build_hook_handlers",
    "normalize_hooks_config",
    # circuit_breaker
    "CircuitBreakerConfig",
    "CircuitState",
    "HookCircuitBreaker",
    "HookCircuitBreakerManager",
]
