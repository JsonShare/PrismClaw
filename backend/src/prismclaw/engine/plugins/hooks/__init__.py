"""Hook 系统模块。"""

from __future__ import annotations

from .circuit_breaker import (
    CircuitBreakerConfig,
    CircuitState,
    HookCircuitBreaker,
    HookCircuitBreakerManager,
)
from .factory import build_hook_handlers, normalize_hooks_config
from .handlers import (
    DEFAULT_HOOK_TIMEOUT,
    CallbackHookHandler,
    HookHandler,
    HttpHookHandler,
    MatcherFilteredHandler,
    ShellCommandHookHandler,
)
from .schema import HookContext, HookDecision, HookOutcome, HookResult, PermissionDecision

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
