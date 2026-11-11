from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from typing import Any, Protocol

from prismclaw.schema.agent.context import MemoryAftermathBoundary
from prismclaw.schema.session.ids import derive_memory_session_key
from prismclaw.schema.llm.request_mode import RequestMode
from prismclaw.schema.audit.audit import RetrievalAuditLogger
from prismclaw.schema.audit.audit_types import RetrievalAuditEntry
from prismclaw.engine.compression.context_router import DynamicContextRouter
from prismclaw.engine.compression.memory_query_planner import build_evidence_strategy
from prismclaw.engine.compression.memory_selector import select_relevant_memories
from prismclaw.engine.foundation.metrics import emit
from prismclaw.schema.agent.task_types import TaskSpec
from prismclaw.schema.content.context import ContextBlock, TaskProfile
from prismclaw.schema.content.evidence import EvidencePacket, EvidenceStrategy
from prismclaw.schema.storage.storage_types import MemoryStore
from prismclaw.schema.memory.memory_graph import EdgeStoreProtocol

ARCHIVE_CACHE_MAX_ENTRIES = 200

# 记忆检索是回合准备阶段的「增强」步骤，不是硬依赖：
# 给整段检索一个总预算，避免 embeddings / DB / selector / graph 扩展等任一外部
# 子步骤挂起时无限期阻塞整个 agent 回合（表现为 UI 卡死、执行过程为 0）。
# 可用 PRISMCLAW_MEMORY_RETRIEVAL_BUDGET_S 覆盖（秒）。
try:
    _DEFAULT_RETRIEVAL_BUDGET_S = float(
        os.environ.get("PRISMCLAW_MEMORY_RETRIEVAL_BUDGET_S", "20")
    )
except (TypeError, ValueError):
    _DEFAULT_RETRIEVAL_BUDGET_S = 20.0
if _DEFAULT_RETRIEVAL_BUDGET_S <= 0:
    _DEFAULT_RETRIEVAL_BUDGET_S = 20.0
# 交互式回答/分析模式的紧收检索预算（Phase 2 记忆策略 a）：
# 实时咨询的答案来自当前会话上下文，跨会话检索只做增强——收紧
# 预算避免 20s 级检索拖住首轮装配。可用 PRISMCLAW_MEMORY_INTERACTIVE_BUDGET_S 覆盖。
try:
    _DEFAULT_INTERACTIVE_RETRIEVAL_BUDGET_S = float(
        os.environ.get("PRISMCLAW_MEMORY_INTERACTIVE_BUDGET_S", "3")
    )
except (TypeError, ValueError):
    _DEFAULT_INTERACTIVE_RETRIEVAL_BUDGET_S = 3.0
if _DEFAULT_INTERACTIVE_RETRIEVAL_BUDGET_S <= 0:
    _DEFAULT_INTERACTIVE_RETRIEVAL_BUDGET_S = 3.0

# 检索降级（SDD D2）：fallback 是确定性的关键词兜底，不带 LLM/向量/BM25，
# 自身必须有独立短超时——宁可无记忆，不可挂回合。
_FALLBACK_FETCH_TIMEOUT_S = 0.5
_FALLBACK_SCAN_LIMIT = 500
# 熔断：连续失败后跳过整段检索、直走 fallback，衰减窗口后恢复。
_RETRIEVAL_CIRCUIT_FAILURES = 3
_RETRIEVAL_CIRCUIT_COOLDOWN_S = 300.0
# fallback 注入硬顶（SDD Q3 默认：max(路由配额,5)；此处为安全上限）。
_FALLBACK_MAX_RESULTS = 8

logger = logging.getLogger(__name__)


class MemoryRetrievalSettings(Protocol):
    search_l2_quota: int
    search_l3_quota: int
    archive_max_results: int
    archive_enabled: bool
    archive_min_similarity: float
    memory_graph_enabled: bool


class _NullMemoryAftermathBoundary:
    async def after_turn(self, session_id: str) -> None:
        _ = session_id

    async def consolidate_session(self, session_id: str) -> bool:
        _ = session_id
        return False

    def set_session_store(self, session_store: Any) -> None:
        _ = session_store


