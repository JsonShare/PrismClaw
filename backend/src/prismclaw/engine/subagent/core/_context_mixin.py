"""Auto-extracted Mixin from instance.py — DO NOT edit method signatures."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from prismclaw.engine.subagent.core.constants import TIMEOUT_HARD_CEILING_S
from prismclaw.engine.subagent.core.contract import (
    coerce_context_snapshot,
    render_task_envelope,
)
from prismclaw.engine.subagent.prompting import (
    coerce_allowed_tool_names,
    resolve_preloaded_skill_context,
)
from prismclaw.engine.subagent.shared_types import PreloadedSkillContext
from prismclaw.engine.execution.request.packing import inject_cache_control_breakpoints
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext
from prismclaw.schema.content.subagent import SubagentContextSnapshot

logger = logging.getLogger(__name__)

class ContextMixin:
    async def _build_context(
        self,
        task: str,
        context_snapshot: SubagentContextSnapshot | dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Build context based on context_mode.

        Phase 3 implementation:
        - context_mode="isolated": zero context (current behavior)
        - context_mode="inherit": inherit parent context (Prompt Cache sharing)
        """
        if self._manifest.context_mode == "inherit":
            return await self._build_inherit_messages(task, context_snapshot)
        else:
            return self._build_isolated_messages(task, context_snapshot)

    def _build_isolated_messages(
        self,
        task: str,
        context_snapshot: SubagentContextSnapshot | dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Build isolated mode messages (zero context, Fresh Agent).

        This is the current behavior: just the task envelope.
        """
        if context_snapshot in (None, {}):
            snapshot = SubagentContextSnapshot(task_goal=task)
        else:
            snapshot = coerce_context_snapshot(context_snapshot, fallback_task=task)
        enhanced_task = render_task_envelope(task, snapshot)

        return [{"role": "user", "content": enhanced_task}]

    async def _build_inherit_messages(
        self,
        task: str,
        context_snapshot: SubagentContextSnapshot | dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Build inherit mode messages (Prompt Cache sharing).

        Phase 3 complete implementation:
        - Load parent session messages from session_store
        - Include parent context for Prompt Cache sharing
        - Add cache_control markers for Prompt Cache sharing

        Implementation:
        1. Load parent session messages from session_store
        2. Convert SessionEntry messages to LLM message format
        3. Add child directive with task description
        4. Apply inject_cache_control_breakpoints for Prompt Cache sharing

        Configuration (from AgentManifest):
        - max_inherit_messages: Maximum number of messages to inherit

        Error Handling:
        - If session_store is None, log warning and return isolated context
        - If session_store.load() fails, log error and return isolated context
        - If parent session not found, log warning and return isolated context
        """
        messages = []

        # 1. Validate session_store availability
        if self._session_store is None:
            logger.warning(
                "agent_instance.build_inherit_messages.session_store_is_none "
                "context_mode='inherit' requires session_store for Prompt Cache sharing. "
                "Falling back to isolated context."
            )
            # Fallback: return isolated context
            directive = self._build_child_directive(task)
            if context_snapshot not in (None, {}):
                snapshot = coerce_context_snapshot(context_snapshot, fallback_task=task)
                context_info = self._format_context_snapshot(snapshot)
                directive = f"{directive}\n\n{context_info}"
            return [{"role": "user", "content": directive}]

        if not self._parent_ctx.session_id:
            logger.warning(
                "agent_instance.build_inherit_messages.parent_session_id_empty "
                "context_mode='inherit' requires parent session_id. "
                "Falling back to isolated context."
            )
            # Fallback: return isolated context
            directive = self._build_child_directive(task)
            if context_snapshot not in (None, {}):
                snapshot = coerce_context_snapshot(context_snapshot, fallback_task=task)
                context_info = self._format_context_snapshot(snapshot)
                directive = f"{directive}\n\n{context_info}"
            return [{"role": "user", "content": directive}]

        # 2. Load parent session messages from session_store
        try:
            parent_session = await self._session_store.load(self._parent_ctx.session_id)
            if parent_session is None:
                logger.warning(
                    "agent_instance.build_inherit_messages.parent_session_not_found session_id=%s",
                    self._parent_ctx.session_id,
                )
                # Fallback: return isolated context
                directive = self._build_child_directive(task)
                if context_snapshot not in (None, {}):
                    snapshot = coerce_context_snapshot(context_snapshot, fallback_task=task)
                    context_info = self._format_context_snapshot(snapshot)
                    directive = f"{directive}\n\n{context_info}"
                return [{"role": "user", "content": directive}]

            # Extract messages from SessionTree
            loaded_messages = self._extract_messages_from_session(
                parent_session,
            )

            # Apply message limit
            max_messages = self._manifest.max_inherit_messages
            if len(loaded_messages) > max_messages:
                loaded_messages = loaded_messages[-max_messages:]
                logger.info(
                    "agent_instance.build_inherit_messages.truncated messages_count=%d max=%d",
                    len(loaded_messages),
                    max_messages,
                )

            messages.extend(loaded_messages)
            logger.info(
                "agent_instance.build_inherit_messages.loaded session_id=%s messages_count=%d",
                self._parent_ctx.session_id,
                len(loaded_messages),
            )
        except Exception as e:
            logger.error(
                "agent_instance.build_inherit_messages.failed load_parent_session_error=%s",
                e,
                exc_info=True,
            )
            # Fallback: return isolated context
            directive = self._build_child_directive(task)
            if context_snapshot not in (None, {}):
                snapshot = coerce_context_snapshot(context_snapshot, fallback_task=task)
                context_info = self._format_context_snapshot(snapshot)
                directive = f"{directive}\n\n{context_info}"
            return [{"role": "user", "content": directive}]

        # 3. Add child directive with task description
        directive = self._build_child_directive(task)

        # If context_snapshot has detailed info, include it
        if context_snapshot not in (None, {}):
            snapshot = coerce_context_snapshot(context_snapshot, fallback_task=task)
            context_info = self._format_context_snapshot(snapshot)
            directive = f"{directive}\n\n{context_info}"

        messages.append({"role": "user", "content": directive})

        # 4. Apply inject_cache_control_breakpoints for Prompt Cache sharing
        messages = inject_cache_control_breakpoints(messages)

        logger.info(
            "agent_instance.build_inherit_messages.complete total_messages=%d task=%s",
            len(messages),
            task[:50] if len(task) > 50 else task,
        )

        return messages


    def _extract_messages_from_session(
        self,
        session_tree: Any,
    ) -> list[dict[str, Any]]:
        """Extract messages from SessionTree.

        SessionTree structure:
        - order: list of entry IDs
        - entries: dict of entry_id -> entry

        Entry can be:
        - dict with type="message"
        - pydantic model with type="message"

        Args:
            session_tree: SessionTree object or dict

        Returns:
            List of messages in LLM API format
        """
        messages = []

        # Handle both dict and object session_tree
        if isinstance(session_tree, dict):
            order = session_tree.get("order", [])
            entries = session_tree.get("entries", {})
        else:
            order = getattr(session_tree, "order", [])
            entries = getattr(session_tree, "entries", {})

        for entry_id in order:
            entry = entries.get(entry_id)
            if entry is None:
                continue

            # Extract message from entry
            msg = self._extract_message_from_entry(entry)
            if msg is not None:
                # Tool messages are never inherited to keep subagent context clean
                if msg["role"] == "tool":
                    continue

                messages.append(msg)

        return messages

    def _extract_message_from_entry(self, entry: Any) -> dict[str, Any] | None:
        """Extract a single message from a session entry.

        Returns None if the entry is not a message entry.

        Converts SessionEntry to LLM message format:
        - MessageEntry with content=str -> {"role": ..., "content": str}
        - MessageEntry with content=list[ContentBlock] -> {"role": ..., "content": list[dict]}
        """
        # Handle dict entry
        if isinstance(entry, dict):
            if entry.get("type") != "message":
                return None

            role = entry.get("role")
            content = entry.get("content")
            tool_calls = entry.get("tool_calls")

            if role is None:
                return None

            # Convert to LLM message format
            msg = {"role": role, "content": content if content is not None else ""}
            if tool_calls is not None:
                msg["tool_calls"] = tool_calls
            if entry.get("tool_call_id") is not None:
                msg["tool_call_id"] = entry["tool_call_id"]

            return msg

        # Handle pydantic model entry (SessionEntry)
        if hasattr(entry, "type"):
            if entry.type != "message":
                return None

            role = getattr(entry, "role", None)
            content = getattr(entry, "content", None)
            tool_calls = getattr(entry, "tool_calls", None)

            if role is None:
                return None

            # Convert content to LLM message format
            # Content can be: str | list[ContentBlock]
            converted_content = self._convert_content_to_llm_format(content)

            msg = {"role": role, "content": converted_content}
            if tool_calls is not None:
                msg["tool_calls"] = tool_calls
            if getattr(entry, "tool_call_id", None) is not None:
                msg["tool_call_id"] = entry.tool_call_id

            return msg

        return None

    def _convert_content_to_llm_format(self, content: Any) -> Any:
        """Convert MessageEntry.content to LLM API format.

        MessageEntry.content can be:
        - str: keep as-is
        - list[ContentBlock]: convert to list[dict[str, Any]]
        """
        if content is None:
            return ""

        if isinstance(content, str):
            return content

        if isinstance(content, list):
            # Convert list[ContentBlock] to list[dict[str, Any]]
            converted = []
            for block in content:
                if isinstance(block, dict):
                    converted.append(block)
                elif hasattr(block, "model_dump"):
                    # Pydantic model
                    converted.append(block.model_dump(mode="json"))
                elif hasattr(block, "dict"):
                    # Older pydantic
                    converted.append(block.dict())
                else:
                    # Unknown type, try to convert to dict
                    converted.append({"type": "text", "text": str(block)})
            return converted

        # Unknown type
        return str(content)

    def _build_child_directive(self, task: str) -> str:
        """Build directive for child agent (inherit mode).

        This is the task description formatted for an inherited context.
        """
        return f"[Inherited Context] 你的任务：{task}\n\n请使用继承的上下文执行任务，并简洁地报告结果。"

    @staticmethod
    def _format_context_snapshot(snapshot: SubagentContextSnapshot) -> str:
        """Format context snapshot as text."""
        parts = []
        if snapshot.task_goal:
            parts.append(f"任务目标：{snapshot.task_goal}")
        if snapshot.done_so_far:
            parts.append(f"已完成：{snapshot.done_so_far}")
        if snapshot.key_findings:
            parts.append(f"关键发现：{snapshot.key_findings}")
        if snapshot.constraints:
            parts.append(f"约束：{snapshot.constraints}")
        return "\n".join(parts)

    def _build_sub_context(self) -> ToolContext:
        from dataclasses import replace as _replace
        sub_session_id = f"{self._parent_ctx.session_id}#sub:{uuid.uuid4().hex[:12]}"
        return _replace(
            self._parent_ctx,
            session_id=sub_session_id,
            subagent_depth=self._parent_ctx.subagent_depth + 1,
            subagent_name=self._manifest.name,
            subagent_manifest=self._manifest,
            parent_session_id=self._parent_ctx.session_id,
        )

    def _resolve_model(self) -> str:
        if self._manifest.model:
            return self._manifest.model
        routed = self._settings.model_routing.get(self._manifest.name)
        if routed:
            return routed
        if self._default_model:
            return self._default_model
        raise RuntimeError(f"sub-agent {self._manifest.name!r}: no model resolved")

    def _resolve_timeout(self) -> float:
        # fork 是主流程的延续分支，与主流程同受「进度驱动终止」哲学约束——
        # 不施加墙钟上限（对齐主流程禁用 run_seconds / max_iterations 的决定）。
        # 时长兜底由 max_turns + 父级 abort 承担；非 fork 的孤立派生子代理仍按
        # manifest / 默认 timeout_seconds 封顶，避免挂死。
        if getattr(self, "_is_fork", False):
            return 0.0
        configured = self._manifest.timeout_seconds or self._settings.default_timeout_seconds
        return min(configured, TIMEOUT_HARD_CEILING_S)

    def _resolve_allowed_tools(
        self,
        *,
        manifest: Any,
        preload_ctx: PreloadedSkillContext,
    ) -> set[str] | None:
        manifest_allowed = coerce_allowed_tool_names(manifest.allowed_tools)
        preload_allowed = preload_ctx.allowed_tools_limit
        if manifest_allowed is None:
            return set(preload_allowed) if preload_allowed is not None else None
        if preload_allowed is None:
            return manifest_allowed
        return manifest_allowed & preload_allowed

    async def _collect_memory_injection(
        self,
        *,
        parent_ctx: ToolContext,
        task: str,
        manifest: Any,
    ) -> str:
        if self._memory_store is None:
            return ""
        searcher = getattr(self._memory_store, "search", None)
        if searcher is None:
            return ""
        try:
            results = await searcher(parent_ctx.session_id, task)
        except Exception:
            logger.warning(
                "subagent.memory_search_failed session=%s",
                parent_ctx.session_id,
                exc_info=True,
            )
            return ""
        if not results:
            return ""
        return self._render_memory_block(results)

    @staticmethod
    def _render_memory_block(results: Any) -> str:
        if isinstance(results, str):
            return results
        if isinstance(results, list):
            try:
                items = "\n".join(f"- {str(r)}" for r in results)
            except Exception:
                return ""
            return f"<memory>\n{items}\n</memory>"
        return ""

    def _resolve_preloaded_skill_context(
        self,
        *,
        parent_ctx: ToolContext,
        manifest: Any,
    ) -> PreloadedSkillContext:
        return resolve_preloaded_skill_context(
            parent_ctx=parent_ctx,
            manifest=manifest,
            skill_runtime=self._skill_runtime,
        )

    # -- internal: main loop -----------------------------------------------------------


