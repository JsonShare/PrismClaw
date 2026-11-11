from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Protocol, runtime_checkable

from prismclaw.schema.content.compaction import CompactResult
from prismclaw.engine.plugins.hooks.schema import (
    HookContext,
    HookDecision,
    HookOutcome,
    HookResult,
    PermissionDecision,
)
from prismclaw.engine.plugins.hooks.handlers import HookHandler

if TYPE_CHECKING:
    from prismclaw.schema.agent.run_control_types import RunControl
    from prismclaw.schema.services.skills import SkillManifest


logger = logging.getLogger(__name__)

# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class PromptBuildContext:
    session_id: str
    workspace_id: str
    agent_id: str
    available_tools: list[str] = field(default_factory=list)
    prompt: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class PromptBuildResult:
    prepend: str | None = None
    append: str | None = None


@dataclass
class ResponseObservation:
    session_id: str
    assistant_text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CompactionContext:
    session_id: str
    workspace_id: str
    agent_id: str
    tokens_before: int = 0
    message_count: int = 0
    extras: dict[str, Any] = field(default_factory=dict)


# ── Type aliases ──────────────────────────────────────────────────────────────


# ── Protocols ────────────────────────────────────────────────────────────────


@runtime_checkable
class SkillProvider(Protocol):
    def list_skills(self, workspace_path: str | None = None) -> list["SkillManifest"]: ...
    def get(self, name: str, workspace_path: str | None = None) -> "SkillManifest" | None: ...
    def refresh_lightweight(
        self,
        file_paths: Iterable[str | Path] | None = None,
        workspace_path: str | None = None,
    ) -> list["SkillManifest"]: ...
    def refresh_heavyweight(
        self,
        workspace_path: str | None = None,
    ) -> list["SkillManifest"]: ...
    def set_plugin_skill_dirs(self, dirs: list[Path]) -> None: ...
    def track_file_paths(self, paths: Iterable[str | Path]) -> None: ...


@runtime_checkable
class AgentHook(Protocol):
    """Hook protocol for the agent loop.

    Required: before_prompt_build, after_response, before_compaction,
    after_compaction.

    Optional (discovered via getattr by HookRegistry): on_run_start,
    on_run_end.
    """

    async def before_prompt_build(
        self, context: PromptBuildContext
    ) -> PromptBuildResult | None: ...
    async def after_response(self, observation: ResponseObservation) -> None: ...
    async def before_compaction(self, context: CompactionContext) -> None: ...
    async def after_compaction(self, context: CompactionContext, result: CompactResult) -> None: ...


# ── HookRegistry ─────────────────────────────────────────────────────────────


class HookRegistry:
    def __init__(self) -> None:
        self._hooks: list[AgentHook] = []

    def register(self, hook: AgentHook) -> None:
        self._hooks.append(hook)

    def hooks(self) -> list[AgentHook]:
        return list(self._hooks)

    async def collect_prompt_additions(self, context: PromptBuildContext) -> PromptBuildResult:
        import logging

        logger = logging.getLogger(__name__)
        prepends: list[str] = []
        appends: list[str] = []
        for hook in self._hooks:
            try:
                result = await hook.before_prompt_build(context)
            except Exception:
                logger.exception("before_prompt_build hook failed: %r", hook)
                continue
            if result is None:
                continue
            if result.prepend:
                prepends.append(result.prepend)
            if result.append:
                appends.append(result.append)
        return PromptBuildResult(
            prepend="\n\n".join(prepends) if prepends else None,
            append="\n\n".join(appends) if appends else None,
        )

    async def notify_response(self, observation: ResponseObservation) -> None:
        import logging

        logger = logging.getLogger(__name__)
        for hook in self._hooks:
            try:
                await hook.after_response(observation)
            except Exception:
                logger.exception("after_response hook failed: %r", hook)

    async def notify_before_compaction(self, context: CompactionContext) -> None:
        import logging

        logger = logging.getLogger(__name__)
        for hook in self._hooks:
            try:
                await hook.before_compaction(context)
            except Exception:
                logger.exception("before_compaction hook failed: %r", hook)

    async def notify_after_compaction(
        self, context: CompactionContext, result: CompactResult
    ) -> None:
        import logging

        logger = logging.getLogger(__name__)
        for hook in self._hooks:
            try:
                await hook.after_compaction(context, result)
            except Exception:
                logger.exception("after_compaction hook failed: %r", hook)

    async def notify_run_start(self, session_id: str, control: RunControl) -> None:
        import logging

        logger = logging.getLogger(__name__)
        for hook in self._hooks:
            method = getattr(hook, "on_run_start", None)
            if method is None:
                continue
            try:
                await method(session_id, control)
            except Exception:
                logger.exception("on_run_start hook failed: %r", hook)

    async def notify_run_end(self, session_id: str, terminated_by: str) -> None:
        import logging

        logger = logging.getLogger(__name__)
        for hook in self._hooks:
            method = getattr(hook, "on_run_end", None)
            if method is None:
                continue
            try:
                await method(session_id, terminated_by)
            except Exception:
                logger.exception("on_run_end hook failed: %r", hook)


