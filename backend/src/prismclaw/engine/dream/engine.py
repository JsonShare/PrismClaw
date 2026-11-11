from __future__ import annotations

import json
import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from prismclaw.schema.llm.types import LLMStreamFn
from prismclaw.schema.llm.request import PackedLLMRequest
from prismclaw.schema.dream.dream import DreamAuditLogger
from prismclaw.schema.dream.dream_types import DreamAuditEntry
from prismclaw.engine.dream.candidates import DreamCandidateStore, DreamMemoryCandidate
from prismclaw.engine.dream.compiler import DreamCompiler
from prismclaw.schema.dream.dream import DreamGovernanceStore
from prismclaw.schema.dream import (
    DreamGovernanceRecord,
    DreamPublishChannel,
    DreamReviewStatus,
    DreamTargetType,
)
from prismclaw.engine.dream.governance.governance_runtime import DreamGovernanceRuntime
from prismclaw.engine.dream.models import (
    DreamArtifactPublishResult,
    DreamPublishResult,
    DreamPublishableArtifact,
    DreamPublishableMemory,
)
from prismclaw.engine.dream.publishing import (
    build_publishable_rem_artifacts as _build_publishable_rem_artifacts,
    build_rem_artifacts as _build_rem_artifacts,
)
from prismclaw.schema.memory.memory_types import MemoryEntry
from prismclaw.schema.storage.storage_types import MemoryStore
from prismclaw.schema.storage.storage_types import SessionStore

logger = logging.getLogger(__name__)
_DREAM_MAX_SCAN = 200
_REM_MAX_ITEMS = 12
_REM_FEEDBACK_LIMIT = 3


