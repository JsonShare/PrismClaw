"""域模块故障隔离 — 包裹域工具和钩子，防止异常传播到核心执行流程。

对标 DeepSeek Harness 的 try/except 包裹 + Pi Agent 的隔离执行。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class DomainIsolatedTool:
    """包裹域工具，隔离执行异常。

    域工具崩溃时返回 ToolResult(error=...) 而非抛异常，
    确保核心 Agent Loop 不被单个域工具的故障打断。

    透传 Tool Protocol 的全部必选属性：
    name, description, parameters, side_effect, tool_class, effect_class
    """

    def __init__(self, tool: Any, domain_name: str) -> None:
        self._tool = tool
        self.domain = domain_name
        # 透传 Tool Protocol 必选属性（提供安全默认值）
        self.name = getattr(tool, "name", f"__domain_{domain_name}_unknown__")
        self.description = getattr(tool, "description", "")
        self.parameters = getattr(tool, "parameters", {})
        self.tool_class = getattr(tool, "tool_class", "write")
        self.side_effect = getattr(tool, "side_effect", True)
        self.effect_class = getattr(tool, "effect_class", "unknown")
        # 透传可选属性
        for attr in ("required", "annotations"):
            if hasattr(tool, attr):
                setattr(self, attr, getattr(tool, attr))

    async def execute(self, args: Any, context: Any) -> Any:
        try:
            return await self._tool.execute(args, context)
        except Exception as e:
            logger.exception(
                "Domain '%s' tool '%s' failed", self.domain, self.name
            )
            # 返回错误结果而非抛异常
            try:
                from prismclaw.schema.content.tool_call import ToolResult
                return ToolResult(
                    output=f"[{self.domain}] Error: {type(e).__name__}: {e}"
                )
            except ImportError:
                return {"error": f"[{self.domain}] {type(e).__name__}: {e}"}


class DomainIsolatedHook:
    """包裹域钩子，隔离执行异常。

    域钩子崩溃时返回 HookResult(passed=True, error=...) 而非抛异常，
    确保后续钩子和核心流程正常执行。

    使用 execute() 方法名（与 HookHandler ABC 一致），
    满足 HookDispatcher.register() 的 isinstance 严格检查。
    通过运行时注册为 HookHandler 子类来通过类型检查。
    """

    def __init__(self, handler: Any, domain_name: str) -> None:
        self._handler = handler
        self.domain = domain_name
        # 透传 HookHandler Protocol 属性
        for attr in ("name", "event"):
            if hasattr(handler, attr):
                setattr(self, attr, getattr(handler, attr))
        # 运行时注册为 HookHandler 子类（通过 isinstance 检查）
        self._register_as_hook_handler()

    def _register_as_hook_handler(self) -> None:
        """运行时注册 DomainIsolatedHook 为 HookHandler 的虚拟子类。

        HookDispatcher.register() 使用 isinstance(handler, HookHandler) 严格检查。
        HookHandler 是 ABC，通过 register() 可以将任意类注册为虚拟子类。
        """
        try:
            from prismclaw.engine.plugins.hooks.handlers import HookHandler
            HookHandler.register(DomainIsolatedHook)
        except ImportError:
            pass

    async def execute(self, context: Any) -> Any:
        """HookHandler.execute() 实现 — 包裹异常隔离。"""
        try:
            # 如果原始 handler 是 HookHandler 实例，调 execute()
            if hasattr(self._handler, "execute"):
                result = await self._handler.execute(context)
            # 如果是 callable（如 CallbackHookHandler 包装的函数），直接调用
            elif callable(self._handler):
                result = await self._handler(context)
            else:
                result = await self._handler(context)
            return result
        except Exception as e:
            logger.exception("Domain '%s' hook failed", self.domain)
            # 返回"通过但记录错误"的结果，不阻断后续钩子
            try:
                from prismclaw.engine.plugins.hooks.schema import HookResult
                return HookResult(passed=True, error=str(e))
            except ImportError:
                return None
