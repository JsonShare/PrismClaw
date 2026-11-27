"""域模块故障隔离 — 包裹域工具和钩子，防止异常传播到核心执行流程。

所有域工具与钩子统一经本模块 try/except 包裹，异常只记日志、不外溢。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from prismclaw.schema.content.content_types import TextBlock, ToolResult

if TYPE_CHECKING:
    from prismclaw.engine.hooks.schema import HookResult
    from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext

logger = logging.getLogger(__name__)


@runtime_checkable
class _ExecutableTool(Protocol):
    """域工具的最小执行契约。"""

    async def execute(self, args: object, context: object) -> object: ...


@runtime_checkable
class _ExecutableHook(Protocol):
    """域钩子的最小执行契约。"""

    async def execute(self, context: object) -> object: ...


class DomainIsolatedTool:
    """包裹域工具，隔离执行异常。

    域工具崩溃时返回 ToolResult(error=...) 而非抛异常，
    确保核心 Agent Loop 不被单个域工具的故障打断。

    透传 Tool Protocol 的全部必选属性：
    name, description, parameters, side_effect, tool_class, effect_class
    """

    def __init__(self, tool: object, domain_name: str) -> None:
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

    async def execute(self, args: object, context: ToolContext) -> ToolResult:
        tool = self._tool
        if not isinstance(tool, _ExecutableTool):
            return ToolResult(
                tool_call_id="",
                content=[TextBlock(text=f"[{self.domain}] Error: 工具缺少 execute() 方法")],
                is_error=True,
            )
        try:
            result = await tool.execute(args, context)
        except Exception as e:
            logger.exception("Domain '%s' tool '%s' failed", self.domain, self.name)
            # 返回错误结果而非抛异常
            return ToolResult(
                tool_call_id="",
                content=[TextBlock(text=f"[{self.domain}] Error: {type(e).__name__}: {e}")],
                is_error=True,
            )
        if isinstance(result, ToolResult):
            return result
        return ToolResult(
            tool_call_id="",
            content=[TextBlock(text=f"[{self.domain}] Error: 工具返回了非法结果类型")],
            is_error=True,
        )


class DomainIsolatedHook:
    """包裹域钩子，隔离执行异常。

    域钩子崩溃时返回非阻断错误结果而非抛异常，
    确保后续钩子和核心流程正常执行。

    使用 execute() 方法名（与 HookHandler ABC 一致），
    满足 HookDispatcher.register() 的 isinstance 严格检查。
    通过运行时注册为 HookHandler 子类来通过类型检查。
    """

    def __init__(self, handler: object, domain_name: str) -> None:
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
            from prismclaw.engine.hooks.handlers import HookHandler

            HookHandler.register(DomainIsolatedHook)
        except ImportError:
            pass

    async def execute(self, context: object) -> HookResult:
        """HookHandler.execute() 实现 — 包裹异常隔离。"""
        from prismclaw.engine.hooks.schema import HookOutcome, HookResult

        handler = self._handler
        if not isinstance(handler, _ExecutableHook):
            return HookResult(
                outcome=HookOutcome.NON_BLOCKING_ERROR,
                system_message=f"[{self.domain}] 钩子缺少 execute() 方法",
            )
        try:
            result = await handler.execute(context)
        except Exception as e:
            logger.exception("Domain '%s' hook failed", self.domain)
            # 返回"记录错误但不阻断"的结果，不中断后续钩子
            return HookResult(
                outcome=HookOutcome.NON_BLOCKING_ERROR,
                system_message=str(e),
            )
        if isinstance(result, HookResult):
            return result
        return HookResult.success()