class DreamEngine:
    """
    Dreaming Engine for Memory Consolidation.
    
    Phases:
    1. Light Sleep: Deduplicate recent L1 entries and move solid facts to L2.
    2. Deep Sleep: Cluster L2 facts and L3 procedures. Prune contradictory or outdated facts.
    3. REM Sleep: Generate knowledge graphs or higher-level abstractions from clustered facts, 
                  and archive them to L4.
    """

    def __init__(
        self,
        memory_store: MemoryStore,
        session_store: SessionStore,
        llm_client: LLMStreamFn,
        *,
        audit_log: DreamAuditLogger | None = None,
        candidate_store: DreamCandidateStore | None = None,
        governance_store: DreamGovernanceStore | None = None,
        memory_publish_channel: str | None = None,
        artifact_publish_channel: str | None = None,
        shadow_publish_enabled: bool = False,
        shadow_memory_publish_enabled: bool | None = None,
        shadow_artifact_publish_enabled: bool | None = None,
        canary_rollout_percentage: int = 0,
        canary_rollout_salt: str = "dream-canary",
        light_sleep_threshold_s: float = 3600,  # 1 hour
        deep_sleep_threshold_s: float = 86400,  # 1 day
        rem_sleep_threshold_s: float = 604800,  # 7 days
        tiers_enabled: bool = False,
        contest_exit_days: int = 30,
        deep_merge_min_positive: int = 1,
        memory_forgetting_budget_entries: int = 0,
        correction_learning_enabled: bool = False,
        edge_store: Any = None,
    ) -> None:
        self._memory = memory_store
        self._session = session_store
        self._llm = llm_client
        self._audit_log = audit_log
        self._candidate_store = candidate_store
        self._governance_store = governance_store
        resolved_shadow_memory = (
            shadow_publish_enabled if shadow_memory_publish_enabled is None else shadow_memory_publish_enabled
        )
        resolved_shadow_artifact = (
            shadow_publish_enabled
            if shadow_artifact_publish_enabled is None
            else shadow_artifact_publish_enabled
        )
        self._memory_publish_channel = _normalize_publish_channel(
            memory_publish_channel,
            fallback_shadow=resolved_shadow_memory,
        )
        self._artifact_publish_channel = _normalize_publish_channel(
            artifact_publish_channel,
            fallback_shadow=resolved_shadow_artifact,
        )
        self._light_sleep_threshold_s = light_sleep_threshold_s
        self._deep_sleep_threshold_s = deep_sleep_threshold_s
        self._rem_sleep_threshold_s = rem_sleep_threshold_s
        # SDD B3 (§5.3): three-tier consolidation replaces the legacy
        # clock-driven LLM phases when enabled.
        self._tiers_enabled = tiers_enabled
        self._contest_exit_days = contest_exit_days
        self._deep_merge_min_positive = deep_merge_min_positive
        self._memory_forgetting_budget_entries = memory_forgetting_budget_entries
        self._correction_learning_enabled = correction_learning_enabled
        self._edge_store = edge_store
        self._compiler = DreamCompiler(
            memory_store=memory_store,
            llm_client=llm_client,
            candidate_store=candidate_store,
            publish_memory=self._publish_candidate_memory,
            stability_score_fn=_estimate_stability_score,
        )
        self._governance_runtime = DreamGovernanceRuntime(
            memory_store=memory_store,
            governance_store=governance_store,
            memory_publish_channel=self._memory_publish_channel,
            artifact_publish_channel=self._artifact_publish_channel,
            canary_rollout_percentage=canary_rollout_percentage,
            canary_rollout_salt=canary_rollout_salt,
        )

    async def trigger_dream_cycle(self, session_key: str) -> dict[str, Any]:
        """
        Manually or via cron trigger a full dream cycle for a given user/session_key.
        """
        logger.info("Starting dream cycle for %s", session_key)
        start_time = datetime.now(timezone.utc).timestamp()
        batch_id = _build_dream_batch_id(session_key, trigger="manual")

        light_res = await self._run_phase_with_audit(
            session_key,
            "light_sleep",
            trigger="manual",
            batch_id=batch_id,
            executor=self._light_sleep_impl,
        )
        deep_res = await self._run_phase_with_audit(
            session_key,
            "deep_sleep",
            trigger="manual",
            batch_id=batch_id,
            executor=self._deep_sleep_impl,
        )
        rem_res = await self._run_phase_with_audit(
            session_key,
            "rem_sleep",
            trigger="manual",
            batch_id=batch_id,
            executor=self._rem_sleep_impl,
        )

        end_time = datetime.now(timezone.utc).timestamp()

        return {
            "session_key": session_key,
            "duration_s": round(end_time - start_time, 2),
            "phases": {
                "light_sleep": light_res,
                "deep_sleep": deep_res,
                "rem_sleep": rem_res,
            }
        }

    async def run_light(self, session_key: str) -> dict[str, Any]:
        return await self._run_phase_with_audit(
            session_key,
            "light_sleep",
            trigger="direct",
            batch_id=_build_dream_batch_id(session_key, trigger="direct"),
            executor=self._light_sleep_impl,
        )

    async def run_deep(self, session_key: str) -> dict[str, Any]:
        return await self._run_phase_with_audit(
            session_key,
            "deep_sleep",
            trigger="direct",
            batch_id=_build_dream_batch_id(session_key, trigger="direct"),
            executor=self._deep_sleep_impl,
        )

    async def run_rem(self, session_key: str) -> dict[str, Any]:
        return await self._run_phase_with_audit(
            session_key,
            "rem_sleep",
            trigger="direct",
            batch_id=_build_dream_batch_id(session_key, trigger="direct"),
            executor=self._rem_sleep_impl,
        )

    async def run_nightly(self, session_key: str, *, include_light: bool = False) -> dict[str, Any]:
        batch_id = _build_dream_batch_id(session_key, trigger="nightly")
        result: dict[str, Any] = {}
        if include_light:
            result["light_sleep"] = await self._run_phase_with_audit(
                session_key,
                "light_sleep",
                trigger="nightly",
                batch_id=batch_id,
                executor=self._light_sleep_impl,
            )
        result["deep_sleep"] = await self._run_phase_with_audit(
            session_key,
            "deep_sleep",
            trigger="nightly",
            batch_id=batch_id,
            executor=self._deep_sleep_impl,
        )
        result["rem_sleep"] = await self._run_phase_with_audit(
            session_key,
            "rem_sleep",
            trigger="nightly",
            batch_id=batch_id,
            executor=self._rem_sleep_impl,
        )
        return result

    async def _light_sleep(self, session_key: str) -> dict[str, Any]:
        result, _llm_cost = await self._light_sleep_impl(session_key)
        return result

    async def _light_sleep_impl(self, session_key: str) -> tuple[dict[str, Any], int]:
        """
        Light Sleep: 
        - Scan L1 (Redis) for entries older than `light_sleep_threshold_s`.
        - Deduplicate them via LLM.
        - Promote valid facts to L2 (SQLite/Postgres).
        """
        logger.info("[%s] Entering Light Sleep", session_key)
        # SDD B3: tiers replace the legacy LLM consolidation. Light tier is
        # deterministic (zero LLM): exact dedup + repeat-pattern suggestions.
        # Timing note: runs on the idle scan rather than a session-end hook —
        # zero-LLM so the latency difference is immaterial.
        if self._tiers_enabled:
            from prismclaw.engine.dream.tiers import run_light_tier

            stats = await run_light_tier(self._memory, session_key)
            return {
                "promoted_to_l2": 0,
                "deduplicated": stats["deduped"],
                "tier": "light",
                "groups": stats["groups"],
                "suggestions": stats["suggestions"],
            }, 0
        l1_entries = await self._memory.index_get(session_key)
        if not l1_entries:
            return {"promoted_to_l2": 0, "deduplicated": 0}, 0

        now = datetime.now(timezone.utc).timestamp()
        
        # V4.1: Delay Active Session Dreaming
        # We significantly extend the wait time for dreaming to allow Claude's native Prompt Caching 
        # to handle short-term history losslessly. We only dream (archive) when the session goes truly cold.
        effective_threshold_s = max(self._light_sleep_threshold_s, 12 * 3600)  # Wait at least 12 hours
        
        candidates = [
            e for e in l1_entries
            if now - e.created_at > effective_threshold_s
        ]

        if not candidates:
            return {"promoted_to_l2": 0, "deduplicated": 0}, 0

        # SDD docs/memory-dream-sdd.md §5.0 (A1): L1 carries previews only.
        # Hydrate full text from L2 before any consumption (LLM consolidation
        # AND the fallback path below) — never feed truncated previews into
        # semantic decisions.
        candidates = await self._hydrate_fulltext(session_key, candidates)
        if not candidates:
            return {"promoted_to_l2": 0, "deduplicated": 0}, 0

        # Use LLM to review candidates
        contents = "\\n---\\n".join([f"ID: {c.id}\\nContent: {c.content}" for c in candidates])
        prompt = (
            "你是一个记忆整理引擎（浅睡阶段）。请审视以下短期记忆，提取出确定的、长期有效的事实或规则。\n"
            "忽略闲聊、临时确认或无效信息。\n"
            "如果有重复内容，请合并。\n"
            "【V4 架构要求：Contextual Chunking】在提取每个事实/规则时，必须在前置生成一段 `<context>...</context>`，"
            "简要说明这个事实产生的背景（比如：在解决某个报错时、在讨论某个特定的业务逻辑时），以便在脱离当前对话流后仍具有清晰的语境。\n"
            "请以 JSON 格式输出，格式为一个列表：[{\"original_ids\": [\"id1\", \"id2\"], \"context\": \"...\", \"consolidated_fact\": \"...\", \"type\": \"facts/procedures\"}]\n"
            f"记忆列表:\n{contents}"
        )

        promoted_count = 0
        dedup_count = 0
        llm_cost = 0
        try:
            response = await self._llm.complete(
                request=PackedLLMRequest(
                    messages=[{"role": "user", "content": prompt}],
                    system="",
                    context_payload=None,
                    cache_strategy={},
                )
            )
            llm_cost = int(getattr(getattr(response, "usage", None), "total_tokens", 0) or 0)
            raw_text = response.text.strip()
            
            # Robust JSON extraction
            import re
            json_match = re.search(r'\[\s*\{.*?\}\s*\]', raw_text, re.DOTALL)
            if json_match:
                raw_text = json_match.group(0)
            elif raw_text.startswith("```json"):
                raw_text = raw_text[7:-3].strip()
            
            actions = json.loads(raw_text)
            import uuid
            for action in actions:
                fact_body = action.get("consolidated_fact")
                context_prefix = action.get("context", "")
                fact_type = action.get("type", "facts")
                orig_ids = action.get("original_ids", [])
                if not fact_body:
                    continue
                
                # V4 Contextual Chunking: Combine context and body
                contextual_fact = f"<context>{context_prefix}</context>\n{fact_body}" if context_prefix else fact_body

                new_entry = MemoryEntry(
                    id=f"mem_{uuid.uuid4().hex[:12]}",
                    layer="L2" if fact_type == "facts" else "L3",
                    type=fact_type,
                    content=contextual_fact,
                    created_at=now,
                    updated_at=now,
                )
                await self._memory.store(session_key, new_entry)
                promoted_count += 1
                
                # Cleanup: 下架被合并的原件——必须 archive（L2 status=archived
                # + L1 驱逐），仅删 L1 索引会让原件继续 active、与合并件并存
                # 制造副本（SDD D4）。
                await self._cleanup_light_sleep_source_ids(session_key, orig_ids)
                dedup_count += len(orig_ids)

        except Exception as e:
            logger.warning("[%s] Light sleep LLM processing failed: %s", session_key, e)
            fallback_result, fallback_cost = await self._fallback_light_sleep_candidates(
                session_key,
                candidates,
                reason=f"light_sleep_llm_failed:{type(e).__name__}",
            )
            llm_cost += fallback_cost
            promoted_count += int(fallback_result.get("promoted_to_l2", 0))
            dedup_count += int(fallback_result.get("deduplicated", 0))
            return fallback_result, llm_cost

        return {"promoted_to_l2": promoted_count, "deduplicated": dedup_count}, llm_cost

    async def _hydrate_fulltext(
        self, session_key: str, candidates: list[MemoryEntry]
    ) -> list[MemoryEntry]:
        """Replace L1 preview contents with full L2 text (SDD A1).

        Entries whose full text cannot be resolved (archived / deleted /
        backend miss) are skipped entirely — a skipped candidate is a no-op,
        a truncated candidate is semantic corruption fed into the LLM.
        """
        try:
            contents = await self._memory.get_contents_by_ids(
                session_key, [c.id for c in candidates]
            )
        except Exception:
            logger.warning(
                "[%s] L1 fulltext hydration failed; skipping light sleep batch",
                session_key,
                exc_info=True,
            )
            return []
        hydrated: list[MemoryEntry] = []
        for candidate in candidates:
            full = contents.get(candidate.id)
            if not full:
                logger.warning(
                    "[%s] L1 entry %s has no active L2 fulltext; skipped",
                    session_key,
                    candidate.id,
                )
                continue
            if full == candidate.content:
                hydrated.append(candidate)
            else:
                hydrated.append(candidate.model_copy(update={"content": full}))
        return hydrated

    async def _deep_sleep(self, session_key: str) -> dict[str, Any]:
        result, _llm_cost = await self._deep_sleep_impl(session_key)
        return result

    async def _deep_sleep_impl(self, session_key: str) -> tuple[dict[str, Any], int]:
        logger.info("[%s] Entering Deep Sleep", session_key)
        compile_result, llm_cost = await self._compiler.compile_candidate_lane(session_key)
        # SDD B3: tiers replace the legacy nightly semantic dedup. Mid tier
        # is deterministic bookkeeping (validation writeback / decay / CONTEST
        # exits); deep tier is the ONLY evidence-gated semantic merge. The
        # evidence gate naturally throttles merge frequency (SDD: weekly —
        # pairs without positive validation history never qualify).
        if self._tiers_enabled:
            from prismclaw.engine.dream.tiers import run_deep_tier, run_mid_tier

            mid_stats = await run_mid_tier(
                self._memory,
                session_key,
                contest_exit_days=self._contest_exit_days,
                budget_entries=self._memory_forgetting_budget_entries,
                edge_store=self._edge_store,
            )
            deep_stats = await run_deep_tier(
                self._memory,
                self._llm,
                session_key,
                min_positive=self._deep_merge_min_positive,
                correction_learning_enabled=self._correction_learning_enabled,
                edge_store=self._edge_store,
            )
            return (
                {
                    "tier": "mid+deep",
                    **mid_stats,
                    "deep_pairs_examined": deep_stats["pairs_examined"],
                    "deep_merged": deep_stats["merged"],
                    "correction_learned": deep_stats.get("correction_learned", 0),
                    **compile_result,
                },
                llm_cost,
            )
        entries = await self._memory.search(
            session_key,
            "",
            layers=["L2", "L3"],
            limit=_DREAM_MAX_SCAN,
        )
        entries = [entry for entry in entries if _is_serving_visible_memory(entry, include_canary=False)]
        grouped: dict[tuple[str, str, str], list[MemoryEntry]] = {}
        for entry in entries:
            normalized = _normalize_memory_text(entry.content)
            if not normalized:
                continue
            grouped.setdefault((entry.layer, entry.type, normalized), []).append(entry)

        clustered_groups = 0
        pruned_entries = 0
        for (_layer, _type, _normalized), group in grouped.items():
            if len(group) < 2:
                continue
            clustered_groups += 1
            survivor = max(group, key=lambda item: (item.updated_at, item.created_at))
            superseded_ids = sorted(entry.id for entry in group if entry.id != survivor.id)
            cluster_id = _build_cluster_id(session_key, survivor.layer, survivor.type, _normalized)
            await self._memory.store(
                session_key,
                survivor.model_copy(
                    update={
                        "origin_stage": "deep",
                        "cluster_id": cluster_id,
                        "supersedes": superseded_ids,
                        "stability_score": _estimate_stability_score(survivor, len(group)),
                        "confidence": max(survivor.confidence or 0.0, 0.85),
                        "last_validated_at": time.time(),
                        "updated_at": time.time(),
                    }
                ),
            )
            for entry in group:
                if entry.id == survivor.id:
                    continue
                changed = await _retire_duplicate_entry(
                    self._memory,
                    session_key,
                    entry,
                    reason=f"dream_deduplicate:{survivor.id}",
                )
                if changed:
                    pruned_entries += 1
        return (
            {
                "clustered_groups": clustered_groups,
                "pruned_entries": pruned_entries,
                **compile_result,
            },
            llm_cost,
        )

    async def _rem_sleep(self, session_key: str) -> dict[str, Any]:
        result, _llm_cost = await self._rem_sleep_impl(session_key)
        return result

    async def _rem_sleep_impl(self, session_key: str) -> tuple[dict[str, Any], int]:
        logger.info("[%s] Entering REM Sleep", session_key)
        entries = await self._memory.search(
            session_key,
            "",
            layers=["L2", "L3"],
            limit=_DREAM_MAX_SCAN,
        )
        entries = [entry for entry in entries if _is_serving_visible_memory(entry, include_canary=False)]
        rem_candidates = [entry for entry in entries if _is_rem_candidate(entry)]
        archive_inputs: list[Any] = []
        if hasattr(self._memory, "search_l4_artifacts"):
            archive_inputs = await self._memory.search_l4_artifacts(
                session_key,
                "",
                artifact_kinds=["archive"],
                limit=max(4, _REM_MAX_ITEMS // 2),
            )
        if not archive_inputs and hasattr(self._memory, "search_archives"):
            archive_inputs = await self._memory.search_archives(
                session_key,
                "",
                limit=max(4, _REM_MAX_ITEMS // 2),
            )
        archive_candidates = [
            archive for archive in archive_inputs if _is_rem_archive_candidate(archive)
        ]
        if len(rem_candidates) + len(archive_candidates) < 4:
            return {"synthesized_archives": 0, "feedback_updates": 0}, 0
        ranked = sorted(rem_candidates, key=_rem_rank_key, reverse=True)
        selected = ranked[:_REM_MAX_ITEMS]
        selected_archives = archive_candidates[: max(1, min(4, _REM_MAX_ITEMS // 2))]
        archive_summary, llm_cost = await self._synthesize_archive_summary(
            session_key,
            selected,
            archive_inputs=selected_archives,
        )
        graph_group_id = _build_graph_group_id(session_key, selected)
        artifacts = _build_rem_artifacts(
            selected,
            archive_summary=archive_summary,
            graph_group_id=graph_group_id,
        )
        if not artifacts:
            return {"synthesized_archives": 0, "feedback_updates": 0}, llm_cost
        publishable_artifacts = _build_publishable_rem_artifacts(
            session_key,
            selected,
            artifacts=artifacts,
            graph_group_id=graph_group_id,
        )
        published_artifacts: list[DreamArtifactPublishResult] = []
        for artifact in publishable_artifacts:
            published_artifacts.append(await self._publish_rem_artifact(session_key, artifact))
        feedback_updates = await self._apply_rem_feedback(
            session_key,
            selected,
            graph_group_id=graph_group_id,
        )
        return (
            {
                "synthesized_archives": len(published_artifacts),
                "synthesized_kinds": [artifact.artifact_kind for artifact in published_artifacts],
                "published_artifact_ids": [artifact.artifact_id for artifact in published_artifacts],
                "feedback_updates": feedback_updates,
            },
            llm_cost,
        )

    async def _fallback_light_sleep_candidates(
        self,
        session_key: str,
        candidates: list[MemoryEntry],
        *,
        reason: str,
    ) -> tuple[dict[str, Any], int]:
        fallback_candidates: list[DreamMemoryCandidate] = []
        now = time.time()
        recovered_candidates = 0
        for entry in candidates:
            content = str(entry.content or "").strip()
            if not content:
                continue
            candidate_id = _build_light_sleep_candidate_id(session_key, entry.id)
            if self._candidate_store is not None:
                existing_candidate = await self._candidate_store.get_candidate(candidate_id)
            else:
                existing_candidate = None
            if existing_candidate is not None and existing_candidate.status in {"published", "discarded"}:
                await self._cleanup_light_sleep_source_ids(
                    session_key,
                    existing_candidate.source_entry_ids or [entry.id],
                )
                recovered_candidates += 1
                continue
            candidate = DreamMemoryCandidate(
                candidate_id=candidate_id,
                session_key=session_key,
                session_id=entry.source_session_id or f"{session_key}:light-sleep",
                kind="turn_context",
                content=content,
                source_entry_ids=[entry.id],
                created_at=entry.created_at,
                updated_at=now,
                metadata={
                    "capture_mode": "light_sleep_fallback",
                    "fallback_reason": reason,
                    "original_layer": entry.layer,
                    "original_type": entry.type,
                },
            )
            fallback_candidates.append(candidate)
            if self._candidate_store is not None:
                await self._candidate_store.upsert(candidate)
        if not fallback_candidates:
            return {
                "promoted_to_l2": recovered_candidates,
                "deduplicated": recovered_candidates,
                "light_sleep_fallback_spill_count": 0,
                "light_sleep_fallback_compiled_candidates": 0,
                "light_sleep_fallback_recovered_candidates": recovered_candidates,
            }, 0
        compile_result, llm_cost = await self._compiler.compile_candidates(session_key, fallback_candidates)
        await self._finalize_light_sleep_fallback_candidates(session_key, fallback_candidates)
        return {
            "promoted_to_l2": int(compile_result.get("published_memory_count", 0)) + recovered_candidates,
            "deduplicated": len(fallback_candidates) + recovered_candidates,
            "light_sleep_fallback_spill_count": len(fallback_candidates),
            "light_sleep_fallback_compiled_candidates": int(
                compile_result.get("compiled_candidates", 0)
            ),
            "light_sleep_fallback_recovered_candidates": recovered_candidates,
            **compile_result,
        }, llm_cost

    async def _finalize_light_sleep_fallback_candidates(
        self,
        session_key: str,
        candidates: list[DreamMemoryCandidate],
    ) -> None:
        for candidate in candidates:
            if self._candidate_store is not None:
                stored_candidate = await self._candidate_store.get_candidate(candidate.candidate_id)
            else:
                stored_candidate = candidate
            if stored_candidate is None or stored_candidate.status not in {"published", "discarded"}:
                continue
            await self._cleanup_light_sleep_source_ids(session_key, stored_candidate.source_entry_ids)

    async def _cleanup_light_sleep_source_ids(self, session_key: str, source_ids: list[str]) -> None:
        for source_id in source_ids:
            await self._memory.archive_entry(
                session_key, source_id, reason="light_sleep_merged"
            )

    async def _synthesize_archive_summary(
        self,
        session_key: str,
        entries: list[MemoryEntry],
        *,
        archive_inputs: list[Any] | None = None,
    ) -> tuple[str, int]:
        memory_items = [
            f"- [{entry.layer}:{entry.type}] {entry.content}"
            for entry in entries
            if isinstance(entry.content, str) and entry.content.strip()
        ]
        archive_items = [
            f"- [{getattr(archive, 'session_id', 'archive')}] {getattr(archive, 'summary', '')}"
            for archive in (archive_inputs or [])
            if isinstance(getattr(archive, "summary", None), str)
            and getattr(archive, "summary").strip()
        ]
        if not memory_items and not archive_items:
            return "", 0
        prompt = (
            "你是一个跨会话记忆归并引擎。请基于以下长期记忆与既有归档摘要，输出一段可检索的归并摘要。"
            "要求：保留用户偏好、稳定规则、可复用流程和重要历史决策；删除重复表述；"
            "不要输出 JSON，不要写解释，只输出摘要正文。\n"
            f"session_key: {session_key}\n"
            f"memory_items:\n{chr(10).join(memory_items) or '- none'}\n"
            f"archive_inputs:\n{chr(10).join(archive_items) or '- none'}"
        )
        try:
            response = await self._llm.complete(
                request=PackedLLMRequest(
                    messages=[{"role": "user", "content": prompt}],
                    system="",
                    context_payload=None,
                    cache_strategy={},
                ),
                temperature=0.0,
            )
            summary = response.text.strip()
            if summary:
                llm_cost = int(getattr(getattr(response, "usage", None), "total_tokens", 0) or 0)
                return summary, llm_cost
        except Exception:
            logger.warning("[%s] REM synthesis LLM failed; falling back to heuristic summary", session_key, exc_info=True)
        return (
            "\n".join(
                [
                    *(
                        f"- [{entry.layer}:{entry.type}] {entry.content}"
                        for entry in entries[:4]
                    ),
                    *(
                        f"- [archive:{getattr(archive, 'session_id', 'archive')}] {getattr(archive, 'summary', '')}"
                        for archive in (archive_inputs or [])[:2]
                    ),
                ]
            ),
            0,
        )

    async def _apply_rem_feedback(
        self,
        session_key: str,
        entries: list[MemoryEntry],
        *,
        graph_group_id: str,
    ) -> int:
        updated = 0
        now = time.time()
        for entry in _select_rem_feedback_entries(entries):
            stability = max(
                _safe_float(entry.stability_score),
                _estimate_stability_score(entry, 2),
            )
            confidence = max(
                _safe_float(entry.confidence),
                min(0.97, stability + 0.05),
            )
            await self._memory.store(
                session_key,
                entry.model_copy(
                    update={
                        "origin_stage": "rem",
                        "confidence": confidence,
                        "stability_score": stability,
                        "graph_group_id": graph_group_id,
                        "last_validated_at": now,
                        "updated_at": now,
                    }
                ),
            )
            updated += 1
        return updated

    async def _run_phase_with_audit(
        self,
        session_key: str,
        phase: str,
        *,
        trigger: str,
        batch_id: str,
        executor: Callable[[str], Awaitable[tuple[dict[str, Any], int]]],
    ) -> dict[str, Any]:
        started_at = time.time()
        result: dict[str, Any] = {}
        llm_cost = 0
        failure_reason: str | None = None
        status = "ok"
        try:
            result, llm_cost = await executor(session_key)
            return result
        except Exception as exc:
            status = "failed"
            failure_reason = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            await self._record_audit(
                DreamAuditEntry(
                    session_key=session_key,
                    batch_id=batch_id,
                    phase=phase,
                    trigger=trigger,
                    status=status,
                    started_at=started_at,
                    ended_at=time.time(),
                    duration_s=max(0.0, time.time() - started_at),
                    promoted_count=_audit_count(result, "promoted_to_l2", "promoted_count"),
                    deduplicated_count=_audit_count(result, "deduplicated", "pruned_entries", "deduplicated_count"),
                    archived_count=_audit_count(result, "archived_count"),
                    synthesized_count=_audit_count(result, "synthesized_archives", "synthesized_count"),
                    compiled_candidates=_audit_count(result, "compiled_candidates"),
                    extracted_candidates=_audit_count(result, "extracted_candidates"),
                    published_candidates=_audit_count(result, "published_candidates"),
                    discarded_candidates=_audit_count(result, "discarded_candidates"),
                    merged_entries=_audit_count(result, "merged_entries"),
                    temporal_updates=_audit_count(result, "temporal_updates"),
                    gate_rejected_entries=_audit_count(result, "gate_rejected_entries"),
                    published_memory_count=_audit_count(result, "published_memory_count"),
                    llm_cost=llm_cost,
                    failure_reason=failure_reason,
                )
            )

    async def _record_audit(self, entry: DreamAuditEntry) -> None:
        if self._audit_log is None:
            return
        try:
            await self._audit_log.record(entry)
        except Exception:
            logger.warning("dream audit record failed for %s", entry.phase, exc_info=True)

    async def _publish_candidate_memory(
        self,
        session_key: str,
        publishable: DreamPublishableMemory,
    ) -> DreamPublishResult:
        return await self._governance_runtime.publish_memory(session_key, publishable)

    async def _publish_rem_artifact(
        self,
        session_key: str,
        artifact: DreamPublishableArtifact,
    ) -> DreamArtifactPublishResult:
        return await self._governance_runtime.publish_artifact(session_key, artifact)

    async def rollback_memory_publication(self, session_key: str, memory_id: str) -> bool:
        return await self._governance_runtime.rollback_memory_publication(session_key, memory_id)

    async def approve_governance_record(self, record_id: str) -> bool:
        return await self._governance_runtime.approve_governance_record(record_id)

    async def reject_governance_record(self, record_id: str, *, reason: str = "") -> bool:
        return await self._governance_runtime.reject_governance_record(record_id, reason=reason)

    async def rollback_artifact_publication(self, record_id: str, *, reason: str = "") -> bool:
        return await self._governance_runtime.rollback_artifact_publication(record_id, reason=reason)

    async def request_artifact_rollback(self, record_id: str, *, reason: str = "") -> bool:
        return await self._governance_runtime.request_artifact_rollback(record_id, reason=reason)

    async def list_pending_reviews(self, session_key: str | None = None) -> list[DreamGovernanceRecord]:
        return await self._governance_runtime.list_pending_reviews(session_key)

    async def list_governance_records(
        self,
        *,
        session_key: str | None = None,
        target_type: DreamTargetType | None = None,
        review_status: DreamReviewStatus | None = None,
        publish_channel: DreamPublishChannel | None = None,
        limit: int = 200,
    ) -> list[DreamGovernanceRecord]:
        if self._governance_store is None:
            return []
        return await self._governance_store.list_records(
            session_key=session_key,
            target_type=target_type,
            review_status=review_status,
            publish_channel=publish_channel,
            limit=limit,
        )


async def _retire_duplicate_entry(
    memory_store: MemoryStore,
    session_key: str,
    entry: MemoryEntry,
    *,
    reason: str,
) -> bool:
    if entry.layer == "L3":
        try:
            return bool(await memory_store.archive_entry(session_key, entry.id, reason=reason))
        except Exception:
            logger.warning("failed to archive duplicate procedure %s", entry.id, exc_info=True)
            return False
    try:
        await memory_store.delete(session_key, entry.id)
        return True
    except Exception:
        logger.warning("failed to delete duplicate fact %s", entry.id, exc_info=True)
        return False


def _normalize_memory_text(content: str) -> str:
    normalized = " ".join(content.lower().split())
    return normalized[:512]


def _build_cluster_id(session_key: str, layer: str, type_: str, normalized: str) -> str:
    digest = hashlib.sha1(f"{session_key}|{layer}|{type_}|{normalized}".encode("utf-8")).hexdigest()
    return f"cluster:{digest[:16]}"


def _build_light_sleep_candidate_id(session_key: str, entry_id: str) -> str:
    digest = hashlib.sha1(f"{session_key}|light-sleep|{entry_id}".encode("utf-8")).hexdigest()
    return f"light-sleep:{digest[:16]}"


def _is_serving_visible_memory(entry: MemoryEntry, *, include_canary: bool) -> bool:
    publish_state = str(entry.publish_state or "").strip().lower()
    if publish_state in {"draft", "rejected"}:
        return False
    if publish_state == "canary" and not include_canary:
        return False
    return True


def _normalize_publish_channel(value: str | None, *, fallback_shadow: bool) -> str:
    text = str(value or "").strip().lower()
    if text in {"direct", "shadow", "canary"}:
        return text
    return "shadow" if fallback_shadow else "direct"


def _estimate_stability_score(entry: MemoryEntry, cluster_size: int) -> float:
    use_bonus = min(0.2, float(entry.use_count or 0) * 0.02)
    cluster_bonus = min(0.2, max(0, cluster_size - 1) * 0.05)
    base = max(0.6, float(entry.confidence or 0.0))
    return min(1.0, base + use_bonus + cluster_bonus)


def _build_dream_batch_id(session_key: str, *, trigger: str) -> str:
    digest = hashlib.sha1(f"{session_key}|{trigger}|{time.time()}".encode("utf-8")).hexdigest()
    return f"{trigger}:{digest[:16]}"


def _audit_count(result: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = result.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return int(value)
    return 0


def _safe_float(value: float | None) -> float:
    if value is None:
        return 0.0
    return float(value)


def _is_rem_candidate(entry: MemoryEntry) -> bool:
    if entry.layer not in ("L2", "L3"):
        return False
    if entry.low_confidence:
        return False
    if not entry.content.strip():
        return False
    signal = max(
        _safe_float(entry.confidence),
        _safe_float(entry.stability_score),
    )
    return signal >= 0.75 or entry.use_count >= 2 or bool(entry.cluster_id)


def _rem_rank_key(entry: MemoryEntry) -> tuple[float, ...]:
    return (
        max(_safe_float(entry.stability_score), _safe_float(entry.confidence)),
        float(entry.use_count or 0),
        float(entry.last_used_at or 0.0),
        float(entry.updated_at),
        float(entry.created_at),
    )


def _select_rem_feedback_entries(entries: list[MemoryEntry]) -> list[MemoryEntry]:
    feedback_entries = [
        entry
        for entry in entries
        if max(_safe_float(entry.confidence), _safe_float(entry.stability_score)) >= 0.8
        or entry.use_count >= 3
        or bool(entry.cluster_id)
    ]
    return feedback_entries[:_REM_FEEDBACK_LIMIT]


def _build_graph_group_id(session_key: str, entries: list[MemoryEntry]) -> str:
    anchors = sorted(
        entry.cluster_id or entry.id
        for entry in entries
        if (entry.cluster_id or entry.id)
    )
    digest = hashlib.sha1(f"{session_key}|{'|'.join(anchors[:8])}".encode("utf-8")).hexdigest()
    return f"graph:{digest[:16]}"




def _is_rem_archive_candidate(archive: Any) -> bool:
    session_id = str(getattr(archive, "session_id", "") or "")
    if not session_id or ":dream:" in session_id:
        return False
    summary = str(getattr(archive, "summary", "") or "").strip()
    return bool(summary)


# Class renamed from DreamEngine to DreamEngine for consistency.
