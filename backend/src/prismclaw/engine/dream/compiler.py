from __future__ import annotations

import json
import logging
import time
from typing import Any, Awaitable, Callable

from prismclaw.schema.llm.types import LLMStreamFn
from prismclaw.schema.llm.request import PackedLLMRequest
from prismclaw.engine.dream.candidates import DreamCandidateStore, DreamMemoryCandidate
from prismclaw.engine.dream.models import DreamPublishResult, DreamPublishableMemory
from prismclaw.engine.dream.publishing import (
    append_unique_ref,
    build_publishable_memory,
    coerce_confidence,
    coerce_non_negative_int,
    infer_memory_kind,
    normalize_steps,
    preferred_str,
)
from prismclaw.schema.storage.storage_types import MemoryStore
from prismclaw.schema.memory.memory_types import MemoryEntry

logger = logging.getLogger(__name__)


class DreamCompiler:
    def __init__(
        self,
        *,
        memory_store: MemoryStore,
        llm_client: LLMStreamFn,
        candidate_store: DreamCandidateStore | None,
        publish_memory: Callable[[str, DreamPublishableMemory], Awaitable[DreamPublishResult]],
        stability_score_fn: Callable[[MemoryEntry, int], float],
    ) -> None:
        self._memory = memory_store
        self._llm = llm_client
        self._candidate_store = candidate_store
        self._publish_memory = publish_memory
        self._stability_score_fn = stability_score_fn

    async def compile_candidate_lane(self, session_key: str) -> tuple[dict[str, Any], int]:
        if self._candidate_store is None:
            return _empty_compile_result(), 0
        draft_candidates = await self._candidate_store.list_candidates(
            session_key,
            status="draft",
            limit=24,
        )
        if not draft_candidates:
            return _empty_compile_result(), 0
        return await self.compile_candidates(session_key, draft_candidates)

    async def compile_candidates(
        self,
        session_key: str,
        candidates: list[DreamMemoryCandidate],
    ) -> tuple[dict[str, Any], int]:
        llm_cost = 0
        extracted_candidates = 0
        published_candidates = 0
        discarded_candidates = 0
        merged_entries = 0
        temporal_updates = 0
        gate_rejected_entries = 0
        published_memory_ids: list[str] = []

        for candidate in candidates:
            extracted, extract_cost = await self._extract_candidate_memories(session_key, candidate)
            llm_cost += extract_cost
            if not extracted:
                await self._update_candidate_status(
                    _replace_candidate_status(candidate, "discarded", reason="no_structured_memories")
                )
                discarded_candidates += 1
                continue

            extracted_candidates += len(extracted)
            candidate_merges = 0
            candidate_temporal_updates = 0
            candidate_gate_rejections = 0
            candidate_published_ids: list[str] = []
            candidate_published_kinds: list[str] = []
            for extracted_item in extracted:
                gate = _evaluate_publish_gate(candidate, extracted_item)
                if not gate["allow"]:
                    candidate_gate_rejections += 1
                    continue
                merge_result, merge_cost = await self._merge_candidate_memory(
                    session_key,
                    candidate,
                    extracted_item,
                )
                llm_cost += merge_cost
                candidate_merges += int(merge_result.get("merged_entries", 0))
                candidate_temporal_updates += int(merge_result.get("temporal_updates", 0))
                candidate_published_ids.extend(
                    [str(item) for item in merge_result.get("published_memory_ids", [])]
                )
                candidate_published_kinds.extend(
                    [str(item) for item in merge_result.get("published_memory_kinds", [])]
                )

            merged_entries += candidate_merges
            temporal_updates += candidate_temporal_updates
            gate_rejected_entries += candidate_gate_rejections
            published_memory_ids.extend(candidate_published_ids)
            if candidate_merges > 0 or candidate_temporal_updates > 0:
                await self._update_candidate_status(
                    _replace_candidate_status(
                        candidate,
                        "published",
                        reason="compiled_to_memory",
                        extra_metadata={
                            "gate_rejected_entries": candidate_gate_rejections,
                            "published_memory_ids": candidate_published_ids,
                            "published_memory_kinds": sorted(set(candidate_published_kinds)),
                            "review_status": "published",
                        },
                    )
                )
                published_candidates += 1
            else:
                await self._update_candidate_status(
                    _replace_candidate_status(
                        candidate,
                        "discarded",
                        reason=(
                            "publish_gate_rejected"
                            if candidate_gate_rejections >= len(extracted)
                            else "merge_no_effect"
                        ),
                        extra_metadata={
                            "gate_rejected_entries": candidate_gate_rejections,
                            "review_status": "rejected",
                        },
                    )
                )
                discarded_candidates += 1

        return (
            {
                "compiled_candidates": len(candidates),
                "extracted_candidates": extracted_candidates,
                "published_candidates": published_candidates,
                "discarded_candidates": discarded_candidates,
                "merged_entries": merged_entries,
                "temporal_updates": temporal_updates,
                "gate_rejected_entries": gate_rejected_entries,
                "published_memory_count": len(published_memory_ids),
            },
            llm_cost,
        )

    async def _update_candidate_status(self, candidate: DreamMemoryCandidate) -> None:
        if self._candidate_store is None:
            return
        await self._candidate_store.upsert(candidate)

    async def _extract_candidate_memories(
        self,
        session_key: str,
        candidate: DreamMemoryCandidate,
    ) -> tuple[list[dict[str, Any]], int]:
        prompt = (
            "你是一个长期记忆编译器。请从给定会话片段中提取值得进入长期记忆的结构化候选。"
            "只保留稳定偏好、约束、事实、可复用流程。"
            "输出 JSON 数组，每项包含 layer(L2 或 L3)、type、memory_kind、content、entity_key、confidence、"
            "scope、evidence_count、prerequisites、steps。"
            "如果没有合适候选，输出 []。\n"
            f"session_key: {session_key}\n"
            f"candidate_kind: {candidate.kind}\n"
            f"candidate_content:\n{candidate.content}"
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
            llm_cost = int(getattr(getattr(response, "usage", None), "total_tokens", 0) or 0)
            parsed = _parse_json_array(getattr(response, "text", ""))
            return _normalize_candidate_memories(parsed), llm_cost
        except Exception:
            logger.warning(
                "[%s] candidate extraction LLM failed for %s",
                session_key,
                candidate.candidate_id,
                exc_info=True,
            )
            return [], 0

    async def _merge_candidate_memory(
        self,
        session_key: str,
        candidate: DreamMemoryCandidate,
        extracted: dict[str, Any],
    ) -> tuple[dict[str, Any], int]:
        layer = str(extracted.get("layer", "L2"))
        content = str(extracted.get("content", "")).strip()
        if not content:
            return _empty_merge_result(), 0

        existing = await self._memory.search(
            session_key,
            content,
            layers=[layer],
            limit=5,
        )
        existing = [entry for entry in existing if _is_visible_memory_entry(entry, include_canary=False)]
        if not existing:
            now = time.time()
            published = await self._publish_memory(
                session_key,
                build_publishable_memory(
                    candidate,
                    extracted,
                    overrides={
                        "version_chain_id": _resolve_version_chain_id(session_key, extracted),
                        "valid_from": now,
                        "valid_to": None,
                        "winner_flag": True,
                    },
                ),
            )
            return {
                "merged_entries": 1,
                "temporal_updates": 0,
                "published_memory_ids": [published.memory_id],
                "published_memory_kinds": [published.memory_kind],
            }, 0

        decision, llm_cost = await self._judge_candidate_merge(
            session_key,
            candidate,
            extracted,
            existing,
        )
        action = str(decision.get("action", "new"))
        target_id = str(decision.get("target_id", "") or "")
        merged_content = str(decision.get("merged_content", "") or "").strip()
        confidence = coerce_confidence(decision.get("confidence"), extracted.get("confidence"))
        target = next((entry for entry in existing if entry.id == target_id), existing[0])
        now = time.time()
        memory_type = str(extracted.get("type", "fact"))
        version_chain_id = _resolve_version_chain_id(session_key, extracted, target)

        if action == "duplicate":
            await self._memory.store(
                session_key,
                target.model_copy(
                    update={
                        "origin_stage": "candidate_merge",
                        "confidence": max(_safe_float(target.confidence), confidence),
                        "stability_score": max(
                            _safe_float(target.stability_score),
                            self._stability_score_fn(target, 2),
                        ),
                        "source_refs": append_unique_ref(target.source_refs, candidate.candidate_id),
                        "last_validated_at": now,
                        "valid_from": target.valid_from or target.created_at,
                        "valid_to": None,
                        "winner_flag": True,
                        "version_chain_id": target.version_chain_id or version_chain_id,
                        "updated_at": now,
                    }
                ),
            )
            return {
                "merged_entries": 1,
                "temporal_updates": 0,
                "published_memory_ids": [target.id],
                "published_memory_kinds": [infer_memory_kind(None, layer=target.layer, type_=target.type)],
            }, llm_cost

        if action == "refine":
            await self._memory.store(
                session_key,
                target.model_copy(
                    update={
                        "type": memory_type,
                        "content": merged_content or content,
                        "entity_key": preferred_str(decision.get("entity_key"), extracted.get("entity_key")),
                        "origin_stage": "candidate_merge",
                        "confidence": max(_safe_float(target.confidence), confidence),
                        "stability_score": max(
                            _safe_float(target.stability_score),
                            confidence,
                        ),
                        "source_refs": append_unique_ref(target.source_refs, candidate.candidate_id),
                        "last_validated_at": now,
                        "valid_from": target.valid_from or target.created_at,
                        "valid_to": None,
                        "winner_flag": True,
                        "version_chain_id": target.version_chain_id or version_chain_id,
                        "updated_at": now,
                    }
                ),
            )
            return {
                "merged_entries": 1,
                "temporal_updates": 0,
                "published_memory_ids": [target.id],
                "published_memory_kinds": [infer_memory_kind(None, layer=target.layer, type_=target.type)],
            }, llm_cost

        if action == "contradict":
            await self._memory.store(
                session_key,
                target.model_copy(
                    update={
                        "status": "superseded",
                        "expires_at": now,
                        "origin_stage": "temporal_resolution",
                        "last_validated_at": now,
                        "valid_from": target.valid_from or target.created_at,
                        "valid_to": now,
                        "winner_flag": False,
                        "version_chain_id": target.version_chain_id or version_chain_id,
                        "updated_at": now,
                    }
                ),
            )
            published = await self._publish_memory(
                session_key,
                build_publishable_memory(
                    candidate,
                    extracted,
                    overrides={
                        "content": merged_content or content,
                        "entity_key": preferred_str(
                            decision.get("entity_key"),
                            extracted.get("entity_key"),
                            target.entity_key,
                        ),
                        "supersedes": [target.id],
                        "version_chain_id": target.version_chain_id or version_chain_id,
                        "valid_from": now,
                        "valid_to": None,
                        "winner_flag": True,
                        "origin_stage": "temporal_resolution",
                        "confidence": max(confidence, 0.8),
                    },
                ),
            )
            return {
                "merged_entries": 1,
                "temporal_updates": 1,
                "published_memory_ids": [published.memory_id],
                "published_memory_kinds": [published.memory_kind],
            }, llm_cost

        published = await self._publish_memory(
            session_key,
            build_publishable_memory(
                candidate,
                extracted,
                overrides={
                    "content": merged_content or content,
                    "confidence": confidence,
                    "version_chain_id": version_chain_id,
                    "valid_from": now,
                    "valid_to": None,
                    "winner_flag": True,
                    "origin_stage": "candidate_merge",
                },
            ),
        )
        return {
            "merged_entries": 1,
            "temporal_updates": 0,
            "published_memory_ids": [published.memory_id],
            "published_memory_kinds": [published.memory_kind],
        }, llm_cost

    async def _judge_candidate_merge(
        self,
        session_key: str,
        candidate: DreamMemoryCandidate,
        extracted: dict[str, Any],
        existing: list[MemoryEntry],
    ) -> tuple[dict[str, Any], int]:
        existing_lines = [
            f"- id={entry.id} type={entry.type} entity={entry.entity_key or '-'} "
            f"status={entry.status} content={entry.content}"
            for entry in existing
        ]
        prompt = (
            "你是一个长期记忆 merge judge。请判断新的记忆候选与现有记忆的关系。"
            "只输出 JSON，对象字段包含 action(target_id 可选，值为 duplicate/refine/contradict/new)、"
            "merged_content、entity_key、confidence。\n"
            f"session_key: {session_key}\n"
            f"candidate_id: {candidate.candidate_id}\n"
            f"candidate:\n{json.dumps(extracted, ensure_ascii=True, sort_keys=True)}\n"
            f"existing_memories:\n{chr(10).join(existing_lines)}"
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
            llm_cost = int(getattr(getattr(response, "usage", None), "total_tokens", 0) or 0)
            parsed = _parse_json_object(getattr(response, "text", ""))
            normalized = _normalize_merge_decision(parsed)
            if normalized:
                return normalized, llm_cost
        except Exception:
            logger.warning(
                "[%s] candidate merge judge failed for %s",
                session_key,
                candidate.candidate_id,
                exc_info=True,
            )
        fallback = _fallback_merge_decision(extracted, existing)
        return fallback, 0


def _empty_compile_result() -> dict[str, int]:
    return {
        "compiled_candidates": 0,
        "extracted_candidates": 0,
        "published_candidates": 0,
        "discarded_candidates": 0,
        "merged_entries": 0,
        "temporal_updates": 0,
        "gate_rejected_entries": 0,
    }


def _empty_merge_result() -> dict[str, Any]:
    return {
        "merged_entries": 0,
        "temporal_updates": 0,
        "published_memory_ids": [],
        "published_memory_kinds": [],
    }


def _replace_candidate_status(
    candidate: DreamMemoryCandidate,
    status: str,
    *,
    reason: str,
    extra_metadata: dict[str, Any] | None = None,
) -> DreamMemoryCandidate:
    metadata = dict(candidate.metadata)
    metadata["compile_status_reason"] = reason
    if extra_metadata:
        metadata.update(extra_metadata)
    return DreamMemoryCandidate(
        candidate_id=candidate.candidate_id,
        session_key=candidate.session_key,
        session_id=candidate.session_id,
        kind=candidate.kind,
        content=candidate.content,
        source_entry_ids=list(candidate.source_entry_ids),
        created_at=candidate.created_at,
        updated_at=time.time(),
        status=status,  # type: ignore[arg-type]
        metadata=metadata,
    )


def _parse_json_array(raw: str) -> list[Any]:
    parsed = _parse_json_fragment(raw)
    return parsed if isinstance(parsed, list) else []


def _parse_json_object(raw: str) -> dict[str, Any]:
    parsed = _parse_json_fragment(raw)
    return parsed if isinstance(parsed, dict) else {}


def _parse_json_fragment(raw: str) -> Any:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    start_candidates = [idx for idx in (text.find("["), text.find("{")) if idx != -1]
    if not start_candidates:
        return None
    start = min(start_candidates)
    for end in range(len(text), start, -1):
        snippet = text[start:end].strip()
        if not snippet:
            continue
        try:
            return json.loads(snippet)
        except Exception:
            continue
    return None


def _normalize_candidate_memories(raw: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        layer = str(item.get("layer", "L2")).upper()
        if layer not in {"L2", "L3"}:
            continue
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        memory_kind = infer_memory_kind(
            item.get("memory_kind"),
            layer=layer,
            type_=item.get("type"),
        )
        steps = normalize_steps(item.get("steps"))
        prerequisites = normalize_steps(item.get("prerequisites"))
        normalized.append(
            {
                "layer": layer,
                "type": str(item.get("type", "fact") or "fact"),
                "content": content,
                "memory_kind": memory_kind,
                "entity_key": preferred_str(item.get("entity_key")),
                "confidence": coerce_confidence(item.get("confidence"), 0.7),
                "scope": preferred_str(item.get("scope")),
                "evidence_count": coerce_non_negative_int(item.get("evidence_count"), 0),
                "valid_from": _parse_optional_float(item.get("valid_from")),
                "valid_to": _parse_optional_float(item.get("valid_to")),
                "winner_flag": bool(item.get("winner_flag", True)),
                "steps": steps,
                "prerequisites": prerequisites,
            }
        )
    return normalized


def _normalize_merge_decision(raw: dict[str, Any]) -> dict[str, Any]:
    action = str(raw.get("action", "new")).strip().lower()
    if action not in {"duplicate", "refine", "contradict", "new"}:
        action = "new"
    return {
        "action": action,
        "target_id": str(raw.get("target_id", "") or ""),
        "merged_content": str(raw.get("merged_content", "") or "").strip(),
        "entity_key": preferred_str(raw.get("entity_key")),
        "confidence": coerce_confidence(raw.get("confidence"), 0.75),
    }


def _fallback_merge_decision(
    extracted: dict[str, Any],
    existing: list[MemoryEntry],
) -> dict[str, Any]:
    content = str(extracted.get("content", "")).strip()
    target = existing[0]
    for entry in existing:
        if _normalize_memory_text(entry.content) == _normalize_memory_text(content):
            return {
                "action": "duplicate",
                "target_id": entry.id,
                "merged_content": entry.content,
                "entity_key": preferred_str(entry.entity_key, extracted.get("entity_key")),
                "confidence": max(
                    _safe_float(entry.confidence),
                    coerce_confidence(extracted.get("confidence"), 0.75),
                ),
            }
    return {
        "action": "new",
        "target_id": target.id,
        "merged_content": content,
        "entity_key": preferred_str(extracted.get("entity_key"), target.entity_key),
        "confidence": coerce_confidence(extracted.get("confidence"), 0.75),
    }


def _evaluate_publish_gate(
    candidate: DreamMemoryCandidate,
    extracted: dict[str, Any],
) -> dict[str, Any]:
    confidence = coerce_confidence(extracted.get("confidence"), 0.0)
    evidence_count = max(
        1,
        len(candidate.source_entry_ids),
        coerce_non_negative_int(extracted.get("evidence_count"), 0),
        coerce_non_negative_int(candidate.metadata.get("message_count"), 0),
    )
    memory_kind = infer_memory_kind(
        extracted.get("memory_kind"),
        layer=str(extracted.get("layer", "L2")),
        type_=extracted.get("type"),
    )
    content = str(extracted.get("content", "")).strip()
    steps = normalize_steps(extracted.get("steps"))
    prerequisites = normalize_steps(extracted.get("prerequisites"))

    if len(content) < 8:
        return {"allow": False, "reason": "content_too_short"}
    if confidence < 0.72:
        return {"allow": False, "reason": "confidence_too_low"}

    if memory_kind == "procedure":
        if len(steps) < 2:
            return {"allow": False, "reason": "procedure_steps_insufficient"}
        if evidence_count < 3 and candidate.kind != "session_episode":
            return {"allow": False, "reason": "procedure_evidence_insufficient"}
        if confidence < 0.8:
            return {"allow": False, "reason": "procedure_confidence_too_low"}

    if memory_kind in {"fact", "preference", "constraint"} and evidence_count < 1:
        return {"allow": False, "reason": "evidence_insufficient"}

    return {
        "allow": True,
        "reason": "ok",
        "memory_kind": memory_kind,
        "evidence_count": evidence_count,
        "prerequisite_count": len(prerequisites),
        "step_count": len(steps),
    }


def _is_visible_memory_entry(entry: MemoryEntry, *, include_canary: bool) -> bool:
    publish_state = str(entry.publish_state or "").strip().lower()
    if publish_state in {"draft", "rejected"}:
        return False
    if publish_state == "canary" and not include_canary:
        return False
    return True


def _normalize_memory_text(content: str) -> str:
    normalized = " ".join(content.lower().split())
    return normalized[:512]


def _safe_float(value: float | None) -> float:
    if value is None:
        return 0.0
    return float(value)


def _parse_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _resolve_version_chain_id(
    session_key: str,
    extracted: dict[str, Any],
    target: MemoryEntry | None = None,
) -> str:
    if target is not None and target.version_chain_id:
        return str(target.version_chain_id)
    entity_key = preferred_str(extracted.get("entity_key"), getattr(target, "entity_key", None))
    memory_type = str(extracted.get("type", getattr(target, "type", "fact")) or "fact")
    chain_seed = f"{session_key}|{memory_type}|{entity_key or 'global'}"
    return f"ver:{hash(chain_seed) & 0xFFFFFFFF:08x}"
