"""``sub_agent`` tool: the parent-agent-side gate that spawns sub-agents.

Per ``openspec/changes/add-subagent-system/specs/builtin-tools/spec.md`` ADDED
Requirement "SubAgentTool is a builtin tool with ``tool_class='write'``" and
``subagent-system/spec.md`` ADDED Requirement "SubAgentTool enforces depth
limit before invoking executor".

Why ``tool_class = "write"``:
The parent's classifier cannot reliably know what the sub-agent will do, so we
pessimistically classify the spawn itself as write-class.

Why ``read_only_allowed = True``:
Spawning a sub-agent is not itself a write. The spawned sub-agent inherits the
parent's permission traits and is governed by the same reducers, including
``ServerReadOnlyReducer`` for MCP servers configured as read-only.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from datetime import UTC, datetime
from typing import Any

from prismclaw.engine.execution.capabilities.catalog import RuntimeCatalog
from prismclaw.engine.experts.scope import RejectedScope
from prismclaw.engine.security.gate.types import (
    ParallelTaskExtras,
    TaskRuntimeContext,
)
from prismclaw.engine.subagent.core.contract import (
    build_context_snapshot,
    context_snapshot_json_schema,
)
from prismclaw.engine.subagent.core.executor import SubAgentExecutor
from prismclaw.engine.subagent.definition.manifest import AgentManifest
from prismclaw.engine.subagent.definition.registry import SubAgentRegistry, _closest_match
from prismclaw.engine.subagent.runtime.collaboration import (
    SubagentCollaborationService,
    SubagentRunLaunch,
    SubagentSpawnOutcome,
)
from prismclaw.engine.subagent.runtime.records import AgentLifecycle
from prismclaw.engine.subagent.team_guard import TeamGuardError, guard_dispatch
from prismclaw.engine.subagent.team_registry import TeamErrorCode, get_team_registry
from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext, error_result
from prismclaw.schema.config.config import SubAgentConfig
from prismclaw.schema.content.content_types import SubagentResultItem, TextBlock, ToolResult
from prismclaw.schema.content.subagent import SubagentContextSnapshot

logger = logging.getLogger(__name__)

_AUDIT_LOGGER_NAME = "prismclaw.audit.subagent"


def _coerce_lifecycle(value: object) -> AgentLifecycle:
    """把模型传入的 lifecycle 收敛到字面量类型，未知值回退 one_shot。"""
    return "persistent" if value == "persistent" else "one_shot"


# Agent item schema（静态：不再动态注入 agent_name enum）。
# 专家包子代理名由 subagent_search 逐级披露；未知名由 execute 的校验给出最近邻提示。
# Each item supports optional model override for multi-LLM parallel scenarios.
_AGENT_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "agent_name": {
            "type": "string",
            "description": (
                "子代理角色名，须与 `<subagents>` 或 `subagent_search` 返回的名称"
                "逐字一致（区分大小写）。"
            ),
        },
        "task": {
            "type": "string",
            "description": "给该子代理的独立自洽任务描述。",
        },
        "lifecycle": {
            "type": "string",
            "enum": ["one_shot", "persistent"],
            "description": (
                "'one_shot' 完成后关闭（默认）；'persistent' 保留身份与子会话，"
                "可用 subagent_control 继续发送消息。"
            ),
        },
        "request_id": {
            "type": "string",
            "description": "可选幂等标识；重试同一请求时保持不变。",
        },
        "model": {
            "type": "string",
            "description": (
                "可选：覆盖该子代理使用的模型；省略则用代理清单默认模型，"
                "并行时可分别为各子代理指定不同模型。"
            ),
        },
        "context_mode": {
            "type": "string",
            "enum": ["inherit", "isolated"],
            "description": ("'inherit' 继承父级上下文；'isolated' 不携带上下文（默认）。"),
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
            "description": ("要派生的子代理列表（1–8 个，每个执行一项独立任务）。"),
        },
        "execution_mode": {
            "type": "string",
            "enum": ["sync", "parallel"],
            "description": (
                "'sync' 顺序执行并逐个等待；'parallel' 并发执行并等待全部（默认）。"
                "仅在 dispatch_mode='now' 时生效。"
            ),
        },
        "dispatch_mode": {
            "type": "string",
            "enum": ["now", "background"],
            "description": (
                "'now' 立即执行并等待全部结果（默认）；"
                "'background' 立即返回 task_id，子代理在后台继续。"
            ),
        },
    },
    "required": ["agents"],
    "additionalProperties": False,
}


def _first_text_block_text(result: ToolResult | None) -> str:
    if result is None:
        return ""
    for block in result.content:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            return text
    return ""


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
        capability_catalog: RuntimeCatalog,
        audit_logger: logging.Logger | None = None,
        allow_background: bool = True,
        collaboration: SubagentCollaborationService | None = None,
        allow_persistent_background: bool = True,
    ) -> None:
        self._executor = executor
        self._registry = registry
        self._settings = settings
        self._capability_catalog = capability_catalog
        self._audit = audit_logger or logging.getLogger(_AUDIT_LOGGER_NAME)
        self._allow_background = allow_background
        self._collaboration = collaboration
        self._allow_persistent_background = allow_persistent_background
        # 静态 schema：专家包子代理名不再注入 enum —— 发现面是 subagent_search，
        # 未知名由 execute 的校验给出最近邻提示，避免每轮请求携带全量名单。
        self.parameters = copy.deepcopy(_PARAMETERS_TEMPLATE)
        if not allow_background and not (collaboration is not None and allow_persistent_background):
            # 没有可跨 worker 恢复的 persistent 路径时，多副本不暴露进程内后台任务。
            self.parameters["properties"].pop("dispatch_mode", None)
        self.description = self._render_description()

    @property
    def registry(self) -> SubAgentRegistry:
        return self._registry

    @property
    def settings(self) -> SubAgentConfig:
        return self._settings

    @property
    def capability_catalog(self) -> RuntimeCatalog:
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

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = args.get("_call_id", "")

        scope = context.session_scope
        # 父会话的专家被拒（未部署 / 策略禁用）时不允许派生：子代理有独立
        # run_loop 入口，若不在此拒绝，RejectedScope 的不变量只在主循环成立。
        if isinstance(scope, RejectedScope):
            return error_result(
                call_id,
                f"sub_agent：当前会话的专家不可用（{scope.code.value}），已拒绝派发子代理。",
            )
        team_name = scope.team_name if scope is not None else None
        if team_name and scope is not None and scope.member:
            raw_agents = args.get("agents")
            first_agent = raw_agents[0] if isinstance(raw_agents, list) and raw_agents else {}
            agent_name = (
                first_agent.get("agent_name", "") if isinstance(first_agent, dict) else ""
            )
            error = TeamGuardError(
                TeamErrorCode.TEAM_SPAWN_BY_MEMBER,
                team_name=team_name,
                agent_name=agent_name,
                caller=context.subagent_name or "unknown",
                reason="team members cannot dispatch other agents",
            )
            return error_result(call_id, f"sub_agent：{error}", metadata=error.metadata)

        if context.subagent_depth >= self._settings.max_depth:
            return error_result(
                call_id,
                f"子代理嵌套深度已超过上限（{self._settings.max_depth}），子代理不能再创建子代理。",
            )

        agents_config = args.get("agents", [])
        if not agents_config:
            return error_result(call_id, "sub_agent：缺少必填参数 'agents'")
        if not isinstance(agents_config, list) or len(agents_config) > 8:
            return error_result(
                call_id,
                "sub_agent：'agents' 必须是包含 1 到 8 个元素的列表",
            )

        execution_mode = args.get("execution_mode", "parallel")
        if execution_mode not in ("sync", "parallel"):
            return error_result(
                call_id,
                f"sub_agent：execution_mode '{execution_mode}' 无效，只能是 'sync' 或 'parallel'。",
            )

        dispatch_mode = args.get("dispatch_mode", "now")
        if dispatch_mode not in ("now", "background"):
            return error_result(
                call_id,
                f"sub_agent：dispatch_mode '{dispatch_mode}' 无效，只能是 'now' 或 'background'。",
            )
        # Resolve and validate all agent configs
        catalog = self.capability_catalog
        resolved_configs = []
        errors = []
        team_errors: list[TeamGuardError] = []
        team_name = context.session_scope.team_name if context.session_scope is not None else None
        team_registry = get_team_registry(context) if team_name else None
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
            lifecycle = agent_cfg.get("lifecycle", "one_shot")
            if lifecycle not in ("one_shot", "persistent"):
                errors.append(
                    f"agents[{i}]：lifecycle '{lifecycle}' 无效，只能是 'one_shot' 或 'persistent'"
                )
                continue
            if lifecycle == "persistent" and self._collaboration is None:
                errors.append(f"agents[{i}]：persistent 生命周期需要协作运行时")
                continue
            entry = catalog.get_agent_entry(agent_name)
            if team_name:
                lead_agent = scope.lead_agent if scope is not None else None
                is_member = scope.member if scope is not None else False
                try:
                    await guard_dispatch(
                        context.subagent_name or lead_agent or "",
                        agent_name,
                        team_name,
                        registry=team_registry,
                        lead_agent=lead_agent or "",
                        entry=entry,
                        is_member=is_member,
                    )
                except TeamGuardError as exc:
                    team_errors.append(exc)
                    errors.append(f"agents[{i}]：{exc}")
                    continue
            elif entry is None:
                all_entries = catalog.list_agent_entries()
                registered_names = {e.name for e in all_entries}
                suggestion = _closest_match(agent_name, registered_names, max_dist=2)
                hint = f"（是否想用 '{suggestion}'？）" if suggestion else ""
                errors.append(f"agents[{i}]：未找到子代理 '{agent_name}'{hint}")
                continue
            # 工具归属门控：目录过滤只影响「模型看到什么」，
            # 模型仍可能从历史消息中获知 agent 名直接派发，故派发处二次校验。
            assert entry is not None
            _owner = entry.expert_name
            if (
                _owner is not None
                and scope is not None
                and _owner not in scope.tool_owners
            ):
                errors.append(
                    f"agents[{i}]：agent_scope_denied:{_owner}"
                    f"（该子代理属于未启用的专家包「{_owner}」）"
                )
                continue
            resolved_configs.append(
                {
                    "index": i,
                    "agent_cfg": agent_cfg,
                    "manifest_entry": entry,
                    "execution_mode": execution_mode,
                }
            )

        lifecycles = {cfg["agent_cfg"].get("lifecycle", "one_shot") for cfg in resolved_configs}
        if "persistent" in lifecycles and len(agents_config) != 1:
            errors.append("persistent 每次 spawn 只能包含一个 Agent")
        if dispatch_mode == "background":
            if "one_shot" in lifecycles and not self._allow_background:
                errors.append(
                    "one_shot 后台派发在当前多副本部署下不可用；"
                    "请使用 dispatch_mode='now' 或 lifecycle='persistent'"
                )
            if "persistent" in lifecycles and not self._allow_persistent_background:
                errors.append("persistent 后台派发在当前部署下不可用")

        if errors:
            metadata: dict[str, Any] | None = None
            if team_errors:
                metadata = dict(team_errors[0].metadata)
                if len(team_errors) > 1:
                    metadata["errors"] = [error.metadata for error in team_errors]
            return error_result(
                call_id,
                "sub_agent：参数校验失败：\n" + "\n".join(f"  - {e}" for e in errors),
                metadata=metadata,
            )

        context.subagent_catalog = catalog

        # 后台派发：全部代理各自排队，立即返回 task_id（不阻塞等待）。
        if dispatch_mode == "background":
            background_results = []
            for cfg in resolved_configs:
                background_results.append(
                    await self._run_single_agent(cfg, context, call_id, background=True)
                )
            return self._aggregate_background_results(background_results, call_id)

        # Execute agents
        if execution_mode == "parallel" and len(resolved_configs) > 1:
            results = await self._run_parallel_agents(resolved_configs, context, call_id)
        else:
            results = []
            for cfg in resolved_configs:
                result = await self._run_single_agent(cfg, context, call_id)
                results.append(result)

        return self._aggregate_results(results, call_id)

    async def _run_single_agent(
        self,
        cfg: dict[str, Any],
        context: ToolContext,
        call_id: str,
        *,
        background: bool = False,
    ) -> dict[str, Any]:
        agent_cfg = cfg["agent_cfg"]
        entry = cfg["manifest_entry"]
        manifest = entry.manifest
        agent_name = agent_cfg["agent_name"]
        task = agent_cfg["task"]
        execution_mode = str(cfg.get("execution_mode", "sync"))

        # ToolContext.__deepcopy__ 已自动排除所有 Any 类型的 runtime 字段
        # （含 Future/Lock/Task 等不可 pickle 对象），无需在此手动 whack-a-mole。
        # parallel_task_store 是运行时回调通道，deepcopy 前先换成干净容器，
        # 避免把父级运行时引用复制给子 agent（子 agent 自己的记录由 run() 写入）。
        parent_extras = context.task_runtime
        context.task_runtime = ParallelTaskExtras(task_runtime=parent_extras.task_runtime)
        try:
            child_context = copy.deepcopy(context)
        finally:
            context.task_runtime = parent_extras

        # hook_dispatcher 是运行时引用（Any 字段，deepcopy 自动排除），
        # 按引用回填——子代理内部的 MCP 工具调用也要经过专家包 PostToolUse 钩子
        # （POST_TOOL 派发点已下沉到 execute_tool_calls 公共汇聚层）。
        child_context.hook_dispatcher = context.hook_dispatcher
        _parent_scope = context.session_scope
        if _parent_scope is not None and _parent_scope.team_name:
            # 人格仍取自该成员的 catalog manifest；团队身份只绑定本次 spawn。
            # 父作用域存在时收窄为成员面（member=True）；为 None 表示无作用域上下文，
            # 保持 None 语义＝不做限制，不凭空创建作用域。
            child_context.session_scope = _parent_scope.with_member()
            child_context.team_registry = context.team_registry

        if background:
            # 后台任务不在父 run 的调用栈内：共享父级 abort，使用户中止整轮时
            # 子代理在下一个检查点退出并落 cancelled 终态；task_manager 与终态
            # 投递 sink 都是运行时引用，deepcopy 后被清空，需要按引用回填。
            child_context.abort = context.abort
            child_context.task_manager = context.task_manager
            child_context.subagent_terminal_sink = context.subagent_terminal_sink

        # Build a strong-typed task runtime context for the child.
        parent_ctx = context
        parent_task_ctx = parent_ctx.task_runtime_ctx

        child_is_fork = parent_task_ctx.is_fork

        child_task_ctx = TaskRuntimeContext(
            parallel_task_id=parent_task_ctx.parallel_task_id,
            managed_task_id=parent_task_ctx.managed_task_id,
            is_fork=child_is_fork,
            progress_batch_id=parent_task_ctx.progress_batch_id,
        )
        child_context.task_runtime = ParallelTaskExtras(
            task_runtime=child_task_ctx,
            parallel_task_store=parent_extras.parallel_task_store,
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

        if self._collaboration is not None:
            request_id = str(agent_cfg.get("request_id") or "").strip()
            if not request_id:
                request_id = f"{call_id or 'sub_agent'}:{cfg.get('index', 0)}:{agent_name}"
            lifecycle = _coerce_lifecycle(agent_cfg.get("lifecycle", "one_shot"))
            return await self._run_collaboration_agent(
                agent_name=agent_name,
                task=task,
                manifest=manifest,
                child_context=child_context,
                call_id=call_id,
                context_snapshot=context_snapshot,
                execution_mode=execution_mode,
                lifecycle=lifecycle,
                request_id=request_id,
                overrides=overrides,
                background=background,
            )

        if background:
            try:
                task_id, agent_id, launch_error = await self._executor.launch_background(
                    manifest,
                    task,
                    child_context,
                    call_id=call_id,
                    context_snapshot=context_snapshot,
                )
            except Exception as exc:
                logger.exception("subagent.background.launch_failed name=%s", agent_name)
                task_id, agent_id, launch_error = None, None, f"{type(exc).__name__}: {exc}"
            self._emit_spawn_audit(
                context=child_context,
                manifest=manifest,
                outcome="background" if task_id is not None else "error",
                turns=0,
                tokens_input=0,
                tokens_output=0,
                duration_seconds=0.0,
                model_used=manifest.model or "",
                execution_mode=execution_mode,
                error_message=launch_error,
            )
            return {
                "agent_name": agent_name,
                "outcome": "background" if task_id is not None else "error",
                "task_id": task_id,
                "agent_id": agent_id,
                "error_message": launch_error,
                "tool_result": None,
            }

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
                execution_mode=execution_mode,
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
                execution_mode=execution_mode,
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
                execution_mode=execution_mode,
                error_message=f"{type(exc).__name__}: {exc}",
            )
            return {
                "agent_name": agent_name,
                "model_used": manifest.model or "",
                "outcome": "error",
                "error_message": f"{type(exc).__name__}: {exc}",
                "tool_result": error_result(
                    call_id,
                    f"[子代理执行失败：{agent_name} | {type(exc).__name__}: {exc}]",
                ),
            }

    async def _run_collaboration_agent(
        self,
        *,
        agent_name: str,
        task: str,
        manifest: AgentManifest,
        child_context: ToolContext,
        call_id: str,
        context_snapshot: SubagentContextSnapshot,
        execution_mode: str,
        lifecycle: AgentLifecycle,
        request_id: str,
        overrides: dict[str, Any],
        background: bool,
    ) -> dict[str, Any]:
        collaboration = self._collaboration
        if collaboration is None:
            raise RuntimeError("协作运行时未配置")
        try:
            tool_registry = self.capability_catalog.tool_registry
            if tool_registry is None:
                raise RuntimeError("当前能力目录没有工具注册表")
            launch = SubagentRunLaunch(
                manifest=manifest,
                executor=self._executor,
                parent_ctx=child_context,
                tool_registry=tool_registry,
                context_snapshot=context_snapshot,
                call_id=call_id,
                agent_source=str(getattr(manifest, "source", "user") or "user"),
                overrides=overrides,
            )
            outcome = await collaboration.spawn(
                launch,
                task=task,
                lifecycle=lifecycle,
                request_id=request_id,
                background=background,
            )
            if outcome.run is None:
                raise RuntimeError("协作运行时没有创建 Run")
            identity = {
                "agent_id": outcome.agent.agent_id,
                "run_id": outcome.run.run_id,
                "child_session_id": outcome.agent.child_session_id,
                "lifecycle": lifecycle,
                "task_id": outcome.run.task_id,
            }
            if background:
                self._emit_spawn_audit(
                    context=child_context,
                    manifest=manifest,
                    outcome="background",
                    turns=0,
                    tokens_input=0,
                    tokens_output=0,
                    duration_seconds=0.0,
                    model_used=manifest.model or "",
                    execution_mode=execution_mode,
                )
                return {
                    "agent_name": agent_name,
                    "outcome": "background",
                    **identity,
                    "collaboration_managed": True,
                    "error_message": None,
                    "tool_result": None,
                }

            tool_result, resolved_outcome = self._collaboration_tool_result(
                outcome,
                call_id,
            )
            artifact = tool_result.subagent_envelope
            if artifact is not None:
                tool_result.subagent_envelope = artifact.model_copy(update=identity)
            result = outcome.result
            self._emit_spawn_audit(
                context=child_context,
                manifest=manifest,
                outcome=resolved_outcome,
                turns=result.turns if result else (artifact.turns if artifact else 0) or 0,
                tokens_input=(
                    result.tokens_input
                    if result
                    else (artifact.tokens_input if artifact else 0) or 0
                ),
                tokens_output=(
                    result.tokens_output
                    if result
                    else (artifact.tokens_output if artifact else 0) or 0
                ),
                duration_seconds=(
                    result.duration_seconds
                    if result
                    else (artifact.duration_seconds if artifact else 0.0) or 0.0
                ),
                model_used=(
                    result.model_used
                    if result
                    else (artifact.model_used if artifact else None)
                    or outcome.agent.definition_snapshot.get("model", "")
                ),
                execution_mode=execution_mode,
                error_message=outcome.run.error_summary,
            )
            return {
                "agent_name": agent_name,
                **identity,
                "model_used": result.model_used if result else manifest.model or "",
                "outcome": resolved_outcome,
                "turns": result.turns if result else 0,
                "duration_seconds": result.duration_seconds if result else 0.0,
                "error_message": outcome.run.error_summary,
                "tool_result": tool_result,
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
                execution_mode=execution_mode,
                error_message="cancelled",
            )
            return {
                "agent_name": agent_name,
                "outcome": "aborted",
                "tool_result": error_result(call_id, f"子代理 '{agent_name}' 已被取消"),
            }
        except Exception as exc:
            logger.exception("subagent.collaboration_failed name=%s", agent_name)
            self._emit_spawn_audit(
                context=child_context,
                manifest=manifest,
                outcome="error",
                turns=0,
                tokens_input=0,
                tokens_output=0,
                duration_seconds=0.0,
                model_used=manifest.model or "",
                execution_mode=execution_mode,
                error_message=f"{type(exc).__name__}: {exc}",
            )
            return {
                "agent_name": agent_name,
                "model_used": manifest.model or "",
                "outcome": "error",
                "error_message": f"{type(exc).__name__}: {exc}",
                "tool_result": error_result(
                    call_id,
                    f"[子代理执行失败：{agent_name} | {type(exc).__name__}: {exc}]",
                ),
            }

    @staticmethod
    def _collaboration_tool_result(
        outcome: SubagentSpawnOutcome,
        call_id: str,
    ) -> tuple[ToolResult, str]:
        from prismclaw.schema.content.content_types import SubagentEnvelopeArtifact

        if outcome.result is not None:
            return outcome.result.tool_result, outcome.result.outcome
        if outcome.run is None:
            return error_result(call_id, "子代理没有可用的 Run 记录"), "error"
        artifact = SubagentEnvelopeArtifact.model_validate(outcome.run.result_envelope or {})
        resolved_outcome = artifact.outcome or {
            "succeeded": "success",
            "cancelled": "aborted",
            "interrupted": "aborted",
        }.get(outcome.run.status, "error")
        envelope = artifact.result_envelope
        if envelope is not None:
            text = envelope.model_dump_json() if resolved_outcome == "success" else envelope.summary
        else:
            text = (
                artifact.final_text
                or outcome.run.error_summary
                or f"子代理 Run 已结束：{outcome.run.status}"
            )
        return (
            ToolResult(
                tool_call_id=call_id,
                content=[TextBlock(text=text)],
                is_error=resolved_outcome != "success" and envelope is None,
                subagent_envelope=artifact,
            ),
            resolved_outcome,
        )

    async def _run_parallel_agents(
        self,
        configs: list[dict[str, Any]],
        context: ToolContext,
        call_id: str,
    ) -> list[dict[str, Any]]:
        """Run multiple sub-agents concurrently using asyncio.gather."""
        tasks = [self._run_single_agent(cfg, context, call_id) for cfg in configs]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        normalized: list[dict[str, Any]] = []
        for cfg, result in zip(configs, results, strict=True):
            if isinstance(result, BaseException):
                agent_name = cfg["agent_cfg"]["agent_name"]
                normalized.append(
                    {
                        "agent_name": agent_name,
                        "outcome": "error",
                        "error_message": str(result),
                        "tool_result": error_result(
                            call_id,
                            f"[子代理执行失败：{agent_name} | {result}]",
                        ),
                    }
                )
            else:
                normalized.append(result)
        return normalized

    def _aggregate_background_results(
        self,
        results: list[dict[str, Any]],
        call_id: str,
    ) -> ToolResult:
        """把「派发回执」聚合成一条不含任何结果正文的秒回消息。"""
        launched = [r for r in results if r.get("task_id")]
        failed = [r for r in results if not r.get("task_id")]

        lines: list[str] = []
        if launched:
            lines.append(f"已派出 {len(launched)} 个后台子任务：")
            for r in launched:
                lines.append(
                    f"- {r['task_id']} {r['agent_name']}（进行中）"
                    f" | agent_id={r.get('agent_id') or ''}"
                    f" | run_id={r.get('run_id') or ''}"
                    f" | lifecycle={r.get('lifecycle') or 'one_shot'}"
                )
            if any(r.get("collaboration_managed") for r in launched):
                lines.append(
                    "完成状态会自动更新；状态查询、追加消息、中断、恢复和关闭统一使用"
                    " subagent_control，并同时提供 agent_id 与 task_id 作为组合寻址键。"
                )
            else:
                lines.append("完成状态会自动更新；结果会在任务结束后回到本会话。")
        if failed:
            lines.append("未派出：")
            for r in failed:
                lines.append(f"- {r['agent_name']}：{r.get('error_message') or '未知原因'}")

        return ToolResult(
            tool_call_id=call_id,
            content=[TextBlock(text="\n".join(lines))],
            is_error=not launched,
        )

    @staticmethod
    def _result_item(raw: dict[str, Any]) -> SubagentResultItem:
        from prismclaw.engine.subagent.core.contract import parse_subagent_result_envelope
        from prismclaw.schema.content.subagent import SubagentResultEnvelope

        tool_result: ToolResult | None = raw.get("tool_result")
        artifact = tool_result.subagent_envelope if tool_result is not None else None
        envelope = artifact.result_envelope if artifact is not None else None
        text = _first_text_block_text(tool_result) if tool_result is not None else ""
        if envelope is None and text:
            envelope = parse_subagent_result_envelope(text) or SubagentResultEnvelope(summary=text)
        status = str(raw.get("outcome", "unknown"))

        def identity(key: str) -> str | None:
            value = raw.get(key)
            if value is None and artifact is not None:
                value = getattr(artifact, key, None)
            return str(value) if value else None

        return SubagentResultItem(
            name=str(raw["agent_name"]),
            agent_id=identity("agent_id"),
            run_id=identity("run_id"),
            child_session_id=identity("child_session_id"),
            status=status,
            envelope=envelope,
            error=raw.get("error_message") or (None if status == "success" else status),
        )

    def _aggregate_results(
        self,
        results: list[dict[str, Any]],
        call_id: str,
    ) -> ToolResult:
        """Return independent per-agent results without merging conclusions."""
        from prismclaw.engine.subagent.core.contract import render_result_envelope_handover
        from prismclaw.schema.content.content_types import SubagentEnvelopeArtifact

        items = [SubAgentTool._result_item(raw) for raw in results]
        result_payloads = [item.model_dump(mode="python", exclude_none=True) for item in items]

        if len(results) == 1:
            raw_tool_result = results[0].get("tool_result")
            if not isinstance(raw_tool_result, ToolResult):
                return error_result(call_id, items[0].error or "子代理未返回结果")
            tool_result = raw_tool_result
            item = items[0]
            artifact = tool_result.subagent_envelope or SubagentEnvelopeArtifact()
            identity = {
                key: value
                for key, value in {
                    "agent_name": item.name,
                    "agent_id": item.agent_id,
                    "run_id": item.run_id,
                    "child_session_id": item.child_session_id,
                    "lifecycle": results[0].get("lifecycle"),
                    "task_id": results[0].get("task_id"),
                }.items()
                if value is not None
            }
            tool_result.subagent_envelope = artifact.model_copy(update=identity)
            metadata = dict(tool_result.metadata or {})
            metadata["results"] = result_payloads
            tool_result.metadata = metadata
            return tool_result

        success_count = sum(item.status == "success" for item in items)
        error_count = len(items) - success_count
        readable_parts = [
            f"[{len(items)} sub-agent results: {success_count} succeeded, {error_count} failed]"
        ]
        for item, raw in zip(items, results, strict=True):
            readable_parts.append(f"\n--- {item.name} [{item.status}] ---")
            if item.envelope is not None:
                readable_parts.append(render_result_envelope_handover(item.envelope))
            else:
                text = _first_text_block_text(raw.get("tool_result"))
                readable_parts.append(text or item.error or "无结果")

        is_error = success_count == 0
        return ToolResult(
            tool_call_id=call_id,
            content=[TextBlock(text="\n".join(readable_parts))],
            is_error=is_error,
            error_origin="external" if is_error else None,
            subagent_envelope=SubagentEnvelopeArtifact(results=items),
            metadata={"results": result_payloads},
        )

    def _render_description(self) -> str:
        if self.capability_catalog.count_agent_entries() <= 0:
            return (
                "派生一个专门的子代理角色来委派独立自洽的任务。"
                "（当前未安装任何角色；无清单存在时，该工具会对模型隐藏。）"
            )
        background_hint = (
            "``dispatch_mode='background'`` 时立即返回 task_id，"
            "派发后不要等待，完成前不得综合其结果。"
            if self._allow_background
            or (self._collaboration is not None and self._allow_persistent_background)
            else ""
        )
        lifecycle_hint = (
            "默认 lifecycle='one_shot'，完成后关闭；lifecycle='persistent' 时保留"
            "稳定 agent_id 和独立子会话，通过 subagent_control 查询、追加消息、"
            "中断、恢复或关闭。persistent 每次只能派生一个 Agent。"
            if self._collaboration is not None
            else ""
        )
        return "派生一个专门的子代理角色来委派独立自洽的任务。" + lifecycle_hint + background_hint

    def _emit_spawn_audit(
        self,
        *,
        context: ToolContext,
        manifest: AgentManifest,
        outcome: str,
        turns: int,
        tokens_input: int,
        tokens_output: int,
        duration_seconds: float,
        model_used: str,
        execution_mode: str,
        error_message: str | None = None,
    ) -> None:
        try:
            payload: dict[str, Any] = {
                "event": "subagent_invocation",
                "event_type": "subagent_spawn",
                "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "subagent_name": manifest.name,
                "subagent_source": getattr(manifest, "source", "user"),
                "execution_mode": execution_mode,
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
            preload_skills = getattr(manifest, "preload_skills", None)
            if isinstance(preload_skills, list) and preload_skills:
                payload["preload_skills"] = [
                    item for item in preload_skills if isinstance(item, str) and item
                ]
            batch_id = context.progress_batch_id
            if batch_id is not None:
                payload["progress_batch_id"] = batch_id
            if context.user_id is not None:
                payload["user_id"] = context.user_id
            if error_message is not None:
                payload["error_message"] = error_message

            self._audit.info(json.dumps(payload, separators=(",", ":"), sort_keys=False))
        except Exception:
            logger.warning("subagent.audit.emit_failed", exc_info=True)


__all__ = ["SubAgentTool"]