# ── HookEvent ────────────────────────────────────────────────────────────────


class HookEvent(str, Enum):
    """Hook 事件枚举。对标参考实现 30+ 种 HookEvent。"""

    # 会话生命周期
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    SETUP = "setup"
    STOP = "stop"
    # 用户输入
    USER_PROMPT_SUBMIT = "user_prompt_submit"
    # 工具调用
    PRE_TOOL = "pre_tool"
    POST_TOOL = "post_tool"
    POST_TOOL_FAILURE = "post_tool_failure"
    # 压缩
    PRE_COMPACT = "pre_compact"
    POST_COMPACT = "post_compact"
    # Subagent
    SUBAGENT_START = "subagent_start"
    SUBAGENT_STOP = "subagent_stop"
    # 环境
    FILE_CHANGED = "file_changed"
    CWD_CHANGED = "cwd_changed"
    # 权限
    PERMISSION_REQUEST = "permission_request"
    PERMISSION_DENIED = "permission_denied"


# ── HookDispatcher ───────────────────────────────────────────────────────────

DEFAULT_DISPATCH_TIMEOUT = 30.0


class HookDispatcher:
    """Hook 调度器。

    v4：只支持 ``HookHandler`` 接口，``dispatch`` 统一返回 ``HookResult``。
    第一个 blocking 结果即停止后续 handler 执行。
    """

    def __init__(self, *, default_timeout: float = DEFAULT_DISPATCH_TIMEOUT) -> None:
        self._hooks: dict[HookEvent, list[HookHandler]] = {
            event: [] for event in HookEvent
        }
        self._default_timeout = default_timeout

    def register(self, event: HookEvent, handler: HookHandler) -> None:
        """注册 HookHandler。"""
        if not isinstance(handler, HookHandler):
            raise TypeError(
                f"Hook handler must be HookHandler instance, got {type(handler).__name__}"
            )
        if event in self._hooks:
            self._hooks[event].append(handler)

    def register_named(self, event_name: str, handler: HookHandler) -> bool:
        """按事件名注册。"""
        normalized = _normalize_hook_event_name(event_name)
        if normalized is None:
            return False
        self.register(normalized, handler)
        return True

    def unregister(self, event: HookEvent, handler: HookHandler) -> None:
        """移除已注册的钩子（域注册回滚用）"""
        key = event if isinstance(event, HookEvent) else HookEvent(event)
        if key in self._hooks:
            handlers = self._hooks[key]
            try:
                handlers.remove(handler)
            except ValueError:
                pass

    def register_extension_hooks(self, extension_manager: Any) -> None:
        """从 ExtensionManager 注册所有 hook bindings。"""
        for binding in extension_manager.iter_hook_bindings():
            event_name = binding.event_name
            handler = binding.handler
            if not isinstance(event_name, str):
                continue
            if not isinstance(handler, HookHandler):
                logger.debug(
                    "Skipping non-HookHandler binding for event %s", event_name
                )
                continue
            self.register_named(event_name, handler)

    async def dispatch(
        self,
        event: HookEvent,
        context: HookContext | Any,
        *,
        timeout: float | None = None,
    ) -> HookResult:
        """分发事件，返回聚合 HookResult。

        对标参考实现 hook decision/updatedInput/additionalContext。
        第一个 blocking 结果即停止后续 handler 执行。
        """
        if event not in self._hooks or not self._hooks[event]:
            return HookResult.success()

        effective_timeout = timeout if timeout is not None else self._default_timeout
        logger.debug("Dispatching hook event: %s", event.value)

        aggregated = HookResult.success()
        for handler in self._hooks[event]:
            try:
                result = await asyncio.wait_for(
                    self._execute_handler(handler, context),
                    timeout=effective_timeout,
                )
            except asyncio.CancelledError:
                # 取消有两种语义，必须分开处理：
                # * 运行期取消（abort / 上层超时）→ 原样上抛，控制流不能
                #   被 hook 层吞掉；
                # * 停机期取消（TaskManager / shutdown drain）→ 记 CANCELLED
                #   并停止后续 handler。停机时的取消是预期行为，不是 hook
                #   故障，不能落成「Hook execution failed」的错误日志。
                from prismclaw.infra.lifecycle_state import is_shutting_down

                if not is_shutting_down():
                    raise
                logger.info(
                    "hook.cancelled_by_shutdown event=%s handler=%s",
                    event.value,
                    type(handler).__name__,
                )
                aggregated.outcome = HookOutcome.CANCELLED
                break
            except asyncio.TimeoutError:
                logger.error("Hook execution timed out for event %s", event.value)
                result = HookResult(
                    outcome=HookOutcome.NON_BLOCKING_ERROR,
                    system_message=f"钩子执行超时（{effective_timeout}s）",
                )
            except Exception as exc:
                logger.warning(
                    "Hook execution failed for event %s handler=%s: %s",
                    event.value,
                    type(handler).__name__,
                    exc,
                    exc_info=True,
                )
                result = HookResult(
                    outcome=HookOutcome.NON_BLOCKING_ERROR,
                    system_message=str(exc),
                )

            # 聚合结果
            if result.additional_context:
                if aggregated.additional_context:
                    aggregated.additional_context += "\n" + result.additional_context
                else:
                    aggregated.additional_context = result.additional_context
            if result.system_message and not aggregated.system_message:
                aggregated.system_message = result.system_message
            if result.permission_decision and not aggregated.permission_decision:
                aggregated.permission_decision = result.permission_decision
                aggregated.permission_decision_reason = result.permission_decision_reason
            if result.updated_input and not aggregated.updated_input:
                aggregated.updated_input = result.updated_input
            if result.updated_mcp_tool_output is not None and aggregated.updated_mcp_tool_output is None:
                aggregated.updated_mcp_tool_output = result.updated_mcp_tool_output
            if result.decision and not aggregated.decision:
                aggregated.decision = result.decision
                aggregated.decision_reason = result.decision_reason

            # blocking 结果停止后续 handler
            if not result.continue_execution:
                aggregated.continue_execution = False
                aggregated.outcome = result.outcome
                break

        return aggregated

    async def _execute_handler(
        self, handler: HookHandler, context: Any
    ) -> HookResult:
        """执行 HookHandler，返回 HookResult。"""
        hook_context = _coerce_context(context)
        return await handler.execute(hook_context)