class MemoryRetriever:
    def __init__(
        self,
        *,
        memory_store: MemoryStore | None = None,
        memory_settings: MemoryRetrievalSettings | None = None,
        session_store: Any = None,
        llm_client: Any = None,
        evolution_settings: Any = None,
        retrieval_audit_log: RetrievalAuditLogger | None = None,
        aftermath_boundary: MemoryAftermathBoundary | None = None,
        attribution_recorder: Any = None,
        edge_store: EdgeStoreProtocol | None = None,
        retrieval_budget_s: float | None = None,
        interactive_retrieval_budget_s: float | None = None,
        validation_ledger: Any = None,
    ) -> None:
        self._memory_store = memory_store
        self._memory_settings = memory_settings
        self._session_store = session_store
        self._llm_client = llm_client
        self._evolution_settings = evolution_settings
        self._retrieval_audit_log = retrieval_audit_log
        # SDD B1: injection bookkeeping — set when attribution_enabled.
        self._attribution_recorder = attribution_recorder
        # SDD D9/T1-5: 无归因环境的最小编码簿记（独立于 attribution 开关）。
        self._validation_ledger = validation_ledger
        self._edge_store = edge_store
        self._retrieval_budget_s = (
            retrieval_budget_s
            if retrieval_budget_s is not None and retrieval_budget_s > 0
            else _DEFAULT_RETRIEVAL_BUDGET_S
        )
        self.interactive_retrieval_budget_s = (
            interactive_retrieval_budget_s
            if interactive_retrieval_budget_s is not None
            and interactive_retrieval_budget_s > 0
            else _DEFAULT_INTERACTIVE_RETRIEVAL_BUDGET_S
        )
        self._l1_cache: dict[str, list[Any]] = {}
        self._archive_cache: dict[str, tuple[str, bool, list[Any]]] = {}
        self._aftermath_boundary = aftermath_boundary or _NullMemoryAftermathBoundary()
        # 检索熔断状态（SDD D2）：连续失败 → 衰减窗口内直走启发式 fallback。
        self._retrieval_fail_streak = 0
        self._retrieval_circuit_open_until = 0.0

    def set_session_store(self, session_store: Any) -> None:
        self._session_store = session_store
        self._aftermath_boundary.set_session_store(session_store)

    @staticmethod
    def derive_session_key(session_id: str) -> str:
        return derive_memory_session_key(session_id)

    async def get_l1_snapshot(self, session_id: str) -> list[Any]:
        if self._memory_store is None:
            return []
        if session_id in self._l1_cache:
            return self._l1_cache[session_id]
        session_key = self.derive_session_key(session_id)
        try:
            entries = await self._memory_store.index_get(session_key)
        except Exception:
            logger.warning(
                "L1 snapshot load failed for session %s",
                session_id,
                exc_info=True,
            )
            entries = []
        self._l1_cache[session_id] = entries
        return entries

    async def retrieve(
        self,
        *,
        session_id: str,
        retrieval_query: str | None,
        task_profile: TaskProfile,
        request_mode: RequestMode | None = None,
        include_canary: bool = False,
    ) -> tuple[list[Any], list[Any]]:
        evidence_strategy = self.build_evidence_strategy(
            session_id=session_id,
            retrieval_query=retrieval_query,
            task_profile=task_profile,
            request_mode=request_mode,
            merged_state_payload=None,
            anchor_blocks=[],
            task_spec=None,
        )
        l2l3_results, archive_results, _packets = await self.retrieve_evidence(
            session_id=session_id,
            evidence_strategy=evidence_strategy,
            task_profile=task_profile,
            include_canary=include_canary,
        )
        return l2l3_results, archive_results

    def build_evidence_strategy(
        self,
        *,
        session_id: str,
        retrieval_query: str | None,
        task_profile: TaskProfile,
        request_mode: RequestMode | None,
        merged_state_payload: dict[str, Any] | None,
        anchor_blocks: list[ContextBlock],
        task_spec: TaskSpec | None,
    ) -> EvidenceStrategy:
        if self._memory_settings is None:
            return EvidenceStrategy(
                strategy_id=f"{session_id}:ep:disabled",
                session_id=session_id,
                query_text=retrieval_query,
                strategy="disabled",
                notes=["memory_settings_unavailable"],
                anchor_sources=[block.source for block in anchor_blocks if block.source],
                merged_state_payload=dict(merged_state_payload or {}),
            )
        cfg = self._memory_settings

        l2_quota, l3_quota, archive_limit = DynamicContextRouter.allocate_quotas(
            task_profile=task_profile,
            settings=cfg,
            request_mode=request_mode,
        )

        return build_evidence_strategy(
            session_id=session_id,
            retrieval_query=retrieval_query,
            task_profile=task_profile,
            request_mode=request_mode,
            merged_state_payload=merged_state_payload,
            anchor_blocks=anchor_blocks,
            task_spec=task_spec,
            l2_quota=l2_quota,
            l3_quota=l3_quota,
            archive_limit=archive_limit,
            graph_available=bool(cfg.memory_graph_enabled),
        )

    async def retrieve_evidence(
        self,
        *,
        session_id: str,
        evidence_strategy: EvidenceStrategy,
        task_profile: TaskProfile,
        include_canary: bool = False,
        retrieval_budget_s: float | None = None,
        llm_selector_enabled: bool = True,
    ) -> tuple[list[Any], list[Any], list[EvidencePacket]]:
        """有界检索包装：记忆检索只做「增强」，不承担阻塞回合的责任。

        底层任一子步骤（embeddings / 向量库 / DB / selector / graph 扩展）
        挂起时，wait_for 在总预算耗尽后取消整段检索，降级为「无检索证据」，
        让回合继续进入 LLM 与工具执行——否则一次外部依赖卡死会让整个回合停在
        准备阶段，前端表现为 0 执行过程 / 页面假死。
        """
        if (
            self._memory_store is None
            or self._memory_settings is None
            or not evidence_strategy.query_text
        ):
            emit("memory_retrieval_total", 1.0, kind="counter", labels={"outcome": "disabled"})
            return [], [], []
        budget_s = (
            retrieval_budget_s
            if retrieval_budget_s is not None and retrieval_budget_s > 0
            else self._retrieval_budget_s
        )
        now_m = time.monotonic()
        if now_m < self._retrieval_circuit_open_until:
            # 熔断中：检索依赖栈连续失败，直走确定性 fallback（无外部依赖）。
            emit("memory_retrieval_total", 1.0, kind="counter", labels={"outcome": "circuit_open"})
            return await self._heuristic_fallback(
                session_id=session_id,
                evidence_strategy=evidence_strategy,
            )
        try:
            result = await asyncio.wait_for(
                self._retrieve_evidence_impl(
                    session_id=session_id,
                    evidence_strategy=evidence_strategy,
                    task_profile=task_profile,
                    include_canary=include_canary,
                    llm_selector_enabled=llm_selector_enabled,
                ),
                timeout=budget_s,
            )
        except asyncio.TimeoutError:
            emit(
                "memory_retrieval_total", 1.0, kind="counter", labels={"outcome": "timeout_fallback"}
            )
            logger.warning(
                "memory_retriever.retrieve_evidence budget exceeded (%.1fs); "
                "degrading to heuristic fallback session=%s — check embeddings/DB health",
                budget_s,
                session_id,
            )
            self._note_retrieval_failure(now_m)
            return await self._heuristic_fallback(
                session_id=session_id,
                evidence_strategy=evidence_strategy,
            )
        except Exception:
            # 非超时异常（SQLAlchemyError 等）同样降级为 fallback——
            # 上游 assemble_coordinator 无 try，不能把检索异常扬到回合外层。
            emit(
                "memory_retrieval_total", 1.0, kind="counter", labels={"outcome": "error_fallback"}
            )
            logger.warning(
                "memory_retriever.retrieve_evidence failed; degrading to "
                "heuristic fallback session=%s",
                session_id,
                exc_info=True,
            )
            self._note_retrieval_failure(now_m)
            return await self._heuristic_fallback(
                session_id=session_id,
                evidence_strategy=evidence_strategy,
            )
        self._retrieval_fail_streak = 0
        emit("memory_retrieval_total", 1.0, kind="counter", labels={"outcome": "ok"})
        return result

    def _note_retrieval_failure(self, now_m: float) -> None:
        self._retrieval_fail_streak += 1
        if self._retrieval_fail_streak >= _RETRIEVAL_CIRCUIT_FAILURES:
            self._retrieval_fail_streak = 0
            self._retrieval_circuit_open_until = now_m + _RETRIEVAL_CIRCUIT_COOLDOWN_S
            logger.warning(
                "memory retrieval circuit opened for %.0fs after %d consecutive failures",
                _RETRIEVAL_CIRCUIT_COOLDOWN_S,
                _RETRIEVAL_CIRCUIT_FAILURES,
            )

    async def _heuristic_fallback(
        self,
        *,
        session_id: str,
        evidence_strategy: EvidenceStrategy,
    ) -> tuple[list[Any], list[Any], list[EvidencePacket]]:
        """确定性检索降级（SDD D2）：扫 active 条目按关键词交集打分。

        零外部依赖（不调 BM25/向量/LLM），自带 0.5s 短超时；拿不到就空结果，
        绝不让回合挂在准备阶段。
        """
        session_key = self.derive_session_key(session_id)
        try:
            active_entries = await asyncio.wait_for(
                self._memory_store.list_entries_by_status(
                    session_key, ["active"], limit=_FALLBACK_SCAN_LIMIT
                ),
                timeout=_FALLBACK_FETCH_TIMEOUT_S,
            )
        except Exception:
            emit(
                "memory_retrieval_fallback", 1.0, kind="counter", labels={"outcome": "failed"}
            )
            logger.warning(
                "memory heuristic fallback fetch failed for %s", session_id, exc_info=True
            )
            return [], [], []
        query = str(evidence_strategy.query_text or "").strip()
        quota = sum(
            int(v)
            for v in (evidence_strategy.per_layer_limits or {}).values()
            if isinstance(v, (int, float)) and v > 0
        )
        budget = max(0, min(_FALLBACK_MAX_RESULTS, max(quota, 5)))
        if not active_entries or budget == 0 or not query:
            emit(
                "memory_retrieval_fallback", 1.0, kind="counter", labels={"outcome": "empty"}
            )
            return [], [], []
        entries = _rank_entries_by_keyword_intersection(active_entries, query)[:budget]
        packets = self._build_evidence_packets(l2l3_results=entries, archive_results=[])
        emit(
            "memory_retrieval_fallback",
            1.0,
            kind="counter",
            labels={"outcome": "ok", "size": str(len(entries))},
        )
        return entries, [], packets

    async def _retrieve_evidence_impl(
        self,
        *,
        session_id: str,
        evidence_strategy: EvidenceStrategy,
        task_profile: TaskProfile,
        include_canary: bool = False,
        llm_selector_enabled: bool = True,
    ) -> tuple[list[Any], list[Any], list[EvidencePacket]]:
        retrieval_query = evidence_strategy.query_text

        started_at = time.time()
        _impl_start_m = time.monotonic()
        session_key = self.derive_session_key(session_id)
        effective_include_canary = include_canary or _should_include_canary_for_session(
            session_id,
            self._evolution_settings,
        )
        cfg = self._memory_settings
        query_plan = evidence_strategy
        l2l3_results: list[Any] = []
        archive_results: list[Any] = []
        artifact_results: list[Any] = []
        l2l3_error = False
        archive_error = False
        archive_cache_hit = False
        memory_search_meta: dict[str, Any] = {}
        archive_search_meta: dict[str, Any] = {}

        _t0 = time.monotonic()
        try:
            raw_l2l3 = await self._memory_store.search(
                session_key,
                retrieval_query,
                layers=list(query_plan.memory_layers),
                per_layer_limits=dict(query_plan.per_layer_limits),
            )
            l2l3_results = _filter_temporal_memory_entries(
                raw_l2l3,
                temporal_mode=query_plan.temporal_mode,
                include_canary=effective_include_canary,
            )
            # SDD B5 (§5.9): scope routing — agent-scope entries leave the
            # default retrieval path (dedicated channel deferred). Legacy
            # NULL-scope entries pass through untouched.
            if bool(getattr(self._memory_settings, "scope_routing_enabled", False)):
                l2l3_results = [
                    entry for entry in l2l3_results
                    if str(getattr(entry, "scope", "") or "").lower() != "agent"
                ]
            memory_search_meta = _search_meta(self._memory_store, kind="memory")
        except Exception:
            l2l3_error = True
            logger.warning(
                "memory_store.search (L2/L3) failed for session %s",
                session_id,
                exc_info=True,
            )
        _l2l3_ms = (time.monotonic() - _t0) * 1000

        _t0 = time.monotonic()
        if cfg.archive_enabled and query_plan.archive_limit > 0:
            cached = self._archive_cache.get(session_id)
            if (
                cached is not None
                and cached[0] == retrieval_query
                and cached[1] is effective_include_canary
            ):
                archive_cache_hit = True
                archive_results = list(cached[2])
            else:
                try:
                    artifact_results = await self._memory_store.search_l4_artifacts(
                        session_key,
                        retrieval_query,
                        artifact_kinds=list(query_plan.artifact_kinds),
                        limit=query_plan.archive_limit,
                        min_similarity=cfg.archive_min_similarity,
                    )
                    artifact_results = (
                        list(artifact_results) if isinstance(artifact_results, list) else []
                    )
                    artifact_results = _filter_publishable_artifacts(
                        artifact_results,
                        include_canary=effective_include_canary,
                    )
                    archive_search_meta = _merge_search_meta(
                        _search_meta(self._memory_store, kind="artifact"),
                        _search_meta(self._memory_store, kind="archive"),
                    )
                    archive_results = _rank_archive_like_entries(
                        artifact_results
                    )[: query_plan.archive_limit]
                    if len(self._archive_cache) >= ARCHIVE_CACHE_MAX_ENTRIES:
                        oldest_session_id = next(iter(self._archive_cache))
                        del self._archive_cache[oldest_session_id]
                    self._archive_cache[session_id] = (
                        retrieval_query,
                        effective_include_canary,
                        archive_results,
                    )
                except Exception:
                    archive_error = True
                    logger.warning(
                        "memory_store.search_l4_artifacts failed for session %s",
                        session_id,
                        exc_info=True,
                    )
        _archive_ms = (time.monotonic() - _t0) * 1000

        # E7: 边扩展（graph_expansion_enabled 且 edge_store 可用时）
        _t0 = time.monotonic()
        if (
            query_plan.graph_expansion_enabled
            and self._edge_store is not None
            and l2l3_results
        ):
            l2l3_results = await self._expand_via_edges(
                session_key=session_key,
                seed_results=l2l3_results,
                retrieval_query=retrieval_query,
            )
        _edges_ms = (time.monotonic() - _t0) * 1000

        selector_metrics: dict[str, Any] = {}
        _t0 = time.monotonic()
        l2l3_results, archive_results = await select_relevant_memories(
            llm_client=self._llm_client if llm_selector_enabled else None,
            retrieval_query=retrieval_query,
            task_profile=task_profile,
            memory_candidates=l2l3_results,
            archive_candidates=archive_results,
            max_memory=max(1, sum(query_plan.per_layer_limits.values())),
            max_archives=max(0, query_plan.archive_limit),
            selector_metrics=selector_metrics,
            ranking_settings=self._memory_settings,
        )
        # SDD T1-8 影子 use 记账：attribution_enabled=False 时仍记录注入面
        # （仅遥测/日志，不写库）——闭合「注入→使用」反馈环的最小一环，
        # 供 curator 验证信号路径（D9）与现场抽样对照。
        if l2l3_results:
            emit(
                "memory_shadow_use_count",
                float(len(l2l3_results)),
                kind="counter",
            )
            logger.debug(
                "memory shadow injection session=%s ids=%s",
                session_id,
                [str(getattr(entry, "id", "")) for entry in l2l3_results],
            )
            # 无归因 ledger：记录本回合注入面，供 aftermath 产 task-outcome
            # 信号与 use_count 回写（SDD D9/T1-5 闭环）。
            if self._validation_ledger is not None:
                self._validation_ledger.note_injections(
                    session_id,
                    [str(getattr(entry, "id", "")) for entry in l2l3_results],
                )
        _selector_ms = (time.monotonic() - _t0) * 1000
        logger.info(
            "retrieval_timing session=%s l2l3=%.0fms archive=%.0fms edges=%.0fms "
            "selector=%.0fms total=%.0fms",
            session_id,
            _l2l3_ms,
            _archive_ms,
            _edges_ms,
            _selector_ms,
            (time.monotonic() - _impl_start_m) * 1000,
        )
        # SDD B1: every post-selector entry is what actually reaches the
        # prompt — record it for attribution (intra-turn dedup inside).
        self._record_injections(
            session_id=session_id,
            retrieval_query=retrieval_query,
            l2l3_results=l2l3_results,
            archive_results=archive_results,
            memory_search_meta=memory_search_meta,
        )
        await self._record_retrieval_audit(
            RetrievalAuditEntry(
                session_key=session_key,
                retrieval_query=retrieval_query,
                started_at=started_at,
                ended_at=time.time(),
                duration_s=max(0.0, time.time() - started_at),
                memory_candidate_count=int(selector_metrics.get("input_memory_count", len(l2l3_results))),
                archive_candidate_count=int(
                    selector_metrics.get("input_archive_count", len(archive_results))
                ),
                memory_text_candidate_count=int(memory_search_meta.get("text_candidate_count", 0)),
                memory_vector_candidate_count=int(memory_search_meta.get("vector_candidate_count", 0)),
                archive_text_candidate_count=int(archive_search_meta.get("text_candidate_count", 0)),
                archive_vector_candidate_count=int(archive_search_meta.get("vector_candidate_count", 0)),
                selected_memory_count=len(l2l3_results),
                selected_archive_count=len(archive_results),
                selected_layer_distribution=_selected_layer_distribution(l2l3_results),
                memory_source_mix=_normalize_mix(memory_search_meta.get("source_mix")),
                archive_source_mix=_normalize_mix(archive_search_meta.get("source_mix")),
                selector_strategy=str(selector_metrics.get("strategy", "unknown")),
                selector_fallback=bool(selector_metrics.get("fallback", False)),
                planner_strategy=query_plan.strategy,
                planner_temporal_mode=query_plan.temporal_mode,
                planner_memory_layers=list(query_plan.memory_layers),
                planner_artifact_kinds=list(query_plan.artifact_kinds),
                planner_graph_expansion_enabled=bool(query_plan.graph_expansion_enabled),
                archive_cache_hit=archive_cache_hit,
                memory_vector_fallback=bool(memory_search_meta.get("vector_fallback", False)),
                archive_vector_fallback=bool(archive_search_meta.get("vector_fallback", False)),
                l2l3_error=l2l3_error,
                archive_error=archive_error,
            )
        )
        evidence_packets = self._build_evidence_packets(
            l2l3_results=l2l3_results,
            archive_results=archive_results,
        )
        return l2l3_results, archive_results, evidence_packets

    async def after_turn(self, session_id: str) -> None:
        self._l1_cache.pop(session_id, None)
        self._archive_cache.pop(session_id, None)
        await self._aftermath_boundary.after_turn(session_id)

    async def consolidate_session(self, session_id: str) -> bool:
        return await self._aftermath_boundary.consolidate_session(session_id)

    async def _expand_via_edges(
        self,
        *,
        session_key: str,
        seed_results: list[Any],
        retrieval_query: str,
        max_hops: int = 2,
        max_edges: int = 100,
        max_expanded_entries: int = 5,
    ) -> list[Any]:
        """E7: 边扩展 — 种子记忆 → traverse → 条件求值 → merge 回候选列表。"""
        seed_ids = [
            str(getattr(e, "id", ""))
            for e in seed_results
            if getattr(e, "id", None)
        ]
        if not seed_ids:
            return seed_results

        # hub 预检（IG2 circuit breaker）
        fanout_threshold = 20
        safe_source_ids: list[str] = []
        for sid in seed_ids:
            try:
                deg = await self._edge_store.edge_degree(session_key, sid)
            except Exception:
                logger.debug("[E7] edge_degree failed for %s, skipping", sid, exc_info=True)
                continue
            if deg <= fanout_threshold:
                safe_source_ids.append(sid)
            else:
                logger.debug("[E7] skipping hub node %s (degree=%d)", sid, deg)
        if not safe_source_ids:
            return seed_results

        # 遍历
        try:
            edges = await self._edge_store.traverse(
                session_key, safe_source_ids,
                max_hops=max_hops, max_edges=max_edges,
            )
        except Exception:
            logger.warning("[E7] edge traversal failed for %s", session_key, exc_info=True)
            return seed_results
        if not edges:
            return seed_results

        # 条件求值
        try:
            applicable_edges = await self._edge_store.edges_applicable_in_context(
                edges, retrieval_query, self._llm_client,
            )
        except Exception:
            logger.warning("[E7] condition evaluation failed for %s", session_key, exc_info=True)
            return seed_results

        # 获取扩展条目 ID（排除已在种子中的），按边置信度降序排列
        seed_id_set = set(seed_ids)
        unique_targets = {}
        for e in applicable_edges:
            if e.target_id not in seed_id_set and e.target_id not in unique_targets:
                unique_targets[e.target_id] = e.confidence
        if not unique_targets:
            return seed_results

        # 限量：按置信度排序后截断
        expanded_target_ids = sorted(
            unique_targets, key=unique_targets.get, reverse=True,
        )[:max_expanded_entries]
        expanded_entries = await self._fetch_entries_by_ids(session_key, expanded_target_ids)

        # 溯源标记（IG5：保留原始 origin_stage，新增 retrieval_path 动态属性）
        for entry in expanded_entries:
            entry.retrieval_path = "edge_expansion"

        # merge：扩展条目追加到末尾，由下游 select_relevant_memories 精排
        logger.info(
            "[E7] edge expansion: session=%s seeds=%d edges=%d applicable=%d expanded=%d",
            session_key, len(seed_ids), len(edges),
            len(applicable_edges), len(expanded_entries),
        )
        return list(seed_results) + expanded_entries

    async def _fetch_entries_by_ids(
        self, session_key: str, entry_ids: list[str],
    ) -> list[Any]:
        """按 ID 列表获取完整记忆条目（E7：使用 PG 后端，避免 L1 镜像截断）。"""
        if not entry_ids or self._memory_store is None:
            return []
        try:
            return await self._memory_store.get_entries_by_ids(session_key, entry_ids)
        except Exception:
            logger.warning("[E7] get_entries_by_ids failed for %s", session_key, exc_info=True)
            return []

    async def _record_retrieval_audit(self, entry: RetrievalAuditEntry) -> None:
        if self._retrieval_audit_log is None:
            return
        try:
            await self._retrieval_audit_log.record(entry)
        except Exception:
            logger.warning("retrieval audit record failed for %s", entry.session_key, exc_info=True)

    def _record_injections(
        self,
        *,
        session_id: str,
        retrieval_query: str,
        l2l3_results: list[Any],
        archive_results: list[Any],
        memory_search_meta: dict[str, Any],
    ) -> None:
        """SDD B1: buffer the final (post-selector) injection set for the turn.

        Never raises — attribution is best-effort bookkeeping.
        """
        recorder = self._attribution_recorder
        if recorder is None:
            return
        if not bool(getattr(self._memory_settings, "attribution_enabled", False)):
            return
        try:
            query_embedding = memory_search_meta.get("query_embedding")
            for entry in l2l3_results:
                recorder.record_injection(
                    session_id,
                    entry_id=str(getattr(entry, "id", "")),
                    entry_content=str(getattr(entry, "content", "") or ""),
                    query_text=retrieval_query,
                    query_embedding=query_embedding,
                    relevance_score=getattr(entry, "score", None),
                )
            for entry in archive_results:
                recorder.record_injection(
                    session_id,
                    entry_id=str(getattr(entry, "id", "")),
                    entry_content=str(
                        getattr(entry, "summary", None)
                        or getattr(entry, "content", "")
                        or ""
                    ),
                    query_text=retrieval_query,
                    query_embedding=None,  # archive search meta lacks the vec (B1 scope)
                    relevance_score=getattr(entry, "similarity", None),
                )
        except Exception:
            logger.warning(
                "injection recording failed for %s", session_id, exc_info=True
            )


    def _build_evidence_packets(
        self,
        *,
        l2l3_results: list[Any],
        archive_results: list[Any],
    ) -> list[EvidencePacket]:
        packets: list[EvidencePacket] = []
        for entry in l2l3_results:
            packet_id = f"memory:{getattr(entry, 'id', '')}"
            packets.append(
                EvidencePacket(
                    packet_id=packet_id,
                    kind="memory",
                    source=f"memory_{str(getattr(entry, 'layer', '')).lower()}",
                    title=f"{getattr(entry, 'layer', '')} {getattr(entry, 'type', 'memory')}",
                    content=str(getattr(entry, "content", "")),
                    summary=str(getattr(entry, "content", ""))[:160],
                    layer=str(getattr(entry, "layer", "")) or None,
                    source_ref=getattr(entry, "id", None),
                    source_refs=[
                        ref
                        for ref in getattr(entry, "source_refs", [])
                        if isinstance(ref, str) and ref.strip()
                    ],
                    confidence=_normalize_confidence(getattr(entry, "confidence", None)),
                    freshness=_freshness_bucket(getattr(entry, "updated_at", None)),
                    provenance={
                        "entry_type": getattr(entry, "type", None),
                        "scope": getattr(entry, "scope", None),
                        "source_session_id": getattr(entry, "source_session_id", None),
                        "retrieval_path": getattr(entry, "retrieval_path", "direct"),
                    },
                    metadata={
                        "graph_group_id": getattr(entry, "graph_group_id", None),
                        "score": getattr(entry, "score", None),
                        "stability_score": getattr(entry, "stability_score", None),
                        "origin_stage": getattr(entry, "origin_stage", None),
                        "supersedes": list(getattr(entry, "supersedes", []) or []),
                    },
                )
            )
        for entry in archive_results:
            artifact_kind = getattr(entry, "artifact_kind", None)
            packet_kind = "artifact" if artifact_kind else "archive"
            packet_id = f"{packet_kind}:{getattr(entry, 'id', '')}"
            packets.append(
                EvidencePacket(
                    packet_id=packet_id,
                    kind=packet_kind,  # type: ignore[arg-type]
                    source="memory_archives",
                    title=str(artifact_kind or "archive"),
                    content=str(getattr(entry, "summary", "")),
                    summary=str(getattr(entry, "summary", ""))[:180],
                    layer="L4",
                    source_ref=getattr(entry, "id", None),
                    source_refs=[
                        ref
                        for ref in getattr(entry, "source_refs", [])
                        if isinstance(ref, str) and ref.strip()
                    ],
                    confidence=0.5 if getattr(entry, "low_confidence", False) else 0.8,
                    freshness=_freshness_bucket(
                        getattr(entry, "updated_at", None) or getattr(entry, "created_at", None)
                    ),
                    provenance={
                        "session_id": getattr(entry, "session_id", None),
                        "artifact_kind": artifact_kind,
                    },
                    metadata={
                        "artifact_kind": artifact_kind,
                        "graph_group_id": getattr(entry, "graph_group_id", None),
                        "score": getattr(entry, "score", None),
                        "similarity": getattr(entry, "similarity", None),
                        "payload": getattr(entry, "payload", {}) if artifact_kind else {},
                    },
                )
            )
        return packets


