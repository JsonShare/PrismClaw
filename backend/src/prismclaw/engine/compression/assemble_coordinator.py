from __future__ import annotations

import logging
import time
from typing import Any

from prismclaw.schema.llm.request_mode import RequestMode
from prismclaw.engine.compression.budget_orchestrator import (
    BudgetAuditTrail,
    BudgetOrchestrator,
    merge_budget_audit_trails,
)
from prismclaw.engine.compression.result_compaction import compact_context_blocks_for_turn
from prismclaw.engine.state.projection_rendering import (
    build_history_compaction_placeholder,
    estimate_rendered_text_tokens,
    render_history_compaction_block,
)
from prismclaw.engine.compression.memory_retriever import MemoryRetriever
from prismclaw.schema.agent.task_types import TaskSpec
from prismclaw.schema.config.config import CompactionConfig
from prismclaw.schema.content.compaction import AssembleResult
from prismclaw.schema.content.context import ContextPackLayout
from prismclaw.schema.session.projection import SessionProjectionEngine
from prismclaw.schema.storage.storage_types import SessionStore

from prismclaw.engine.compression.memory_formatter import MemoryFormatter
from prismclaw.engine.compression.state_store import SessionStateStore
from prismclaw.engine.compression.task_profile_inference import TaskProfileInference
from prismclaw.engine.compression.evidence_runtime import build_context_graph
from prismclaw.engine.compression.assembly_state import build_assembly_state_bundle
from prismclaw.engine.compression.memory_assembly_cache import (
    MemoryCore,
    MemoryCoreCache,
    memory_core_cache_key,
)

logger = logging.getLogger(__name__)