# ── Helper functions ──────────────────────────────────────────────────────────


def _coerce_context(context: Any) -> HookContext:
    """将任意 context 转换为 HookContext。

    如果 context 已经是 HookContext，直接返回。
    否则尝试从 dict 或对象属性构建。
    """
    if isinstance(context, HookContext):
        return context
    if isinstance(context, dict):
        return HookContext(
            event=context.get("event", ""),
            session_id=context.get("session_id", ""),
            cwd=context.get("cwd", ""),
            tool_name=context.get("tool_name"),
            tool_input=context.get("tool_input"),
            skill_name=context.get("skill_name"),
        )
    # 从对象属性构建
    return HookContext(
        event=getattr(context, "event", getattr(context, "hook_event", "")),
        session_id=getattr(context, "session_id", ""),
        cwd=getattr(context, "cwd", ""),
        tool_name=getattr(context, "tool_name", None),
        tool_input=getattr(context, "tool_input", None),
        skill_name=getattr(context, "skill_name", None),
    )


def _normalize_hook_event_name(event_name: str) -> HookEvent | None:
    """规范化 hook 事件名。对标参考实现 ``normalizeHookEventName``。"""
    normalized = re.sub(r"[^a-z0-9]+", "", event_name.strip().lower())
    mapping = {
        # 会话生命周期
        "sessionstart": HookEvent.SESSION_START,
        "sessionend": HookEvent.SESSION_END,
        "setup": HookEvent.SETUP,
        "stop": HookEvent.STOP,
        # 用户输入
        "userpromptsubmit": HookEvent.USER_PROMPT_SUBMIT,
        # 工具调用
        "pretool": HookEvent.PRE_TOOL,
        "pretooluse": HookEvent.PRE_TOOL,
        "posttool": HookEvent.POST_TOOL,
        "posttooluse": HookEvent.POST_TOOL,
        "posttoolfailure": HookEvent.POST_TOOL_FAILURE,
        "posttoolfail": HookEvent.POST_TOOL_FAILURE,
        # 压缩
        "precompact": HookEvent.PRE_COMPACT,
        "postcompact": HookEvent.POST_COMPACT,
        # Subagent
        "subagentstart": HookEvent.SUBAGENT_START,
        "subagentstop": HookEvent.SUBAGENT_STOP,
        # 环境
        "filechanged": HookEvent.FILE_CHANGED,
        "cwdchanged": HookEvent.CWD_CHANGED,
        # 权限
        "permissionrequest": HookEvent.PERMISSION_REQUEST,
        "permissiondenied": HookEvent.PERMISSION_DENIED,
    }
    return mapping.get(normalized)