def _normalize_confidence(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    return None


def _freshness_bucket(timestamp: Any) -> str:
    if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
        return "unknown"
    age_s = max(0.0, time.time() - float(timestamp))
    if age_s < 3600:
        return "fresh"
    if age_s < 86400:
        return "recent"
    if age_s < 86400 * 30:
        return "warm"
    return "stale"


def _selected_layer_distribution(entries: list[Any]) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for entry in entries:
        layer = str(getattr(entry, "layer", "") or "unknown")
        distribution[layer] = distribution.get(layer, 0) + 1
    return distribution


def _filter_temporal_memory_entries(
    entries: list[Any],
    *,
    temporal_mode: str,
    include_canary: bool,
) -> list[Any]:
    if temporal_mode != "current_only":
        return list(entries)
    filtered: list[Any] = []
    now = time.time()
    for entry in entries:
        publish_state = str(getattr(entry, "publish_state", "") or "").strip().lower()
        if publish_state in {"draft", "rejected"}:
            continue
        if publish_state == "canary" and not include_canary:
            continue
        valid_to = getattr(entry, "valid_to", None)
        winner_flag = getattr(entry, "winner_flag", True)
        expires_at = getattr(entry, "expires_at", None)
        if valid_to is not None and float(valid_to) < now:
            continue
        if expires_at is not None and float(expires_at) < now:
            continue
        if winner_flag is False:
            continue
        filtered.append(entry)
    return filtered


def _filter_publishable_artifacts(entries: list[Any], *, include_canary: bool) -> list[Any]:
    filtered: list[Any] = []
    for entry in entries:
        publish_state = str(getattr(entry, "publish_state", "") or "").strip().lower()
        if publish_state in {"draft", "rejected"}:
            continue
        if publish_state == "canary" and not include_canary:
            continue
        filtered.append(entry)
    return filtered


def _search_meta(memory_store: Any, *, kind: str) -> dict[str, Any]:
    attr_name = {
        "memory": "last_memory_search_meta",
        "archive": "last_archive_search_meta",
        "artifact": "last_artifact_search_meta",
    }.get(kind, "last_archive_search_meta")
    raw = getattr(memory_store, attr_name, {})
    return raw if isinstance(raw, dict) else {}


def _normalize_mix(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, int] = {}
    for key, value in raw.items():
        try:
            normalized[str(key)] = int(value)
        except Exception:
            continue
    return normalized


def _rank_archive_like_entries(entries: list[Any]) -> list[Any]:
    return sorted(
        entries,
        key=lambda entry: (
            float(getattr(entry, "score", None) or getattr(entry, "similarity", None) or 0.0),
            float(getattr(entry, "updated_at", None) or getattr(entry, "created_at", None) or 0.0),
            float(getattr(entry, "created_at", None) or 0.0),
        ),
        reverse=True,
    )


def _merge_search_meta(*metas: dict[str, Any]) -> dict[str, Any]:
    merged_mix: dict[str, int] = {}
    text_count = 0
    vector_count = 0
    selected_count = 0
    vector_requested = False
    vector_fallback = False
    for meta in metas:
        if not isinstance(meta, dict):
            continue
        text_count += int(meta.get("text_candidate_count", 0) or 0)
        vector_count += int(meta.get("vector_candidate_count", 0) or 0)
        selected_count += int(meta.get("selected_count", 0) or 0)
        vector_requested = vector_requested or bool(meta.get("vector_requested", False))
        vector_fallback = vector_fallback or bool(meta.get("vector_fallback", False))
        for key, value in _normalize_mix(meta.get("source_mix")).items():
            merged_mix[key] = merged_mix.get(key, 0) + int(value)
    return {
        "text_candidate_count": text_count,
        "vector_candidate_count": vector_count,
        "selected_count": selected_count,
        "source_mix": merged_mix,
        "vector_requested": vector_requested,
        "vector_fallback": vector_fallback,
    }


def _should_include_canary_for_session(session_id: str, evolution_settings: Any) -> bool:
    dream_settings = getattr(evolution_settings, "dream", None)
    if dream_settings is None:
        return False
    rollout_percentage = int(getattr(dream_settings, "effective_canary_rollout_percentage", 0) or 0)
    if rollout_percentage <= 0:
        return False
    if rollout_percentage >= 100:
        return True
    rollout_salt = str(getattr(dream_settings, "canary_rollout_salt", "dream-canary") or "dream-canary")
    digest = hashlib.sha1(f"{rollout_salt}:{session_id}".encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 100
    return bucket < rollout_percentage


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _query_terms(text: str) -> set[str]:
    """查询词元：英文/数字词 + CJK 字符 bigram（确定性、零依赖）。"""
    lowered = text.lower()
    terms = set(re.findall(r"[a-z0-9_]+", lowered))
    cjk_chars = _CJK_RE.findall(lowered)
    terms.update(
        cjk_chars[i] + cjk_chars[i + 1] for i in range(max(0, len(cjk_chars) - 1))
    )
    return {term for term in terms if term}


def _recency_weight(timestamp: Any) -> float:
    """30 天线性衰减的新鲜度权重（0~1），fallback 打分微调用。"""
    try:
        age = max(0.0, time.time() - float(timestamp))
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, 1.0 - age / (30 * 86400.0))


def _rank_entries_by_keyword_intersection(
    entries: list[Any], query: str
) -> list[Any]:
    """按关键词交集 + 新鲜度微调排序（fallback 专用，无 DB/LLM 依赖）。

    零命中条目一律剔除（去零）：纯新鲜度不得让无关记忆混入注入面。
    """
    terms = _query_terms(query)

    def hit_ratio(entry: Any) -> float:
        content_terms = _query_terms(str(getattr(entry, "content", "") or "").lower())
        return float(len(terms & content_terms)) / max(1.0, float(len(terms)))

    def score(entry: Any) -> float:
        value = hit_ratio(entry)
        if not terms:
            value = 0.0
        return value + 0.1 * _recency_weight(getattr(entry, "updated_at", None))

    candidates = [(score(entry), hit_ratio(entry), entry) for entry in entries]
    if terms:
        candidates = [candidate for candidate in candidates if candidate[1] > 0.0]
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [entry for _score, _hit, entry in candidates]
