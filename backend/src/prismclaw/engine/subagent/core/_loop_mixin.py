"""Auto-extracted Mixin from instance.py — DO NOT edit method signatures.

轮次循环已迁移至 _subagent_loop_runner.run_subagent_turn_loop（复用
run_loop 骨架）；本 Mixin 仅保留循环所需的工具分发与进度上报辅助。
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from prismclaw.engine.execution.dispatch.execution_entry import execute_preflighted_tool_calls
from prismclaw.engine.subagent.core._instance_base import _InstanceBase
from prismclaw.engine.subagent.core._result_types import (
    SettledSubagentToolSurface,
    _coerce_skill_invocation,
)
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.content.content_types import ToolResult

if TYPE_CHECKING:
    from prismclaw.engine.subagent.definition.manifest import AgentManifest
    from prismclaw.engine.toolkit.subagent.subagent_progress_emitter import (
        SubagentProgressEmitter,
    )

logger = logging.getLogger(__name__)


class LoopMixin(_InstanceBase):
    # -- internal: tool helpers -----------------------------------------------------------

    def _settle_tool_surface(
        self,
        *,
        sub_ctx: ToolContext,
    ) -> SettledSubagentToolSurface:
        from prismclaw.engine.security.gate.types import AllowedToolsPolicy
        from prismclaw.engine.subagent.tool_surface import SUBAGENT_TOOL_SURFACE
        from prismclaw.engine.toolkit.registry.governance import (
            resolve_registry_tool_governance,
        )

        # 唯一规则：子代理工具面固定为内置专家面（不含 TEAM_LEAD_TOOLS）；
        # 同一份 surface 同时驱动 LLM 可见 schema 与治理 reducer。
        tool_surface = SUBAGENT_TOOL_SURFACE

        skill_invocation = _coerce_skill_invocation(sub_ctx.invoked_skill_state)
        allowed_policy: AllowedToolsPolicy | None = None
        if skill_invocation is not None and skill_invocation.allowed_tools_override is not None:
            registered = self._tools.names()
            lower_map: dict[str, str] = {n.lower(): n for n in registered}
            active_allowed: set[str] = set()
            for raw_name in skill_invocation.allowed_tools_override:
                tool = self._tools.get(raw_name)
                if tool is not None:
                    active_allowed.add(tool.name)
                else:
                    canonical = lower_map.get(raw_name.lower())
                    if canonical is not None:
                        active_allowed.add(canonical)
            allowed_policy = AllowedToolsPolicy(
                allow_all=False,
                allowed_names=tuple(sorted(active_allowed)),
            )
            skill_invocation = None
        discovered_tools: set[str] | None = None
        raw_turn_state = sub_ctx.turn_state
        if raw_turn_state is not None:
            from prismclaw.engine.state.turn_state import TurnState

            if isinstance(raw_turn_state, TurnState):
                discovered = raw_turn_state.discovered_tool_names()
                discovered_tools = discovered or None
            elif isinstance(raw_turn_state, dict):
                try:
                    restored_turn_state = TurnState.model_validate(raw_turn_state)
                except Exception:
                    restored_turn_state = None
                if restored_turn_state is not None:
                    discovered = restored_turn_state.discovered_tool_names()
                    discovered_tools = discovered or None

        _scope = sub_ctx.session_scope
        resolved_toolset = resolve_registry_tool_governance(
            self._tools,
            skill_invocation=skill_invocation,
            allowed_tools_policy=allowed_policy,
            discovered_tools=discovered_tools,
            subagent_depth=sub_ctx.subagent_depth,
            # 子代理结构上不持有会话队列，无法暂停等用户回答，
            # 因此对需要交互用户的工具恒为无头。
            headless=True,
            allowed_tool_owners=_scope.tool_owners if _scope is not None else None,
            tool_surface=tool_surface,
        )
        return SettledSubagentToolSurface(
            visible_tool_names=tuple(resolved_toolset.visible_tools_for_llm),
            callable_tool_names=tuple(resolved_toolset.callable_tools),
        )

    async def _dispatch_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
        *,
        sub_ctx: ToolContext,
        parent_ctx: ToolContext,
        manifest: AgentManifest,
    ) -> list[ToolResult]:
        """Dispatch tool calls using shared preflighted execution wiring."""
        return await execute_preflighted_tool_calls(
            self._tools,
            tool_calls,
            sub_ctx,
            default_tool_timeout_s=self._tool_timeout_s,
        )

    @staticmethod
    def _first_tool_name(tool_calls: list[dict[str, Any]]) -> str | None:
        if not tool_calls:
            return None
        fn = tool_calls[0].get("function") or {}
        raw = fn.get("name") or ""
        return raw.replace("__", ":", 1) if "__" in raw else (raw or None)

    # 摘要优先取这些参数（越靠前越有辨识度）；只取首个命中的字符串，不拼多值。
    _SUMMARY_ARG_KEYS = (
        "command",
        "file_path",
        "path",
        "pattern",
        "query",
        "url",
        "target",
    )

    @classmethod
    def _summarize_tool_call(cls, tool_calls: list[dict[str, Any]]) -> str | None:
        """给首个工具调用生成一行参数摘要（如 file_path=src/app.ts）。

        纯展示用途：任何解析失败都返回 None，绝不打断进度上报。
        """
        if not tool_calls:
            return None
        fn = tool_calls[0].get("function") or {}
        raw = fn.get("arguments")
        try:
            args = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except Exception:
            return None
        if not isinstance(args, dict):
            return None
        for key in cls._SUMMARY_ARG_KEYS:
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                return f"{key}={value.strip()[:60]}"
        return None

    # -- internal: progress emitter -----------------------------------------------------------

    @staticmethod
    def _resolve_progress_emitter(
        parent_ctx: ToolContext,
        manifest: AgentManifest,
    ) -> SubagentProgressEmitter | None:
        emitter: SubagentProgressEmitter | None = parent_ctx.subagent_progress_emitter
        return emitter

    def _emit_progress(
        self,
        emitter: SubagentProgressEmitter | None,
        *,
        sub_ctx: ToolContext,
        manifest: AgentManifest,
        turn: int,
        max_turns: int,
        partial_status: str,
        current_tool: str | None,
        started_at: float,
        current_tool_summary: str | None = None,
        status: str | None = None,
    ) -> None:
        if emitter is None:
            return
        try:
            # 从定义处所属模块导入：schema.agent 的惰性门面表未收录该符号，
            # 包级导入会抛 ImportError 并被本 try 吞掉（进度上报静默失效）。
            from prismclaw.schema.content.streaming import SubagentProgressItem

            active_skill_name = sub_ctx.active_skill_name
            raw_invoked_skill_names = sub_ctx.invoked_skill_names
            invoked_skill_names = (
                [str(item).strip() for item in raw_invoked_skill_names if str(item).strip()]
                if isinstance(raw_invoked_skill_names, list)
                else []
            )
            run_context = getattr(self, "_run_context", None)
            item = SubagentProgressItem(
                sub_session_id=sub_ctx.session_id,
                subagent_name=manifest.name,
                agent_id=getattr(run_context, "agent_id", None),
                run_id=getattr(self, "_run_id", None),
                lifecycle=getattr(run_context, "lifecycle", None),
                status=status,
                turn=max(1, turn),
                max_turns=max_turns,
                partial_status=partial_status,
                current_tool=current_tool,
                active_skill_name=(
                    str(active_skill_name).strip()
                    if isinstance(active_skill_name, str) and active_skill_name.strip()
                    else None
                ),
                invoked_skill_names=list(dict.fromkeys(invoked_skill_names)),
                duration_so_far=max(0.0, time.monotonic() - started_at),
                current_tool_summary=current_tool_summary,
            )
            emitter.update(item)
        except Exception:
            logger.warning(
                "subagent.progress.emit_failed name=%s status=%s",
                manifest.name,
                partial_status,
                exc_info=True,
            )

    def _emit_terminal(
        self,
        emitter: SubagentProgressEmitter | None,
        *,
        sub_ctx: ToolContext,
        manifest: AgentManifest,
        turn: int,
        max_turns: int,
        partial_status: str,
        started_at: float,
    ) -> None:
        terminal_status = {
            "completed": "succeeded",
            "aborted": "interrupted",
            "failed": "failed",
        }.get(partial_status, partial_status)
        self._emit_progress(
            emitter,
            sub_ctx=sub_ctx,
            manifest=manifest,
            turn=turn,
            max_turns=max_turns,
            partial_status=partial_status,
            current_tool=None,
            started_at=started_at,
            status=terminal_status,
        )

    # -- internal: result helpers -----------------------------------------------------------

    @staticmethod
    def _extract_last_assistant_text(
        messages: list[dict[str, Any]],
    ) -> str | None:
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                content = msg.get("content")
                if isinstance(content, str) and content:
                    return content
        return None

    @staticmethod
    def _count_assistant_turns(messages: list[dict[str, Any]]) -> int:
        return sum(1 for m in messages if m.get("role") == "assistant")

    # -- internal: cleanup -----------------------------------------------------------
