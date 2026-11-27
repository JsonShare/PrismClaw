"""Hook 执行处理器。

- ``ShellCommandHookHandler`` → Shell 子进程执行
- ``HttpHookHandler`` → HTTP POST 请求
- ``CallbackHookHandler`` → Python callable

Shell hook 细节：
- stdin 注入 HookContext JSON
- stdout 读取 HookResult JSON
- 环境变量注入 PRISMCLAW_PLUGIN_ROOT / PRISMCLAW_PLUGIN_DATA
- ${PRISMCLAW_PLUGIN_ROOT} / ${PRISMCLAW_PLUGIN_DATA} 变量替换
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from prismclaw.engine.hooks.schema import HookContext, HookOutcome, HookResult

logger = logging.getLogger(__name__)

# 默认超时 30s（PrismClaw hook 主要是轻量级检查）
DEFAULT_HOOK_TIMEOUT = 30.0


class HookHandler(ABC):
    """Hook 处理器接口。"""

    @abstractmethod
    async def execute(self, context: HookContext) -> HookResult:
        """执行 hook。"""


class CallbackHookHandler(HookHandler):
    """Python callable hook 处理器。PrismClaw 已有此模式。"""

    def __init__(self, callback: Callable[[HookContext], Awaitable[object] | object]) -> None:
        self._callback = callback

    async def execute(self, context: HookContext) -> HookResult:
        import inspect

        try:
            if inspect.iscoroutinefunction(self._callback):
                result = await self._callback(context)
            else:
                result = self._callback(context)
            if isinstance(result, HookResult):
                return result
            if isinstance(result, dict):
                return HookResult.from_dict(result)
            if result is None:
                return HookResult.success()
            return HookResult.success()
        except Exception as exc:
            logger.error("Callback hook failed: %s", exc)
            return HookResult(
                outcome=HookOutcome.NON_BLOCKING_ERROR,
                system_message=str(exc),
            )


class MatcherFilteredHandler(HookHandler):
    """带 matcher 过滤的 hook 处理器装饰器。

    hooks.json 的 matcher 组：``matcher`` 是对工具名（``tool_name``）
    的正则过滤，仅工具类事件（PreToolUse/PostToolUse）有意义。命中才委托内层
    handler，未命中直接返回 ``HookResult.success()``（no-op，不阻断、不注入）。

    语义：
    - ``matcher`` 为 None/空/``"*"`` → 恒命中（此类 handler 通常不应被包装，
      但为保证安全，包装后也按恒命中处理）；
    - 上下文无 ``tool_name``（非工具事件）而 matcher 有值 → 恒不命中；
    - 正则非法 → 退化为子串匹配，避免因配置笔误静默失配。
    """

    def __init__(self, inner: HookHandler, matcher: str) -> None:
        self._inner = inner
        self._matcher = matcher

    def _matches(self, candidate: str | None) -> bool:
        matcher = self._matcher
        if not matcher or matcher == "*":
            return True
        if not candidate:
            return False
        try:
            return re.search(matcher, candidate) is not None
        except re.error:
            return matcher in candidate

    async def execute(self, context: HookContext) -> HookResult:
        # 匹配候选列表：tool_name → skill_name
        for candidate in (context.tool_name, context.skill_name):
            if self._matches(candidate):
                return await self._inner.execute(context)
        logger.debug(
            "hook matcher %r did not match any candidate (tool_name=%r, skill_name=%r)",
            self._matcher,
            context.tool_name,
            context.skill_name,
        )
        return HookResult.success()


class ShellCommandHookHandler(HookHandler):
    """Shell 子进程执行 hook。

    行为：
    - 通过 stdin 注入 HookContext JSON
    - 从 stdout 读取 HookResult JSON
    - 环境变量注入 PRISMCLAW_PLUGIN_ROOT / PRISMCLAW_PLUGIN_DATA
    - ${PRISMCLAW_PLUGIN_ROOT} / ${PRISMCLAW_PLUGIN_DATA} 变量替换
    """

    def __init__(
        self,
        command: str,
        *,
        timeout: float = DEFAULT_HOOK_TIMEOUT,
        expert_root: str | None = None,
        expert_data_dir: str | None = None,
        shell: str | None = None,
    ) -> None:
        self._command = command
        self._timeout = timeout
        self._expert_root = expert_root
        self._expert_data_dir = expert_data_dir
        self._shell = shell or _default_shell()

    async def execute(self, context: HookContext) -> HookResult:
        command = self._substitute_variables(self._command)
        env = self._build_env()

        try:
            stdin_data = json.dumps(context.to_dict()) + "\n"
            proc = await asyncio.create_subprocess_exec(
                self._shell,
                "-c",
                command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=stdin_data.encode("utf-8")),
                    timeout=self._timeout,
                )
            except TimeoutError:
                proc.kill()
                await proc.wait()
                logger.error("Shell hook timed out after %ss: %s", self._timeout, command)
                return HookResult(
                    outcome=HookOutcome.BLOCKING,
                    system_message=f"钩子执行超时（{self._timeout}s）",
                )

            if proc.returncode != 0:
                logger.warning(
                    "Shell hook exited with %d: %s",
                    proc.returncode,
                    stderr.decode("utf-8", errors="replace"),
                )

            return _parse_hook_output(stdout.decode("utf-8", errors="replace"))
        except FileNotFoundError as exc:
            logger.error("Shell hook failed (shell not found): %s", exc)
            return HookResult(outcome=HookOutcome.NON_BLOCKING_ERROR, system_message=str(exc))
        except Exception as exc:
            logger.error("Shell hook failed: %s", exc)
            return HookResult(outcome=HookOutcome.NON_BLOCKING_ERROR, system_message=str(exc))

    def _substitute_variables(self, command: str) -> str:
        """变量替换：expert root/data 占位符，走共享替换器。"""
        from prismclaw.engine.experts.manifest.substitution import (
            build_expert_variables,
            substitute_variables,
        )

        return substitute_variables(
            command,
            build_expert_variables(
                expert_root=self._expert_root,
                expert_data_dir=self._expert_data_dir,
            ),
        )

    def _build_env(self) -> dict[str, str]:
        """构建子进程环境变量。"""
        env = dict(os.environ)
        if self._expert_root is not None:
            env["PRISMCLAW_PLUGIN_ROOT"] = self._expert_root
        if self._expert_data_dir is not None:
            env["PRISMCLAW_PLUGIN_DATA"] = self._expert_data_dir
        return env


class HttpHookHandler(HookHandler):
    """HTTP POST 请求 hook。"""

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = DEFAULT_HOOK_TIMEOUT,
    ) -> None:
        self._url = url
        self._headers = headers or {"Content-Type": "application/json"}
        self._timeout = timeout

    async def execute(self, context: HookContext) -> HookResult:
        try:
            import aiohttp
        except ImportError:
            logger.error("aiohttp not available for HTTP hook")
            return HookResult(
                outcome=HookOutcome.NON_BLOCKING_ERROR, system_message="aiohttp not installed"
            )

        from prismclaw.infra.aiohttp_session import open_client_session

        try:
            async with (
                open_client_session() as session,
                session.post(
                    self._url,
                    json=context.to_dict(),
                    headers=self._headers,
                    timeout=aiohttp.ClientTimeout(total=self._timeout),
                ) as resp,
            ):
                if resp.content_type == "application/json":
                    body = await resp.json()
                else:
                    text = await resp.text()
                    try:
                        body = json.loads(text)
                    except json.JSONDecodeError:
                        return HookResult(
                            outcome=HookOutcome.NON_BLOCKING_ERROR,
                            system_message=f"HTTP 钩子返回了非 JSON 内容：{text[:200]}",
                        )
            if isinstance(body, dict):
                return HookResult.from_dict(body)
            return HookResult.success()
        except TimeoutError:
            logger.error("HTTP hook timed out after %ss", self._timeout)
            return HookResult(
                outcome=HookOutcome.BLOCKING,
                system_message=f"HTTP 钩子超时（{self._timeout}s）",
            )
        except Exception as exc:
            logger.error("HTTP hook failed: %s", exc)
            return HookResult(outcome=HookOutcome.NON_BLOCKING_ERROR, system_message=str(exc))


def _default_shell() -> str:
    """默认 shell。"""
    if sys.platform == "win32":
        return "bash"
    return os.environ.get("SHELL", "bash")


def _parse_hook_output(stdout: str) -> HookResult:
    """解析 hook stdout。"""
    stripped = stdout.strip()
    if not stripped:
        return HookResult.success()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        # 非 JSON 输出视为 plain text
        return HookResult(
            additional_context=stripped,
            system_message=stripped if len(stripped) < 200 else None,
        )
    if not isinstance(parsed, dict):
        return HookResult.success()
    return HookResult.from_dict(parsed)
