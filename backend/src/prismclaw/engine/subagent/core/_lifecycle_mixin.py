"""Auto-extracted Mixin from instance.py — DO NOT edit method signatures."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from prismclaw.engine.execution.dispatch.turn_utils import strip_noise
from prismclaw.engine.execution.handoff.contract import normalize_handoff_request_payload
from prismclaw.engine.subagent.core._instance_base import _InstanceBase
from prismclaw.engine.subagent.core._result_types import (
    AgentRunResult,
)
from prismclaw.engine.subagent.core._subagent_loop_runner import run_subagent_turn_loop
from prismclaw.engine.subagent.core.contract import (
    normalize_result_envelope_text,
    parse_subagent_result_envelope,
)
from prismclaw.engine.subagent.core.evidence_inference import infer_domain_fields
from prismclaw.engine.subagent.core.run_context import SubagentRunContext
from prismclaw.engine.subagent.definition.manifest import AgentManifest
from prismclaw.engine.subagent.prompting import (
    compose_subagent_system_prompt,
)
from prismclaw.engine.toolkit.registry.registry import ToolRegistry
from prismclaw.engine.toolkit.registry.tool_classification import is_search_intent_tool
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext, error_result
from prismclaw.schema.content.content_types import (
    HandoffRequestPayload,
    PersistenceArtifact,
    SubagentEnvelopeArtifact,
    TextBlock,
    ToolResult,
)
from prismclaw.schema.content.subagent import (
    EvidenceRef,
    SubagentContextSnapshot,
    SubagentResultEnvelope,
)

if TYPE_CHECKING:
    from prismclaw.engine.execution.skills.runtime import SkillRuntime
    from prismclaw.schema.config.config import SubAgentConfig
    from prismclaw.schema.llm.types import LLMClient
    from prismclaw.schema.storage.storage_types import MemoryStore, SessionStore

logger = logging.getLogger(__name__)

# 非正常终止时合成的工具结果摘要总字符上限，防止并行多 agent 场景下
# 单条任务记录与父上下文膨胀。
_SYNTHESIZED_RESULT_BUDGET = 6000

# 预算内为截断标注预留的空间
_RESULT_TAIL_RESERVE = 40


class LifecycleMixin(_InstanceBase):
    def __init__(
        self,
        *,
        run_id: str,
        manifest: AgentManifest,
        parent_ctx: ToolContext,
        tool_registry: ToolRegistry,
        llm_client: LLMClient,
        settings: SubAgentConfig,
        memory_store: MemoryStore | None = None,
        session_store: SessionStore | None = None,  # SessionStore for loading parent session
        audit_logger: logging.Logger | None = None,
        default_model: str | None = None,
        channel: str = "web",
        skill_runtime: SkillRuntime | None = None,
        is_fork: bool = False,
        # 单工具时限：由 SubAgentExecutor 从 agent 配置 timeouts.tool_seconds 透传
        tool_timeout_s: float = 120.0,
        run_context: SubagentRunContext | None = None,
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
        self._channel = channel
        self._skill_runtime = skill_runtime
        self._is_fork = is_fork
        self._tool_timeout_s = tool_timeout_s
        self._run_context = run_context

        # -- state --
        self._status: str = "created"
        self._mcp_servers: list[Any] = []
        self._registered_hooks: list[Any] = []
        self._task: str | None = None  # Current task description

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
    def manifest(self) -> AgentManifest:
        return self._manifest

    # -- core -----------------------------------------------------------

    async def run(
        self,
        task: str,
        *,
        call_id: str = "",
        context_snapshot: SubagentContextSnapshot | dict[str, Any] | None = None,
    ) -> AgentRunResult:
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
                        TextBlock(text=(f"[拒绝准入] 子代理嵌套深度 已超过上限（{max_depth}）。"))
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

        # Approval hook inheritance removed — tool-level gating lives in the parent runner
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
                "subagent.preload_skills_resolution_failed name=%s",
                self._manifest.name,
                exc_info=True,
            )
            self._status = "failed"
            return AgentRunResult(
                tool_result=error_result(
                    call_id,
                    f"[子代理执行失败：preload_skills 解析失败 | {exc}]",
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
            available=set(self._tools.names()),
        )
        settled_tool_surface = self._settle_tool_surface(
            allowed_tools,
            sub_ctx=sub_ctx,
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

        # 协作内运行的 run（one_shot 与 persistent）都从子 SessionTree 重建上下文；
        # 只有绕过 collaboration 的直连调用走原有的一次性上下文构造。
        if self._run_context is not None:
            messages = self._run_context.build_messages()
        else:
            messages = await self._build_context(task, context_snapshot)

        if self._run_context is not None:
            self._run_context.start_heartbeat()
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
            if self._run_context is not None:
                await self._run_context.stop_heartbeat()
            await self._cleanup_light()

        duration = time.monotonic() - started_at
        # 子代理的最终文本会作为 tool_result 回给父级，并可能进入用户可见正文。
        # 与主循环出口同源清洗：<thinking> 草稿与原生工具调用标记都不能外泄。
        final_text = strip_noise(self._extract_last_assistant_text(messages) or "")
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
            synthesized = _synthesize_tool_results(messages)
            detail = final_text.strip()
            has_substance = bool(detail or synthesized)

            if outcome == "max_turns_exceeded":
                reason = f"已达最大轮次上限（{turns} 轮）"
                failure_prefix = f"[子代理{reason}]"
            elif outcome == "timeout":
                reason = f"超时：{timeout_s:g}s 后共执行 {turns} 轮"
                failure_prefix = f"[子代理{reason}]"
            elif outcome == "aborted":
                reason = "已被父级中止"
                failure_prefix = "[子代理已被父级中止]"
            else:
                reason = f"执行失败：outcome={outcome}"
                failure_prefix = f"[子代理{reason}]"

            # 有内容交付时使用非失败前缀：结果可被父级与 reduce 工具正常采用，
            # 避免部分完成的产出被 first_success 当作错误丢弃。
            prefix = f"[子代理部分完成：{reason}]" if has_substance else failure_prefix

            parts = [prefix]
            if detail:
                parts.append(detail)
            if synthesized:
                parts.append(synthesized)
            normalized_result_text = "\n\n".join(parts)

            # 证据只走结构化字段，不在纯文本中重复索引。
            envelope = None
            if auto_evidence or has_substance:
                envelope = SubagentResultEnvelope(
                    summary=normalized_result_text,
                    evidence=auto_evidence,
                )

        tool_result = ToolResult(
            tool_call_id=call_id,
            content=[TextBlock(text=normalized_result_text)],
            # 非 success 但有内容交付时 envelope 存在 → is_error=False
            is_error=(outcome != "success") and (envelope is None),
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
                agent_name=self._manifest.name,
                agent_source=getattr(self._manifest, "source", None),
                agent_id=(self._run_context.agent_id if self._run_context is not None else None),
                run_id=self._run_id,
                child_session_id=(
                    self._run_context.child_session_id if self._run_context is not None else None
                ),
                lifecycle=(self._run_context.lifecycle if self._run_context is not None else None),
                outcome=outcome,
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


def _build_tool_result_map(
    messages: list[dict[str, Any]],
) -> dict[str, tuple[str, str]]:
    """构建 tool_call_id → (tool_name, content_text) 映射。

    公共基础设施：被 _collect_evidence_from_messages 和
    _synthesize_tool_results 共用，避免重复遍历 messages + 重复
    content 形态归一化（list[TextBlock] vs str）。
    """
    # 先从 assistant tool_calls 提取 tc_id → tool_name
    tc_id_to_name: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            tc_id = tc.get("id", "")
            fn = tc.get("function", {})
            if isinstance(fn, dict):
                tc_id_to_name[tc_id] = fn.get("name", "unknown")

    # 再从 tool 消息收集 content，合并 tool_name
    result_map: dict[str, tuple[str, str]] = {}
    for msg in messages:
        if msg.get("role") != "tool":
            continue
        tc_id = msg.get("tool_call_id", "")
        if not tc_id:
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(item.get("text", "") or "")
                else:
                    parts.append(str(item))
            content = "\n".join(parts)
        tool_name = tc_id_to_name.get(tc_id, "unknown")
        # 保留原始 content（不 strip）——evidence 需要精确引用原始内容，
        # synthesize 自行 strip 后判断空内容
        result_map[tc_id] = (tool_name, str(content))

    return result_map


def _synthesize_tool_results(
    messages: list[dict[str, Any]],
) -> str:
    """从已执行的 tool result 合成可读文本摘要。

    覆盖所有工具类型（不限 search-intent），供 sub-agent
    非自然终止（max_turns/timeout/abort）时交付已收集内容。
    输出按工具首次调用顺序排列，总量受 ``_SYNTHESIZED_RESULT_BUDGET`` 约束。

    与 _collect_evidence_from_messages 互补：
      - evidence：只收录 search-intent 工具，生成结构化 EvidenceRef
      - synthesize：覆盖所有工具，生成纯文本可读摘要

    无 tool 消息时返回空字符串（turns==0 场景）。
    """
    from collections import defaultdict

    result_map = _build_tool_result_map(messages)
    if not result_map:
        return ""

    by_tool: dict[str, list[str]] = defaultdict(list)
    for tool_name, raw_content in result_map.values():
        content = raw_content.strip()
        if not content:
            continue
        _max_head = 600
        _max_tail = 300
        _overlap = 20
        total = len(content)
        if total <= _max_head + _max_tail + _overlap:
            snippet = content
        else:
            snippet = content[:_max_head] + "\n...[截断]...\n" + content[-_max_tail:]
        by_tool[tool_name].append(snippet)

    total_items = sum(len(items) for items in by_tool.values())
    parts: list[str] = []
    used = 0
    emitted = 0
    truncated = False

    for tool_name, snippets in by_tool.items():
        for index, snippet in enumerate(snippets, 1):
            chunk = f"[工具: {tool_name}, 第{index}次]\n{snippet}"
            separator = 2 if parts else 0
            if used + separator + len(chunk) + _RESULT_TAIL_RESERVE > _SYNTHESIZED_RESULT_BUDGET:
                truncated = True
                break
            parts.append(chunk)
            used += separator + len(chunk)
            emitted += 1
        if truncated:
            break

    text = "\n\n".join(parts)
    if truncated:
        text += f"\n\n[已省略 {total_items - emitted} 条工具结果]"
    return text


def _collect_evidence_from_messages(
    messages: list[dict[str, Any]],
) -> list[EvidenceRef]:
    """从 subagent 的对话历史中自动提取证据引用。

    扫描 messages 中的 assistant tool_use + tool result 对，
    为每次 search-intent 工具调用生成一条 EvidenceRef。
    """

    evidence: list[EvidenceRef] = []
    result_map = _build_tool_result_map(messages)

    # 扫描 assistant 消息中的 tool_calls（解析 arguments 用于 source_path）
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

            # 从公共 map 查找 tool result
            tc_id = tc.get("id", "")
            entry = result_map.get(tc_id)
            if entry is None:
                continue
            _, result_text = entry
            if not result_text:
                continue

            source_type, path_key = evidence_spec
            source_path = str(tool_input.get(path_key, ""))

            # 截取 quoted_text：最多 2000 字符（精确引用保留更多原文）
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

            evidence.append(
                EvidenceRef(
                    source_type=source_type,
                    source_path=source_path,
                    source_location=source_location,
                    quoted_text=quoted_text,
                    confidence="high",
                    domain_extensions=infer_domain_fields(source_type, source_path, result_text),
                )
            )

    return evidence
