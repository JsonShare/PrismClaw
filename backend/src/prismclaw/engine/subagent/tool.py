"""``sub_agent`` tool: the parent-agent-side gate that spawns sub-agents.

Per ``openspec/changes/add-subagent-system/specs/builtin-tools/spec.md`` ADDED
Requirement "SubAgentTool is a builtin tool with ``tool_class='write'``" and
``subagent-system/spec.md`` ADDED Requirement "SubAgentTool enforces depth
limit before invoking executor".

Why ``tool_class = "write"``:
The parent's classifier cannot reliably know what the sub-agent will do, so we
pessimistically classify the spawn itself as write-class.

Why ``read_only_allowed = True``:
Sub-agent spawning is allowed in read-only (Chat) mode because the spawned
sub-agent inherits the parent's ``permission_tier`` and its own
``ReadOnlyPermissionReducer`` blocks all write tools before the LLM sees them.
This provides a double-guarantee: spawn is allowed, but the sub-agent cannot
actually mutate files in read-only mode.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from datetime import UTC, datetime
from typing import Any

from prismclaw.engine.execution.capabilities.catalog import CapabilityCatalogService
from prismclaw.engine.security.gate.types import (
    ParallelTaskExtras,
    TaskRuntimeContext,
)
from prismclaw.engine.subagent.core.contract import (
    build_context_snapshot,
    context_snapshot_json_schema,
)
from prismclaw.engine.subagent.core.executor import SubAgentExecutor
from prismclaw.engine.subagent.definition.registry import SubAgentRegistry, _closest_match
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext, error_result
from prismclaw.schema.config.config import SubAgentConfig
from prismclaw.schema.content.content_types import TextBlock, ToolResult

logger = logging.getLogger(__name__)

_AUDIT_LOGGER_NAME = "prismclaw.audit.subagent"


# Agent item schema（静态：不再动态注入 agent_name enum）。
# 插件子代理名由 subagent_search 逐级披露；未知名由 execute 的校验给出最近邻提示。
# Each item supports optional model override for multi-LLM parallel scenarios.
_AGENT_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "agent_name": {
            "type": "string",
            "description": (
                "子代理角色名。核心子代理见 `<subagents>`，插件子代理由 "
                "`subagent_search` 检索得到；名称必须逐字一致，区分大小写。"
            ),
        },
        "task": {
            "type": "string",
            "description": "给该子代理的独立自洽任务描述。",
        },
        "model": {
            "type": "string",
            "description": (
                "可选的、针对该子代理的模型覆盖。若省略，则使用代理清单中的默认模型。"
                "在并行执行时，可用此项为不同子代理分配不同模型。"
            ),
        },
        "context_mode": {
            "type": "string",
            "enum": ["inherit", "isolated"],
            "description": (
                "上下文模式：'inherit' = 继承父级上下文；"
                "'isolated' = 不携带任何上下文。默认：'isolated'。"
            ),
        },
        "context_snapshot": {
            "description": (
                "可选的结构化移交状态。若省略，PrismClaw 会根据当前任务自动组装上下文。"
            ),
            **context_snapshot_json_schema(),
        },
    },
    "required": ["agent_name", "task"],
    "additionalProperties": False,
}

_PARAMETERS_TEMPLATE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "agents": {
            "type": "array",
            "items": _AGENT_ITEM_SCHEMA,
            "minItems": 1,
            "maxItems": 8,
            "description": (
                "要派生的子代理列表。每个代理各自执行一项独立任务。"
                "可使用多个代理，在不同模型或不同上下文之间并行推进工作。"
            ),
        },
        "execution_mode": {
            "type": "string",
            "enum": ["sync", "parallel"],
            "description": (
                "'sync' = 顺序执行各代理，逐个等待完成；"
                "'parallel' = 并发执行全部代理，等待全部完成。"
                "默认：'parallel'。"
            ),
        },
    },
    "required": ["agents"],
    "additionalProperties": False,
}


def _first_text_block_text(result: ToolResult) -> str:
    for block in result.content:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            return text
    return ""


def _dedupe_merge_strings(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for v in values:
        s = str(v).strip()
        if s and s not in seen:
            seen.add(s)
            result.append(s)
    return result


def _dedupe_merge_lists(groups) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for group in groups:
        for item in group:
            s = str(item).strip()
            if s and s not in seen:
                seen.add(s)
                result.append(s)
    return result


def _dedupe_evidence(items: list) -> list:
    """去重 evidence 列表，按 source_path + source_location + quoted_text 判重"""
    seen: set[str] = set()
    result: list = []
    for e in items:
        key = f"{e.source_path}:{e.source_location}:{e.quoted_text}"
        if key not in seen:
            seen.add(key)
            result.append(e)
    return result


def _assess_overall_confidence(envelopes: list) -> str:
    """评估整体置信度——取最低（空字符串视为缺失，不参与降级）"""
    if not envelopes:
        return "low"
    levels = {"high": 3, "medium": 2, "low": 1}
    valid_levels = [
        levels.get(getattr(e, "confidence_assessment", ""), 0)
        for e in envelopes
        if getattr(e, "confidence_assessment", "")
    ]
    if not valid_levels:
        return ""  # 全部缺失时不强制降级
    min_level = min(valid_levels)
    return {3: "high", 2: "medium", 1: "low"}.get(min_level, "low")


class SubAgentTool:
    name = "sub_agent"
    side_effect = True
    tool_class = "write"
    effect_class = "local_mutation"
    visibility_scope = "main_only"
    read_only_allowed = True

    def __init__(
        self,
        *,
        executor: SubAgentExecutor,
        registry: SubAgentRegistry,
        settings: SubAgentConfig,
        capability_catalog: CapabilityCatalogService,
        audit_logger: logging.Logger | None = None,
    ) -> None:
        self._executor = executor
        self._registry = registry
        self._settings = settings
        self._capability_catalog = capability_catalog
        self._audit = audit_logger or logging.getLogger(_AUDIT_LOGGER_NAME)
        # 静态 schema：插件子代理名不再注入 enum —— 发现面是 subagent_search，
        # 未知名由 execute 的校验给出最近邻提示，避免每轮请求携带全量名单。
        self.parameters = copy.deepcopy(_PARAMETERS_TEMPLATE)
        self.description = self._render_description()

    @property
    def registry(self):
        return self._registry

    @property
    def settings(self):
        return self._settings

    @property
    def capability_catalog(self) -> CapabilityCatalogService:
        return self._capability_catalog

    def resolve_dispatch_timeout_seconds(self, args: dict[str, Any]) -> float:
        """子代理分发外层超时——恒为 0（不封顶），时长交由内部 turn loop 掌控。

        ``sub_agent`` 拉起的是一整条嵌套 agent 循环（多轮 LLM + 多次工具调用），
        与单次有界的真实工具调用语义/量级完全不同，不应由通用 ``tool_seconds`` 的
        分发包裹层施加墙钟上限。

        返回 0 表示「外层不封顶」：``run_with_timeout`` / ``asyncio.wait_for`` 在
        timeout <= 0 时仅受 ``abort_event`` 约束。子代理的真实时限由内部
        ``run_subagent_turn_loop``（复用 ``run_loop`` 骨架）决定——fork（主流程延续
        分支）无墙钟上限（沿用主流程 run_seconds / max_iterations 禁用的哲学），
        孤立派生子代理按 manifest / 默认 ``timeout_seconds`` 封顶（夹
        ``TIMEOUT_HARD_CEILING_S``）。单一权威源在内部，避免外层与内层双重上限互相踩。
        真实工具仍走 ``tool_seconds``。
        """
        return 0.0

    async def execute(
        self, args: dict[str, Any], context: ToolContext
    ) -> ToolResult:
        call_id = args.get("_call_id", "")

        if context.subagent_depth >= self._settings.max_depth:
            return error_result(
                call_id,
                f"子代理嵌套深度已超过上限（{self._settings.max_depth}），"
                f"子代理不能再创建子代理。",
            )

        agents_config = args.get("agents", [])
        if not agents_config:
            return error_result(
                call_id, "sub_agent：缺少必填参数 'agents'"
            )
        if not isinstance(agents_config, list) or len(agents_config) > 8:
            return error_result(
                call_id,
                "sub_agent：'agents' 必须是包含 1 到 8 个元素的列表",
            )

        execution_mode = args.get("execution_mode", "parallel")
        if execution_mode not in ("sync", "parallel"):
            return error_result(
                call_id,
                f"sub_agent：execution_mode '{execution_mode}' 无效，"
                "只能是 'sync' 或 'parallel'。",
            )

        # Resolve and validate all agent configs
        catalog = self.capability_catalog
        resolved_configs = []
        errors = []
        for i, agent_cfg in enumerate(agents_config):
            if not isinstance(agent_cfg, dict):
                errors.append(f"agents[{i}]：必须是对象")
                continue
            agent_name = agent_cfg.get("agent_name", "")
            task = agent_cfg.get("task", "")
            if not agent_name:
                errors.append(f"agents[{i}]：缺少 'agent_name'")
                continue
            if not task:
                errors.append(f"agents[{i}]：缺少 'task'")
                continue
            entry = catalog.get_agent_entry(agent_name)
            if entry is None:
                all_entries = catalog.list_agent_entries()
                registered_names = {e.name for e in all_entries}
                suggestion = _closest_match(agent_name, registered_names, max_dist=2)
                hint = f"（是否想用 '{suggestion}'？）" if suggestion else ""
                errors.append(
                    f"agents[{i}]：未找到子代理 '{agent_name}'{hint}"
                )
                continue
            resolved_configs.append({
                "index": i,
                "agent_cfg": agent_cfg,
                "manifest_entry": entry,
            })

        if errors:
            return error_result(
                call_id,
                "sub_agent：参数校验失败：\n" + "\n".join(f"  - {e}" for e in errors),
            )

        context.subagent_catalog = catalog

        # Execute agents
        if execution_mode == "parallel" and len(resolved_configs) > 1:
            results = await self._run_parallel_agents(
                resolved_configs, context, call_id
            )
        else:
            results = []
            for cfg in resolved_configs:
                result = await self._run_single_agent(
                    cfg, context, call_id
                )
                results.append(result)

        return self._aggregate_results(results, call_id)

    async def _run_single_agent(
        self,
        cfg: dict[str, Any],
        context: ToolContext,
        call_id: str,
    ) -> dict[str, Any]:
        agent_cfg = cfg["agent_cfg"]
        entry = cfg["manifest_entry"]
        manifest = entry.manifest
        agent_name = agent_cfg["agent_name"]
        task = agent_cfg["task"]

        # ToolContext.__deepcopy__ 已自动排除所有 Any 类型的 runtime 字段
        # （含 Future/Lock/Task 等不可 pickle 对象），无需在此手动 whack-a-mole。
        # 仅需处理 ParallelTaskExtras 里 progress_emitter / parallel_task_store
        # 这两个非 Any 字段——它们是回调通道，子 agent 不继承。
        _saved_task_runtime = context.task_runtime
        _saved_progress_emitter = None
        _saved_parallel_task_store = None
        if context.task_runtime is not None:
            _saved_progress_emitter = getattr(context.task_runtime, "progress_emitter", None)
            _saved_parallel_task_store = getattr(context.task_runtime, "parallel_task_store", None)
            inner_ctx = getattr(context.task_runtime, "task_runtime", None)
            context.task_runtime = ParallelTaskExtras(
                task_runtime=inner_ctx,
            )

        try:
            child_context = copy.deepcopy(context)
        finally:
            context.task_runtime = _saved_task_runtime

        # hook_dispatcher 是运行时引用（Any 字段，deepcopy 自动排除），
        # 按引用回填——子代理内部的 MCP 工具调用也要经过插件 PostToolUse 钩子
        # （POST_TOOL 派发点已下沉到 execute_tool_calls 公共汇聚层）。
        child_context.hook_dispatcher = getattr(context, "hook_dispatcher", None)

        if child_context.task_runtime is not None:
            if hasattr(child_context.task_runtime, "progress_emitter"):
                child_context.task_runtime.progress_emitter = _saved_progress_emitter
            if hasattr(child_context.task_runtime, "parallel_task_store"):
                child_context.task_runtime.parallel_task_store = _saved_parallel_task_store

        # Build a strong-typed task runtime context for the child.
        parent_ctx = context
        parent_task_ctx = parent_ctx.task_runtime_ctx

        child_is_fork = parent_task_ctx.is_fork

        child_task_ctx = TaskRuntimeContext(
            parallel_task_id=parent_task_ctx.parallel_task_id,
            managed_task_id=parent_task_ctx.managed_task_id,
            is_fork=child_is_fork,
            handoff_chain_id=parent_task_ctx.handoff_chain_id,
            handoff_hop_index=parent_task_ctx.handoff_hop_index,
            effective_hop_index=parent_task_ctx.effective_hop_index,
            progress_batch_id=parent_task_ctx.progress_batch_id,
            source_skill_origin=parent_task_ctx.source_skill_origin,
            source_preload_skills=parent_task_ctx.source_preload_skills,
            merged_from_chains=parent_task_ctx.merged_from_chains,
        )
        # 用 getattr 从 parent_ctx.task_runtime 安全读取 extras（父容器此时可能已被
        # 我们在 L381-387 包成干净的 ParallelTaskExtras，即使没有 progress_emitter 也 None 兜底）
        _parent_emitter = getattr(parent_ctx.task_runtime, "progress_emitter", None)
        _parent_store = getattr(parent_ctx.task_runtime, "parallel_task_store", None)
        child_context.task_runtime = ParallelTaskExtras(
            task_runtime=child_task_ctx,
            progress_emitter=_parent_emitter,
            parallel_task_store=_parent_store,
        )

        # Build context snapshot
        raw_snapshot = agent_cfg.get("context_snapshot")
        try:
            context_snapshot = build_context_snapshot(
                task=task,
                parent_ctx=context,
                raw_snapshot=raw_snapshot,
            )
        except ValueError as exc:
            return {
                "agent_name": agent_name,
                "outcome": "error",
                "error_message": f"context_snapshot 无效：{exc}",
                "tool_result": error_result(call_id, f"context_snapshot 无效：{exc}"),
            }

        # Apply overrides: model, context_mode
        overrides: dict[str, Any] = {}
        model_override = agent_cfg.get("model")
        if model_override:
            overrides["model"] = model_override
        context_mode = agent_cfg.get("context_mode")
        if context_mode in ("inherit", "isolated"):
            overrides["context_mode"] = context_mode
        if overrides:
            manifest = manifest.model_copy(update=overrides)

        try:
            result = await self._executor.run(
                manifest,
                task,
                child_context,
                call_id=call_id,
                context_snapshot=context_snapshot,
            )
            self._emit_spawn_audit(
                context=child_context,
                manifest=manifest,
                outcome=result.outcome,
                turns=result.turns,
                tokens_input=result.tokens_input,
                tokens_output=result.tokens_output,
                duration_seconds=result.duration_seconds,
                model_used=result.model_used,
            )
            return {
                "agent_name": agent_name,
                "model_used": result.model_used,
                "outcome": result.outcome,
                "turns": result.turns,
                "duration_seconds": result.duration_seconds,
                "tool_result": result.tool_result,
            }
        except asyncio.CancelledError:
            self._emit_spawn_audit(
                context=child_context,
                manifest=manifest,
                outcome="aborted",
                turns=0,
                tokens_input=0,
                tokens_output=0,
                duration_seconds=0.0,
                model_used=manifest.model or "",
                error_message="cancelled",
            )
            return {
                "agent_name": agent_name,
                "outcome": "aborted",
                "tool_result": error_result(call_id, f"子代理 '{agent_name}' 已被取消"),
            }
        except Exception as exc:
            logger.exception("subagent.run_failed name=%s", agent_name)
            self._emit_spawn_audit(
                context=child_context,
                manifest=manifest,
                outcome="error",
                turns=0,
                tokens_input=0,
                tokens_output=0,
                duration_seconds=0.0,
                model_used=manifest.model or "",
                error_message=f"{type(exc).__name__}: {exc}",
            )
            return {
                "agent_name": agent_name,
                "model_used": manifest.model or "",
                "outcome": "error",
                "error_message": f"{type(exc).__name__}: {exc}",
                "tool_result": error_result(
                    call_id,
                    f"子代理 '{agent_name}' 执行失败：{type(exc).__name__}: {exc}",
                ),
            }

    async def _run_parallel_agents(
        self,
        configs: list[dict[str, Any]],
        context: ToolContext,
        call_id: str,
    ) -> list[dict[str, Any]]:
        """Run multiple sub-agents concurrently using asyncio.gather."""
        tasks = [
            self._run_single_agent(cfg, context, call_id)
            for cfg in configs
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        normalized = []
        for cfg, result in zip(configs, results):
            if isinstance(result, Exception):
                agent_name = cfg["agent_cfg"]["agent_name"]
                normalized.append({
                    "agent_name": agent_name,
                    "outcome": "error",
                    "error_message": str(result),
                    "tool_result": error_result(
                        call_id,
                        f"子代理 '{agent_name}' 执行失败：{result}",
                    ),
                })
            else:
                normalized.append(result)
        return normalized

    def _aggregate_results(
        self,
        results: list[dict[str, Any]],
        call_id: str,
    ) -> ToolResult:
        """Aggregate results from multiple sub-agents into a single ToolResult.

        Merges structured ``SubagentResultEnvelope`` objects when available
        so that findings / open_questions survive the aggregation boundary and
        can be extracted by ``extract_state_update`` downstream.
        """
        if len(results) == 1:
            return results[0]["tool_result"]

        from prismclaw.engine.subagent.core.contract import (
            parse_subagent_result_envelope,
            render_result_envelope_handover,
        )
        from prismclaw.schema.content.content_types import SubagentEnvelopeArtifact
        from prismclaw.schema.content.subagent import SubagentResultEnvelope

        envelopes: list[SubagentResultEnvelope] = []
        agent_summaries: list[str] = []
        success_count = 0
        error_count = 0
        merged_handoff = None
        task_id = None

        for r in results:
            agent_name = r["agent_name"]
            outcome = r.get("outcome", "unknown")
            tool_result: ToolResult | None = r.get("tool_result")

            if outcome != "success":
                error_count += 1
                error_msg = r.get("error_message", "unknown error")
                agent_summaries.append(f"[{agent_name}] error: {error_msg}")
                # 非 success subagent 仍可能携带 evidence（超时/部分完成时
                # _lifecycle_mixin 会自动收集已执行工具调用的 evidence），
                # 不应直接丢弃——提取其 envelope 参与聚合
                if tool_result is not None and tool_result.subagent_envelope is not None:
                    envelope = tool_result.subagent_envelope.result_envelope
                    if envelope is not None:
                        envelopes.append(envelope)
                continue

            success_count += 1
            envelope: SubagentResultEnvelope | None = None
            if tool_result is not None and tool_result.subagent_envelope is not None:
                envelope = tool_result.subagent_envelope.result_envelope
                if task_id is None and tool_result.subagent_envelope.task_id:
                    task_id = tool_result.subagent_envelope.task_id
                if merged_handoff is None and tool_result.subagent_envelope.handoff_request:
                    merged_handoff = tool_result.subagent_envelope.handoff_request

            if envelope is not None:
                envelopes.append(envelope)
            else:
                text = _first_text_block_text(tool_result) if tool_result else ""
                if text:
                    parsed = parse_subagent_result_envelope(text)
                    if parsed is not None:
                        envelopes.append(parsed)
                    else:
                        envelopes.append(
                            SubagentResultEnvelope(summary=f"[{agent_name}] {text}")
                        )

        if envelopes:
            # 合并 evidence——按 source_path + quoted_text 去重
            all_evidence: list = []
            for e in envelopes:
                all_evidence.extend(e.evidence)
            merged_evidence = _dedupe_evidence(all_evidence)

            # 评估整体置信度——取最低
            confidence_assessment = _assess_overall_confidence(envelopes)

            merged = SubagentResultEnvelope(
                summary="\n".join(
                    _dedupe_merge_strings(e.summary for e in envelopes if e.summary.strip())
                ),
                findings=_dedupe_merge_lists(e.findings for e in envelopes),
                evidence=merged_evidence,
                decisions=_dedupe_merge_lists(e.decisions for e in envelopes),
                open_questions=_dedupe_merge_lists(e.open_questions for e in envelopes),
                artifact_refs=_dedupe_merge_lists(e.artifact_refs for e in envelopes),
                recommended_next_actions=_dedupe_merge_lists(
                    e.recommended_next_actions for e in envelopes
                ),
                confidence_assessment=confidence_assessment,
            )
            readable_parts = [f"[{success_count} sub-agents completed]"]
            for i, envelope_item in enumerate(envelopes):
                readable_parts.append(f"\n--- Agent {i + 1} ---")
                readable_parts.append(render_result_envelope_handover(envelope_item))
            readable_parts.append("\n--- Merged ---")
            readable_parts.append(render_result_envelope_handover(merged))
            readable_text = "\n".join(readable_parts)
        else:
            readable_text = (
                f"[{len(results)} sub-agents: {error_count} errors]\n"
                + "\n".join(agent_summaries)
            )

        is_error = error_count > 0 and success_count == 0

        # 全部子代理失败 → 主因是外部数据源不可用（NSSD 404/patentsview
        # DNS/OpenAlex 失败/CNKI 门禁，logs/mcp_server_* 实证），归
        # external：失败归因分流不得把科研检索失败当模型空转。
        result = ToolResult(
            tool_call_id=call_id,
            content=[TextBlock(text=readable_text)],
            is_error=is_error,
            error_origin="external" if is_error else None,
        )
        if envelopes:
            result.subagent_envelope = SubagentEnvelopeArtifact(
                result_envelope=merged,
                task_id=task_id,
                handoff_request=merged_handoff,
            )
        return result

    @staticmethod
    def _build_audit_manifest_stub(*, name: str, source: str) -> Any:
        return type(
            "_AuditManifestStub",
            (),
            {
                "name": name,
                "source": source,
                "execution_mode": "sync",
                "context_mode": "isolated",
                "skill_origin": None,
                "declared_invoke_sequence": [],
            },
        )()

    def _render_description(self) -> str:
        if self.capability_catalog.count_agent_entries() <= 0:
            return (
                "派生一个专门的子代理角色来委派独立自洽的任务。"
                "（当前未安装任何角色；无清单存在时，该工具会对模型隐藏。）"
            )
        return (
            "派生一个专门的子代理角色来委派独立自洽的任务。"
            "子代理在与父级相同的权限层级和沙箱中、以隔离上下文运行。"
            "可用的代理由运行时能力增量面单独投影。"
        )

    def _emit_spawn_audit(
        self,
        *,
        context: ToolContext,
        manifest: Any,
        outcome: str,
        turns: int,
        tokens_input: int,
        tokens_output: int,
        duration_seconds: float,
        model_used: str,
        error_message: str | None = None,
    ) -> None:
        try:
            payload: dict[str, Any] = {
                "event": "subagent_invocation",
                "event_type": "subagent_spawn",
                "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "subagent_name": manifest.name,
                "subagent_source": getattr(manifest, "source", "user"),
                "execution_mode": manifest.execution_mode,
                "context_mode": manifest.context_mode,
                "parent_session_id": context.session_id,
                "subagent_depth": context.subagent_depth + 1,
                "outcome": outcome,
                "turns": turns,
                "tokens_input": tokens_input,
                "tokens_output": tokens_output,
                "duration_seconds": round(duration_seconds, 3),
                "model_used": model_used,
            }
            skill_origin = getattr(manifest, "skill_origin", None)
            if isinstance(skill_origin, str) and skill_origin:
                payload["skill_origin"] = skill_origin
            # preload_skills 弃用,audit 埋点值统一从 declared_invoke_sequence 取
            declared = getattr(manifest, "declared_invoke_sequence", None)
            if isinstance(declared, list) and declared:
                payload["declared_invoke_sequence"] = [
                    item for item in declared if isinstance(item, str) and item
                ]
            tier = context.mode
            if tier is not None:
                payload["tier_inherited"] = tier
            batch_id = context.progress_batch_id
            if batch_id is not None:
                payload["progress_batch_id"] = batch_id
            task_ctx = context.task_runtime_ctx
            if task_ctx.handoff_chain_id is not None:
                payload["handoff_chain_id"] = task_ctx.handoff_chain_id
            if task_ctx.handoff_hop_index is not None:
                payload["handoff_hop_index"] = task_ctx.handoff_hop_index
            if task_ctx.effective_hop_index is not None:
                payload["effective_hop_index"] = task_ctx.effective_hop_index
            if task_ctx.merged_from_chains:
                payload["merged_from_chains"] = task_ctx.merged_from_chains
            dispatch_extras = context.pending_handoff_dispatch_extras
            if dispatch_extras:
                source_skill_origin = dispatch_extras.get(
                    "pending_handoff_source_skill_origin"
                )
                if source_skill_origin is not None:
                    payload["source_skill_origin"] = source_skill_origin
                source_preload_skills = dispatch_extras.get(
                    "pending_handoff_source_preload_skills"
                )
                if source_preload_skills is not None:
                    payload["source_preload_skills"] = source_preload_skills
                source_is_fork = dispatch_extras.get(
                    "pending_handoff_source_is_fork"
                )
                if source_is_fork is not None:
                    payload["source_is_fork"] = source_is_fork
            if context.user_id is not None:
                payload["user_id"] = context.user_id
            if error_message is not None:
                payload["error_message"] = error_message

            self._audit.info(
                json.dumps(payload, separators=(",", ":"), sort_keys=False)
            )
        except Exception:
            logger.warning("subagent.audit.emit_failed", exc_info=True)


__all__ = ["SubAgentTool"]