def _log_hook_outcome(event: HookEvent, result: HookResult) -> None:
    """v4：统一记录 hook dispatch 结果。

    对 BLOCKING / NON_BLOCKING_ERROR / CANCELLED 记录 warning，
    SUCCESS 不记录。
    """
    if result.outcome == HookOutcome.SUCCESS:
        return
    logger.warning(
        "hook %s returned outcome=%s system_message=%s",
        event.value,
        result.outcome.value,
        result.system_message,
    )


async def _dispatch_pre_tool_hooks(
    *,
    dispatcher: HookDispatcher,
    parsed_calls: list[Any],
    session_id: str,
    cwd: str,
    active_skill_name: str | None = None,
) -> dict[str, str]:
    """逐调用分发 PRE_TOOL hook，收集插件拒绝决策。

    对标参考实现 PreToolUse：每个工具调用单独分发，携带 tool_name/tool_input，
    使插件 hook 的 matcher 过滤与按工具拒绝生效。返回 ``{call_id: reason}``，
    由调用方并入 ``denied_ids``——被拒调用经骨架 before_tool_call 拦截，
    结果与 assistant 消息一起落树（persist_denied_turn / execute_tools Step 0）。

    最小闭环语义：
    - ``permission_decision == DENY`` 或通用 ``decision == BLOCK`` → 拒绝该调用；
    - ``ALLOW`` 不覆盖审批门禁（只追加拒绝，从不移除）；
    - ``ASK`` 暂不路由，留给既有 tool_approval_hook 通道（阶段二处理）。
    """
    denied: dict[str, str] = {}
    for parsed in parsed_calls:
        call = parsed.call if isinstance(parsed.call, dict) else {}
        call_id = call.get("id", "") or ""
        if not call_id:
            continue
        context = HookContext(
            event=HookEvent.PRE_TOOL.value,
            session_id=session_id,
            cwd=cwd,
            tool_name=parsed.name or None,
            tool_input=parsed.arguments or None,
            skill_name=active_skill_name,
        )
        result = await dispatcher.dispatch(HookEvent.PRE_TOOL, context)
        _log_hook_outcome(HookEvent.PRE_TOOL, result)
        reason: str | None = None
        if result.permission_decision == PermissionDecision.DENY:
            reason = result.permission_decision_reason or "denied by plugin hook"
        elif result.decision == HookDecision.BLOCK:
            reason = result.decision_reason or "blocked by plugin hook"
        if reason:
            denied[call_id] = f"[plugin hook] {reason}"
    return denied