class AssembleCoordinator:
    def __init__(
        self,
        *,
        memory_retriever: MemoryRetriever,
        state_store: SessionStateStore,
        memory_formatter: MemoryFormatter,
        task_profile_inference: TaskProfileInference,
        compaction_config: CompactionConfig | None = None,
        session_store: SessionStore | None = None,
        ports: Any = None,
    ) -> None:
        self._memory_retriever = memory_retriever
        self._state_store = state_store
        self._memory_formatter = memory_formatter
        self._task_profile_inference = task_profile_inference
        self._compaction_config = compaction_config
        self._session_store = session_store
        # turn 层记忆组合缓存（Run 生命周期注入）；None 时执行路径与
        # 未重构前的逐语句行为一致（parity 基线）。
        self._memory_core_cache: MemoryCoreCache | None = None
        # 依赖反转——validator_chain 通过 ports 获取，避免反向 import。
        self._ports = ports
        self._validator_chain: Any = None

    def set_ports(self, ports: Any) -> None:
        """注入依赖反转 ports。"""
        self._ports = ports

    def set_memory_core_cache(self, cache: MemoryCoreCache | None) -> None:
        """注入 Run 级记忆组合缓存；None 表示关闭缓存（行为等价旧路径）。"""
        self._memory_core_cache = cache

    def _ensure_validator_chain(self) -> Any:
        if self._validator_chain is None:
            if self._ports is None:
                # ports 必须在 bootstrap 后注入；未注入即视为
                # 装配错误，fail-fast 而非静默 deferred import 反向依赖。
                raise RuntimeError(
                    "AssembleCoordinator._ports not injected — call "
                    "set_ports() with an ExecutionRuntimePorts instance "
                    "before assemble()."
                )
            self._validator_chain = self._ports.get_context_validator_chain()
        return self._validator_chain

    def set_compaction_config(self, compaction_config: CompactionConfig | None) -> None:
        self._compaction_config = compaction_config

    def set_session_store(self, session_store: SessionStore | None) -> None:
        self._session_store = session_store

    async def assemble(
        self,
        *,
        session_id: str,
        messages: list[Any],
        token_budget: int | None = None,
        prompt: str | None = None,
        request_mode: RequestMode | None = None,
        task_spec: TaskSpec | None = None,
    ) -> AssembleResult:
        """上下文装配。

        拆分为两层（三周期投影 Phase 1）：
        - turn 层：锚块构建 + 记忆组合（检索/选择/profile/预算）——
          输入不随逐轮消息变化，经 ``memory_core_cache`` 缓存；
        - iter 层：validator 与结果打包——每轮执行。

        缓存未注入时（``_memory_core_cache is None``）路径与重构前
        逐语句一致。
        """
        persisted_artifacts, history_artifact, persisted_memory_state = (
            await self._ensure_session_state_loaded(session_id, messages)
        )
        retrieval_anchor = _build_retrieval_anchor(
            session_id=session_id,
            history_artifact=history_artifact,
            persisted_memory_state=persisted_memory_state,
            state_store=self._state_store,
        )
        turn_state_block = _build_turn_state_block(
            session_id=session_id,
            persisted_memory_state=persisted_memory_state,
            state_store=self._state_store,
        )
        if self._memory_core_cache is not None:
            key = memory_core_cache_key(
                session_id=session_id,
                task_id=task_spec.task_id if task_spec is not None else None,
                prompt=prompt,
                request_mode=str(request_mode) if request_mode is not None else None,
                history_artifact_id=getattr(history_artifact, "artifact_id", None),
            )
            core = self._memory_core_cache.get(key)
            if core is None:
                core = await self._compute_memory_core(
                    session_id=session_id,
                    prompt=prompt,
                    messages=messages,
                    token_budget=token_budget,
                    request_mode=request_mode,
                    task_spec=task_spec,
                    anchor_blocks=retrieval_anchor,
                    persisted_artifacts=persisted_artifacts,
                )
                self._memory_core_cache.put(key, core)
        else:
            core = await self._compute_memory_core(
                session_id=session_id,
                prompt=prompt,
                messages=messages,
                token_budget=token_budget,
                request_mode=request_mode,
                task_spec=task_spec,
                anchor_blocks=retrieval_anchor,
                persisted_artifacts=persisted_artifacts,
            )
        return await self._compose_result(
            messages=messages,
            token_budget=token_budget,
            prompt=prompt,
            request_mode=request_mode,
            core=core,
            turn_state_block=turn_state_block,
        )

    async def _compute_memory_core(
        self,
        *,
        session_id: str,
        prompt: str | None,
        messages: list[Any],
        token_budget: int | None,
        request_mode: RequestMode | None,
        task_spec: TaskSpec | None,
        anchor_blocks: list[Any],
        persisted_artifacts: dict[str, Any],
    ) -> MemoryCore:
        """turn 层记忆组合：检索选择、三次 profile 推断、记忆预算。

        参与缓存键的输入在键生成处声明；本函数不感知缓存。
        """
        assembly_state = build_assembly_state_bundle(
            prompt=prompt,
            messages=messages,
            anchor_blocks=anchor_blocks,
            task_spec=task_spec,
        )
        _t0 = time.monotonic()
        initial_task_profile = self._task_profile_inference.infer(
            prompt=prompt,
            messages=messages,
            memory_blocks=assembly_state.anchor_blocks,
        )
        _profile1_ms = (time.monotonic() - _t0) * 1000
        _t0 = time.monotonic()
        evidence_strategy = self._memory_retriever.build_evidence_strategy(
            session_id=session_id,
            retrieval_query=assembly_state.retrieval_query,
            task_profile=initial_task_profile,
            request_mode=request_mode,
            merged_state_payload=assembly_state.merged_state_payload,
            anchor_blocks=assembly_state.anchor_blocks,
            task_spec=task_spec,
        )
        _strategy_ms = (time.monotonic() - _t0) * 1000
        # 交互式回合（短对话/问答）紧收检索预算——靠 goal / prompt 长度启发式：
        # 目标短且包含问号或解释关键词 → 3s 紧收预算；其他 → 20s 完整预算。
        _goal = (task_spec.goal if task_spec else prompt) or ""
        _interactive_mode = (
            len(_goal) < 80
            and any(kw in _goal for kw in ("?", "？", "怎么", "什么", "为什么", "解释", "help", "explain"))
        )
        _t0 = time.monotonic()
        _l2l3_results, _archive_results, evidence_packets = (
            await self._memory_retriever.retrieve_evidence(
                session_id=session_id,
                evidence_strategy=evidence_strategy,
                task_profile=initial_task_profile,
                retrieval_budget_s=(
                    self._memory_retriever.interactive_retrieval_budget_s
                    if _interactive_mode
                    else None
                ),
                llm_selector_enabled=not _interactive_mode,
            )
        )
        _retrieval_ms = (time.monotonic() - _t0) * 1000
        context_graph = build_context_graph(
            evidence_strategy=evidence_strategy,
            anchor_blocks=assembly_state.anchor_blocks,
            retrieval_packets=evidence_packets,
        )
        raw_memory_blocks = self._memory_formatter.format_blocks_from_packets(
            evidence_packets,
            graph=context_graph,
        )
        raw_context_blocks = [
            *assembly_state.anchor_blocks,
            *raw_memory_blocks,
        ]
        _t0 = time.monotonic()
        task_profile = self._task_profile_inference.infer(
            prompt=prompt,
            messages=messages,
            memory_blocks=raw_context_blocks,
        )
        _profile2_ms = (time.monotonic() - _t0) * 1000
        memory_plan = ContextPackLayout.from_blocks(raw_context_blocks)
        memory_plan.apply_task_profile(task_profile)
        selection_audit = BudgetAuditTrail()
        if token_budget is not None:
            selected_non_static_blocks, selection_decisions = BudgetOrchestrator(ports=self._ports).select_non_static_blocks(
                memory_plan.non_static_blocks(),
                budget=max(0, token_budget),
                task_profile=task_profile,
                zone="memory_context",
            )
            memory_plan.replace_non_static_blocks(selected_non_static_blocks)
            selection_audit = BudgetAuditTrail.from_decisions(selection_decisions)
        compacted_non_static_blocks = compact_context_blocks_for_turn(
            blocks=memory_plan.non_static_blocks(),
            token_budget=token_budget,
            compaction_config=self._compaction_config,
            persisted_artifacts=persisted_artifacts,
        )
        memory_plan.replace_non_static_blocks(compacted_non_static_blocks)
        if token_budget is not None:
            selected_non_static_blocks, selection_decisions = BudgetOrchestrator(ports=self._ports).select_non_static_blocks(
                memory_plan.non_static_blocks(),
                budget=max(0, token_budget),
                task_profile=task_profile,
                zone="memory_context",
            )
            memory_plan.replace_non_static_blocks(selected_non_static_blocks)
            selection_audit = merge_budget_audit_trails(
                selection_audit,
                BudgetAuditTrail.from_decisions(selection_decisions),
            )
        memory_budget_audit = selection_audit
        if token_budget is not None:
            non_static_blocks, decisions = BudgetOrchestrator(ports=self._ports).apply_dynamic_budget(
                memory_plan.non_static_blocks(),
                budget=max(0, token_budget),
                zone="memory_context",
                result_budget=max(0, token_budget),
            )
            memory_plan.replace_non_static_blocks(non_static_blocks)
            memory_budget_audit = merge_budget_audit_trails(
                selection_audit,
                BudgetAuditTrail.from_decisions(decisions),
            )
        _t0 = time.monotonic()
        task_profile = self._task_profile_inference.infer(
            prompt=prompt,
            messages=messages,
            memory_blocks=memory_plan.non_static_blocks(),
        )
        _profile3_ms = (time.monotonic() - _t0) * 1000
        logger.info(
            "memory_core_timing session=%s profile1=%.0fms strategy=%.0fms "
            "retrieval=%.0fms profile2=%.0fms profile3=%.0fms",
            session_id,
            _profile1_ms,
            _strategy_ms,
            _retrieval_ms,
            _profile2_ms,
            _profile3_ms,
        )
        return MemoryCore(
            retrieval_query=assembly_state.retrieval_query,
            anchor_sources=assembly_state.anchor_sources,
            evidence_strategy=evidence_strategy,
            context_graph=context_graph,
            raw_memory_blocks=raw_memory_blocks,
            task_profile=task_profile,
            memory_plan=memory_plan,
            memory_budget_audit=memory_budget_audit,
        )

    async def _compose_result(
        self,
        *,
        messages: list[Any],
        token_budget: int | None,
        prompt: str | None,
        request_mode: RequestMode | None,
        core: MemoryCore,
        turn_state_block: Any | None,
    ) -> AssembleResult:
        """iter 层打包：每轮注入滚动会话态块，再走 validator 链与组装。

        ``turn_state_block`` 是逐轮变化的 session_state 投影——不进
        MemoryCore（不污染缓存键与检索），在打包时叠加进 dynamic 区，
        保证 LLM 每轮看到最新会话态。
        """
        memory_plan = core.memory_plan
        if turn_state_block is not None:
            memory_plan = ContextPackLayout(
                dynamic_blocks=[turn_state_block, *memory_plan.dynamic_blocks],
                history_blocks=list(memory_plan.history_blocks),
                result_blocks=list(memory_plan.result_blocks),
                metadata=dict(memory_plan.metadata or {}),
            )
        validator_chain = self._ensure_validator_chain()
        validator_chain.token_budget = token_budget
        modified_prompt = await validator_chain.validate_and_transform(
            memory_plan, prompt
        )
        return AssembleResult(
            messages=messages,
            context_layout=ContextPackLayout(
                dynamic_blocks=list(memory_plan.dynamic_blocks),
                history_blocks=list(memory_plan.history_blocks),
                result_blocks=list(memory_plan.result_blocks),
                metadata={
                    "request_mode": request_mode,
                    "retrieval_query": core.retrieval_query,
                    "modified_prompt": modified_prompt,
                    "budget_audit": core.memory_budget_audit.to_metadata_dict(),
                },
            ),
            task_profile=core.task_profile,
            evidence_strategy=core.evidence_strategy,
            context_graph=core.context_graph,
        )

    async def _ensure_session_state_loaded(
        self,
        session_id: str,
        _messages: list[Any],
    ) -> tuple[dict[str, Any], Any | None, Any | None]:
        persisted_artifacts: dict[str, Any] = {}
        history_artifact = None
        persisted_memory_state = None
        # Step 3：Run 级三元组缓存——三者仅在压缩落盘时变化（失效由
        # 压缩重启路径显式触发），命中时跳过每迭代全树重载。
        if self._memory_core_cache is not None:
            cached = self._memory_core_cache.get_tree_state(session_id)
            if cached is not None:
                return cached
        existing = self._state_store.get_existing_state(session_id)
        if existing is not None and existing.has_content():
            if self._session_store is None:
                return (
                    persisted_artifacts,
                    history_artifact,
                    existing.model_copy(deep=True),
                )
            tree = await self._session_store.load(session_id)
            if tree is not None:
                engine = SessionProjectionEngine(tree)
                result = (
                    engine.replacement.get_persisted_artifacts(),
                    engine.history_compaction.get_latest_artifact(),
                    engine.get_latest_session_memory_state(),
                )
                if self._memory_core_cache is not None:
                    self._memory_core_cache.put_tree_state(session_id, result)
                return result
            fallback = (
                persisted_artifacts,
                history_artifact,
                existing.model_copy(deep=True),
            )
            if self._memory_core_cache is not None:
                self._memory_core_cache.put_tree_state(session_id, fallback)
            return fallback
        if self._session_store is not None:
            tree = await self._session_store.load(session_id)
            if tree is not None:
                engine = SessionProjectionEngine(tree)
                persisted_artifacts = engine.replacement.get_persisted_artifacts()
                history_artifact = engine.history_compaction.get_latest_artifact()
                persisted_state = engine.get_latest_session_memory_state()
                if persisted_state is not None and persisted_state.has_content():
                    self._state_store.hydrate_state(session_id, persisted_state)
                    persisted_memory_state = persisted_state.model_copy(deep=True)
        result = persisted_artifacts, history_artifact, persisted_memory_state
        if self._memory_core_cache is not None:
            self._memory_core_cache.put_tree_state(session_id, result)
        return result


