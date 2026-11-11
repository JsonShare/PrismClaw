"""Auto-extracted Mixin from instance.py — DO NOT edit method signatures."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from prismclaw.engine.execution.handoff.contract import normalize_handoff_request_payload
from prismclaw.engine.subagent.core._subagent_loop_runner import run_subagent_turn_loop
from prismclaw.engine.subagent.core.contract import (
    normalize_result_envelope_text,
    parse_subagent_result_envelope,
)
from prismclaw.engine.subagent.prompting import (
    compose_subagent_system_prompt,
)
from prismclaw.engine.toolkit.registry.tool_classification import is_search_intent_tool
from prismclaw.schema.content.content_types import (
    HandoffRequestPayload,
    PersistenceArtifact,
    SubagentEnvelopeArtifact,
    TextBlock,
    ToolResult,
)
from prismclaw.schema.content.subagent import EvidenceRef, SubagentContextSnapshot, SubagentResultEnvelope
from prismclaw.engine.subagent.core.evidence_inference import infer_domain_fields
from prismclaw.engine.subagent.core._result_types import (
    AgentRunResult,
    _render_terminal_result_text,
)
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext, error_result

logger = logging.getLogger(__name__)

class LifecycleMixin:
    def __init__(
        self,
        *,
        run_id: str,
        manifest: Any,  # AgentManifest
        parent_ctx: ToolContext,
        tool_registry: Any,  # ToolRegistry
        llm_client: Any,
        settings: Any,  # SubAgentSettings
        memory_store: Any = None,
        session_store: Any = None,  # SessionStore for loading parent session
        audit_logger: logging.Logger | None = None,
        default_model: str | None = None,
        tool_approval_hook: Any = None,  # deprecated: kept for signature compat, unused
        decision_audit_logger: Any = None,
        channel: str = "web",
        skill_runtime: Any = None,
        is_fork: bool = False,
        # 单工具时限：由 SubAgentExecutor 从 agent 配置 timeouts.tool_seconds 透传
        tool_timeout_s: float = 120.0,
    ) -> None:
        self._run_id = run_id
        self._manifest = manifest
        self._parent_ctx = parent_ctx
        self._tools = tool_registry
        self._llm = llm_client
        self._settings = settings
        self._memory_store = memory_store
        self._session_store = session_store
        self._audit = audit_logger or logging.getLogger("prismclaw.audit.subagent")
        self._default_model = default_model
        self._decision_audit = decision_audit_logger
        self._channel = channel
        self._skill_runtime = skill_runtime
        self._is_fork = is_fork
        self._tool_timeout_s = tool_timeout_s

        # -- state --
        self._status: str = "created"
        self._mcp_servers: list[Any] = []
        self._registered_hooks: list[Any] = []
        self._task: str | None = None  # Current task description

        # -- completion tracking --
        self._completed: bool = False
        self._run_result: AgentRunResult | None = None
        self._on_complete_callbacks: list[Any] = []

        # -- execution state --
        self._start_time: float | None = None
        self._created_at: float = time.time()
        self._outcome: str | None = None
        self._turns: int = 0
        self._tokens_input: int = 0
        self._tokens_output: int = 0
        self._model_used: str | None = None

    # -- public getters ---------------------------------------------------

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def status(self) -> str:
        return self._status

    @property
    def manifest(self) -> Any:
        return self._manifest

    # -- completion callback -----------------------------------------------

    def on_complete(self, callback: Any) -> None:
        """
        Register a callback to be called when this instance completes.

        The callback will be called with the AgentRunResult as argument.
        If the instance has already completed, the callback is called immediately.

        Args:
            callback: Callable that takes (AgentRunResult) -> None
        """
        if self._completed and self._run_result is not None:
            # Already completed, call immediately
            asyncio.create_task(self._call_callback(callback))
        else:
            self._on_complete_callbacks.append(callback)

    async def _call_callback(self, callback: Any) -> None:
        """Call a single callback."""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(self._run_result)
            else:
                callback(self._run_result)
        except Exception as e:
            logger.error(
                "Error in on_complete callback: run_id=%s, error=%s",
                self._run_id,
                e,
                exc_info=True,
            )

    async def _execute_callbacks(self) -> None:
        """Execute all registered callbacks."""
        self._completed = True
        for callback in self._on_complete_callbacks:
            await self._call_callback(callback)
        self._on_complete_callbacks.clear()

    # -- core -----------------------------------------------------------


    async def run(
        self,
        task: str,
        *,
        call_id: str = "",
        context_snapshot: SubagentContextSnapshot | dict[str, Any] | None = None,
    ) -> "AgentRunResult":
        """Execute the sub-agent task.

        This method preserves the production `SubAgentExecutor.run()`
        behaviour while owning resource cleanup and lifecycle tracking.
        """
        self._status = "running"
        self._task = task  # Save task description for resumption
        started_at = time.monotonic()

        # Admission: max depth
        current_depth = self._parent_ctx.subagent_depth
        max_depth = self._settings.max_subagent_depth
        if current_depth >= max_depth:
            self._status = "failed"
            return AgentRunResult(
                tool_result=ToolResult(
                    tool_call_id=call_id,
                    content=[
                        TextBlock(
                            text=(
                                f"[拒绝准入] 子代理嵌套深度 "
                                f"已超过上限（{max_depth}）。"
                            )
                        )
                    ],
                    is_error=True,
                ),
                outcome="depth_exceeded",
                turns=0,
                tokens_input=0,
                tokens_output=0,
                duration_seconds=0.0,
                model_used="none",
            )

        # Build sub-context
        sub_ctx = self._build_sub_context()

        # Approval hook inheritance removed — SafetyGate is now the sole gate
        if self._decision_audit is None:
            self._decision_audit = self._parent_ctx.decision_audit_logger
        if self._parent_ctx.channel is not None:
            self._channel = self._parent_ctx.channel

        # Preload skills
        try:
            preload_ctx = self._resolve_preloaded_skill_context(
                parent_ctx=self._parent_ctx,
                manifest=self._manifest,
            )
        except Exception as exc:
            logger.warning(
                "subagent.declared_invoke_sequence_resolution_failed name=%s",
                self._manifest.name,
                exc_info=True,
            )
            self._status = "failed"
            return AgentRunResult(
                tool_result=error_result(
                    call_id,
                    f"子代理 preload_skills 解析失败：{exc}",
                ),
                outcome="error",
                turns=0,
                tokens_input=0,
                tokens_output=0,
                duration_seconds=0.0,
                model_used=self._resolve_model(),
            )

        effective_model = self._resolve_model()
        # fork 是主流程的延续分支：与主流程「无 max_iterations / run_seconds」的
        # 进度驱动哲学对齐，不施加 max_turns 与 timeout_s 两层机械上限
        # （timeout_s 见 _context_mixin._resolve_timeout 的 fork 分支）。
        # max_turns=0 被 run_subagent_turn_loop 解释为「无迭代上限」，退出条件只剩：
        # 父级 abort / LLM 返回无工具调用（正常完成）/ 单个工具的时限。非 fork 的孤立
        # 派生子代理保持原有限额不变。
        if getattr(self, "_is_fork", False):
            max_turns = 0
        else:
            max_turns = self._manifest.max_turns or self._settings.default_max_turns
        timeout_s = self._resolve_timeout()
        allowed_tools = self._resolve_allowed_tools(
            manifest=self._manifest,
            preload_ctx=preload_ctx,
        )
        permission_tier = str(
            self._parent_ctx.mode or "work"
        )
        settled_tool_surface = self._settle_tool_surface(
            allowed_tools,
            sub_ctx=sub_ctx,
            mode=permission_tier,
        )
        tool_schemas = self._tools.list_for_llm(settled_tool_surface.visible_tool_names)
        memory_block = await self._collect_memory_injection(
            parent_ctx=self._parent_ctx,
            task=task,
            manifest=self._manifest,
        )
        system_prompt = compose_subagent_system_prompt(
            system_prompt=self._manifest.system_prompt,
            preload_catalog_block=preload_ctx.catalog_block,
            preload_instructions_block=preload_ctx.instructions_block,
            memory_block=memory_block,
        )

        # Build context based on context_mode
        messages = await self._build_context(task, context_snapshot)

        try:
            outcome, turns, usage = await run_subagent_turn_loop(
                self,
                messages=messages,
                system_prompt=system_prompt,
                model=effective_model,
                max_turns=max_turns,
                timeout_s=timeout_s,
                tool_surface=settled_tool_surface,
                tool_schemas=tool_schemas,
                sub_ctx=sub_ctx,
                parent_ctx=self._parent_ctx,
                manifest=self._manifest,
            )
        except Exception:
            logger.error("AgentInstance loop crashed", exc_info=True)
            outcome = "error"
            turns = self._count_assistant_turns(messages)
            usage = {}
            self._status = "failed"
        finally:
            await self._cleanup_light()

        duration = time.monotonic() - started_at
        final_text = self._extract_last_assistant_text(messages) or ""
        pending_handoff = (
            normalize_handoff_request_payload(sub_ctx.pending_handoff)
            if outcome == "success"
            else None
        )
        # 自动从 tool call 历史中提取 evidence（不论 success 与否，
        # 超时/错误退出时已执行的工具调用仍可能包含关键信息）
        auto_evidence = _collect_evidence_from_messages(messages)

        if outcome == "success":
            normalized_result_text = normalize_result_envelope_text(final_text)
            envelope = parse_subagent_result_envelope(normalized_result_text)

            if envelope is not None:
                envelope.evidence = auto_evidence
            elif auto_evidence:
                envelope = SubagentResultEnvelope(
                    summary=normalized_result_text,
                    evidence=auto_evidence,
                )
        else:
            normalized_result_text = _render_terminal_result_text(
                outcome=outcome,
                turns=turns,
                timeout_s=timeout_s,
                final_text=final_text,
            )
            envelope = None
            # 非 success 但有 evidence 时，包装为 envelope 携带证据
            if auto_evidence:
                envelope = SubagentResultEnvelope(
                    summary=normalized_result_text,
                    evidence=auto_evidence,
                    confidence_assessment="low",
                )

        tool_result = ToolResult(
            tool_call_id=call_id,
            content=[TextBlock(text=normalized_result_text)],
            is_error=outcome != "success",
            persistence_artifact=(
                None
                if pending_handoff is None
                else PersistenceArtifact(
                    artifact_type="handoff_request",
                    entry_type="handoff_request",
                    handoff_request=HandoffRequestPayload.model_validate(pending_handoff),
                )
            ),
            subagent_envelope=SubagentEnvelopeArtifact(
                final_text=final_text or None,
                result_envelope=(
                    envelope if isinstance(envelope, SubagentResultEnvelope) else None
                ),
                handoff_request=(
                    HandoffRequestPayload.model_validate(pending_handoff)
                    if pending_handoff is not None
                    else None
                ),
            ),
        )

        self._status = "completed" if outcome == "success" else "failed"

        result = AgentRunResult(
            tool_result=tool_result,
            outcome=outcome,
            turns=turns,
            tokens_input=usage.get("input_tokens", 0),
            tokens_output=usage.get("output_tokens", 0),
            duration_seconds=duration,
            model_used=effective_model,
        )

        # Store result and execute callbacks
        self._run_result = result
        await self._execute_callbacks()

        return result

    # -- internal: context & model -----------------------------------------


    async def _cleanup_light(self) -> None:
        """Full resource cleanup (Phase 5/6 implementation).

        This method is called in the ``finally`` block of ``run()``
        to ensure proper resource cleanup.

        Cleanup steps (per v3 plan):
        1. Cleanup MCP server connections
        2. Cleanup session hooks
        3. Release file state cache
        4. Release Transcript resources
        5. Save transcript for potential resumption
        6. Save state for potential resumption
        """
        logger.debug("agent_instance.cleanup.started run_id=%s", self._run_id)

        # 1. Cleanup MCP server connections
        for server in self._mcp_servers:
            try:
                await server.cleanup()
                logger.debug("agent_instance.cleanup.mcp_server_cleaned server=%s", server)
            except Exception as e:
                logger.warning(
                    "agent_instance.cleanup.mcp_server_cleanup_failed server=%s error=%s",
                    server,
                    e,
                    exc_info=True,
                )

        # 2. Cleanup session hooks
        for hook in self._registered_hooks:
            try:
                hook.unregister()
                logger.debug("agent_instance.cleanup.hook_unregistered hook=%s", hook)
            except Exception as e:
                logger.warning(
                    "agent_instance.cleanup.hook_unreg Failed hook=%s error=%s",
                    hook,
                    e,
                    exc_info=True,
                )

        # 3. Release file state cache
        file_state = self._parent_ctx.read_file_state
        if hasattr(file_state, "clear"):
            try:
                file_state.clear()
                logger.debug("agent_instance.cleanup.file_state_cleared")
            except Exception as e:
                logger.warning(
                    "agent_instance.cleanup.file_state_clear_failed error=%s",
                    e,
                    exc_info=True,
                )

        logger.debug("agent_instance.cleanup.completed run_id=%s", self._run_id)


# ---------------------------------------------------------------------------
# Evidence collection — auto-extract from tool call history
# ---------------------------------------------------------------------------

_TOOL_EVIDENCE_MAP: dict[str, tuple[str, str]] = {
    "read": ("file", "file_path"),
    "grep": ("tool_output", "pattern"),
    "glob": ("tool_output", "pattern"),
    "web_fetch": ("web", "url"),  # legacy builtin (historical sessions)
    "bash": ("tool_output", "command"),
}


def _evidence_spec(tool_name: str) -> tuple[str, str] | None:
    """Evidence (source_type, arg_key) for a tool.

    Configured web-retrieval tools (service name lives in config) resolve
    dynamically to ``("web", "query")``.
    """
    if tool_name in _TOOL_EVIDENCE_MAP:
        return _TOOL_EVIDENCE_MAP[tool_name]
    if is_search_intent_tool(tool_name):
        return ("web", "query")
    return None


def _collect_evidence_from_messages(
    messages: list[dict[str, Any]],
) -> list:
    """从 subagent 的对话历史中自动提取证据引用。

    扫描 messages 中的 assistant tool_use + tool result 对，
    为每次工具调用生成一条 EvidenceRef。
    """

    evidence: list[EvidenceRef] = []

    # 构建 tool_call_id → result_text 索引
    result_map: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        tc_id = msg.get("tool_call_id", "")
        content = msg.get("content", "")
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and "text" in item:
                    parts.append(item["text"])
                elif isinstance(item, str):
                    parts.append(item)
            content = "\n".join(parts)
        if tc_id:
            result_map[tc_id] = str(content)

    # 扫描 assistant 消息中的 tool_calls
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls", [])
        if not isinstance(tool_calls, list):
            continue
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            func = tc.get("function", {})
            if not isinstance(func, dict):
                continue
            tool_name = func.get("name", "")
            evidence_spec = _evidence_spec(tool_name)
            if evidence_spec is None:
                continue

            # 解析 arguments
            raw_args = func.get("arguments", {})
            if isinstance(raw_args, str):
                try:
                    import json
                    raw_args = json.loads(raw_args)
                except Exception:
                    raw_args = {}
            tool_input = raw_args if isinstance(raw_args, dict) else {}

            # 查找对应的 tool result
            tc_id = tc.get("id", "")
            result_text = result_map.get(tc_id, "")
            if not result_text:
                continue

            source_type, path_key = evidence_spec
            source_path = str(tool_input.get(path_key, ""))

            # 截取 quoted_text：最多 2000 字符，保留原始证据（精确引用保留更多原文）
            quoted_text = result_text[:2000] if len(result_text) > 2000 else result_text

            # read 工具可以提取行号信息
            source_location = ""
            if tool_name == "read":
                offset = tool_input.get("offset")
                limit = tool_input.get("limit")
                if offset:
                    source_location = f"L{offset}"
                    if limit:
                        source_location += f"-L{offset + limit}"

            evidence.append(EvidenceRef(
                source_type=source_type,
                source_path=source_path,
                source_location=source_location,
                quoted_text=quoted_text,
                confidence="high",
                domain_extensions=infer_domain_fields(source_type, source_path, result_text),
            ))

    return evidence



