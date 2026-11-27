"""Base class declaring shared state for AgentInstance mixin composition.

``LifecycleMixin`` / ``ContextMixin`` / ``LoopMixin`` are auto-extracted from the
former monolithic ``instance.py`` and freely reference each other's attributes and
methods. Declaring that shared surface here keeps static analysis able to resolve
those cross-mixin references while the concrete ``AgentInstance`` still owns the
single ``__init__``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from prismclaw.engine.subagent.core._result_types import SettledSubagentToolSurface
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.content.content_types import ToolResult
from prismclaw.schema.content.subagent import SubagentContextSnapshot

if TYPE_CHECKING:
    from prismclaw.engine.execution.skills.runtime import SkillRuntime
    from prismclaw.engine.subagent.core.run_context import SubagentRunContext
    from prismclaw.engine.subagent.definition.manifest import AgentManifest
    from prismclaw.engine.subagent.shared_types import PreloadedSkillContext
    from prismclaw.engine.toolkit.registry.registry import ToolRegistry
    from prismclaw.engine.toolkit.subagent.subagent_progress_emitter import (
        SubagentProgressEmitter,
    )
    from prismclaw.schema.config.config import SubAgentConfig
    from prismclaw.schema.llm.types import LLMClient
    from prismclaw.schema.storage.storage_types import MemoryStore, SessionStore


class _InstanceBase:
    """Declares the state and cross-mixin helpers of ``AgentInstance``."""

    # Set in LifecycleMixin.__init__
    _run_id: str
    _manifest: AgentManifest
    _parent_ctx: ToolContext
    _tools: ToolRegistry
    _llm: LLMClient
    _settings: SubAgentConfig
    _memory_store: MemoryStore | None
    _session_store: SessionStore | None
    _audit: logging.Logger
    _default_model: str | None
    _channel: str
    _skill_runtime: SkillRuntime | None
    _is_fork: bool
    _tool_timeout_s: float
    _run_context: SubagentRunContext | None
    _status: str
    _mcp_servers: list[Any]
    _registered_hooks: list[Any]
    _task: str | None
    _start_time: float | None
    _created_at: float
    _outcome: str | None
    _turns: int
    _tokens_input: int
    _tokens_output: int
    _model_used: str | None

    # Defined in ContextMixin — declared here for cross-mixin type resolution
    async def _build_context(
        self,
        task: str,
        context_snapshot: SubagentContextSnapshot | dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    def _build_sub_context(self) -> ToolContext:
        raise NotImplementedError

    def _resolve_model(self) -> str:
        raise NotImplementedError

    def _resolve_timeout(self) -> float:
        raise NotImplementedError

    async def _collect_memory_injection(
        self,
        *,
        parent_ctx: ToolContext,
        task: str,
        manifest: AgentManifest,
    ) -> str:
        raise NotImplementedError

    def _resolve_preloaded_skill_context(
        self,
        *,
        parent_ctx: ToolContext,
        manifest: AgentManifest,
    ) -> PreloadedSkillContext:
        raise NotImplementedError

    # Defined in LoopMixin — declared here for cross-mixin type resolution
    def _settle_tool_surface(
        self,
        *,
        sub_ctx: ToolContext,
    ) -> SettledSubagentToolSurface:
        raise NotImplementedError

    async def _dispatch_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
        *,
        sub_ctx: ToolContext,
        parent_ctx: ToolContext,
        manifest: AgentManifest,
    ) -> list[ToolResult]:
        raise NotImplementedError

    @staticmethod
    def _first_tool_name(tool_calls: list[dict[str, Any]]) -> str | None:
        raise NotImplementedError

    @classmethod
    def _summarize_tool_call(cls, tool_calls: list[dict[str, Any]]) -> str | None:
        raise NotImplementedError

    @staticmethod
    def _resolve_progress_emitter(
        parent_ctx: ToolContext,
        manifest: AgentManifest,
    ) -> SubagentProgressEmitter | None:
        raise NotImplementedError

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
        raise NotImplementedError

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
        raise NotImplementedError

    @staticmethod
    def _extract_last_assistant_text(messages: list[dict[str, Any]]) -> str | None:
        raise NotImplementedError

    @staticmethod
    def _count_assistant_turns(messages: list[dict[str, Any]]) -> int:
        raise NotImplementedError