def _build_retrieval_anchor(
    *,
    session_id: str,
    history_artifact: Any | None,
    persisted_memory_state: Any | None,
    state_store: Any,
) -> list[Any]:
    """Run 级检索锚（Step 2 归位）：只含 Run 内稳定的成分。

    - ``session_memory`` 块基于持久基线状态渲染（非滚动 state）；
    - 历史压缩块仅在压缩时变化。

    滚动会话态（recent_messages/todos/decisions 增量）不在此处——
    它由 ``_build_turn_state_block`` 每轮渲染、进 iter 层注入。
    """
    memory_block = state_store.build_persisted_memory_block(
        session_id,
        baseline_state=persisted_memory_state,
    )
    history_block = _history_compaction_block(history_artifact)
    return [
        *([memory_block] if memory_block is not None else []),
        *([history_block] if history_block is not None else []),
    ]


def _build_turn_state_block(
    *,
    session_id: str,
    persisted_memory_state: Any | None,
    state_store: Any,
) -> Any:
    """iter 层滚动会话态块：每轮基于最新 state 渲染（delta 自持久基线）。"""
    return state_store.build_context_block(
        session_id,
        baseline_state=persisted_memory_state,
    )


def _history_compaction_block(history_artifact: Any | None):
    if history_artifact is None:
        return None
    summary_text = getattr(history_artifact, "summary_text", None)
    rendered = render_history_compaction_block(summary_text if isinstance(summary_text, str) else None)
    if not rendered:
        return None
    from prismclaw.schema.content.context import ContextBlock

    return ContextBlock(
        id="history_compaction",
        zone="history",
        source="history_compaction",
        content=build_history_compaction_placeholder(getattr(history_artifact, "artifact_id", None)),
        stability="semi_stable",
        priority="critical",
        cacheable=False,
        truncatable=False,
        estimated_tokens=estimate_rendered_text_tokens(rendered),
        metadata={
            "wrapper": "history_compaction",
            "history_compaction": history_artifact.to_metadata_dict()
            if hasattr(history_artifact, "to_metadata_dict")
            else None,
        },
    )
