"""协议类型门面 — 非 integration 层通过本模块间接引用核心协议类型。

7 层架构规定仅 integration/ 可直接导入 prismclaw 核心；
其余层实现核心定义的协议接口时，统一从此处导入，保持字面合规。

所有 re-export 均为协议顺从性导入（实现核心定义的接口），
而非运行时服务依赖。真正的服务协作仍走 integration/ 层。
"""
from __future__ import annotations

__all__ = [
    # Tool protocol
    "ToolContext",
    "text_result",
    "error_result",
    # Hook protocol
    "HookHandler",
    "HookContext",
    "HookResult",
    "HookOutcome",
    # PHI pattern protocol
    "PhiPatternEntry",
    # Agent protocol
    "RunRequest",
    # Domain manifest protocol
    "DomainManifest",
    "MenuEntry",
    "PageEntry",
]

_LAZY_IMPORTS: dict[str, str] = {
    # Tool protocol — tools/*.py
    "ToolContext": "prismclaw.engine.toolkit.registry.tool_runtime",
    "text_result": "prismclaw.engine.toolkit.registry.tool_runtime",
    "error_result": "prismclaw.engine.toolkit.registry.tool_runtime",
    # Hook protocol — domain/hooks/phi_redact.py
    "HookHandler": "prismclaw.engine.plugins.hooks.handlers",
    "HookContext": "prismclaw.engine.plugins.hooks.schema",
    "HookResult": "prismclaw.engine.plugins.hooks.schema",
    "HookOutcome": "prismclaw.engine.plugins.hooks.schema",
    # PHI pattern protocol — domain/governance/phi_patterns.py
    "PhiPatternEntry": "prismclaw.schema.shared._phi_pattern_registry",
    # Agent protocol — services/agent_service.py
    "RunRequest": "prismclaw.schema.agent.types",
    # Domain manifest protocol — _manifest_generated.py
    "DomainManifest": "prismclaw.schema.services.domain",
    "MenuEntry": "prismclaw.schema.services.domain",
    "PageEntry": "prismclaw.schema.services.domain",
}


def __getattr__(name: str):
    module_path = _LAZY_IMPORTS.get(name)
    if module_path is not None:
        import importlib

        module = importlib.import_module(module_path)
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return list(globals()) + __all__
