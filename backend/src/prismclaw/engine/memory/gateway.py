"""SDD B2: memory write arbitration gateway (docs/memory-dream-sdd.md §5.2).

The single semantic write chokepoint. Six legacy write paths each invented
their own (or no) dedup/conflict handling (defect S1/S2); everything that
changes what a memory MEANS flows through here instead.

Five-state decision set:
- ADD        no near neighbor (fast path) or genuinely new information
- UPDATE     candidate supplements an existing entry → merged content
- SUPERSEDE  candidate explicitly corrects an old entry → old gets valid_to
             (shadow-gated until decision gate 2; degrades to ADD)
- CONTEST    contradiction with undecidable temporal semantics → BOTH sides
             quarantined (status='contested', confidence halved), excluded
             from default retrieval until evidence resolves them (B3 exit)
- NOOP       duplicate, nothing new

Implementation discipline (SDD §5.2, MindVault lessons):
1. Pure-evidence prompts: the arbitration LLM sees only raw data — candidate
   fulltext, neighbor originals, similarity numbers, validation-event
   counts. NEVER a pre-computed label like conflict_detected=True.
2. Fast path: no search hits → ADD with zero LLM. Fast-path share is the
   decision-gate-1 cost metric.
3. Concurrency: read-judge-write is non-atomic; the whole span runs under a
   Redis lock keyed by session_key. Lock failure raises (retryable by the
   caller) — NEVER a lockless write.
4. Shadow mode: arbitrate + audit, then execute the LEGACY store unchanged
   (production behavior identical; decision data accumulates for gate 2).

Fail-open convention: LLM/parse failure → ADD (matches the codebase's
dedup-fail-open rule: never lose a memorize the user explicitly requested).
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from prismclaw.schema.storage.storage_types import MemoryStore
from prismclaw.schema.memory.classify import classify_scope
from prismclaw.schema.memory.memory_types import MemoryEntry

logger = logging.getLogger(__name__)

_GATEWAY_LOCK_TTL_MS = 30_000
_GATEWAY_LOCK_KEY_PREFIX = "memory:arb:"

_ALLOWED_DECISIONS = {"ADD", "UPDATE", "SUPERSEDE", "CONTEST", "NOOP"}
_CONTEST_CONFIDENCE = 0.3


class GatewayLockError(RuntimeError):
    """Arbitration lock could not be acquired — caller should retry."""


class GatewayDecision(str, Enum):
    ADD = "ADD"
    UPDATE = "UPDATE"
    SUPERSEDE = "SUPERSEDE"
    CONTEST = "CONTEST"
    NOOP = "NOOP"


@dataclass
class GatewayResult:
    decision: str
    decided_by: str  # fast_path / llm / llm_error_fallback / legacy_off / shadow_stored
    executed: bool
    mode: str
    target_id: str | None = None
    reason: str | None = None
    merged_content: str | None = None
    latency_ms: int | None = None
    notes: list[str] = field(default_factory=list)


_ARBITRATION_PROMPT = """你是一个记忆仲裁器。判断新记忆（候选）与既有记忆的关系，从五个决策中选一个。

决策定义：
- ADD：候选是新信息，与既有记忆不重复、不冲突
- UPDATE：候选补充/扩展某条既有记忆（信息兼容，合起来更完整）→ 给出合并后的完整内容
- SUPERSEDE：候选明确纠正/取代某条既有记忆（用户显式说明"改用了X""现在是Y"）→ 指出被取代者
- CONTEST：候选与某条既有记忆矛盾，但无法判定谁对（可能是并存的两个语境，如不同项目）→ 指出矛盾对象
- NOOP：候选与既有记忆语义重复，无新信息

同时为候选标注 scope：
- user：用户个人画像/偏好（跨会话属于该用户）
- project：项目/环境语境（当前工作空间相关）
- agent：与具体用户/项目无关的通用经验

以 JSON 输出：
{{"decision": "ADD|UPDATE|SUPERSEDE|CONTEST|NOOP", "target_id": "决策对象的id（UPDATE/SUPERSEDE/CONTEST必填）", "merged_content": "仅UPDATE时：合并后的完整内容", "scope": "user|project|agent", "reason": "一句话理由"}}

=== 新记忆（候选）===
{candidate}

=== 既有记忆（按检索相关性排序，含原始数据）===
{neighbors}
=== 结束 ==="""


class MemoryWriteGateway:
    def __init__(
        self,
        *,
        memory_store: MemoryStore,
        llm_client: Any,
        lock_manager: Any,
        memory_settings: Any,
    ) -> None:
        self._memory = memory_store
        self._llm = llm_client
        self._lock = lock_manager
        self._settings = memory_settings

    @property
    def _mode(self) -> str:
        return str(getattr(self._settings, "gateway_mode", "off") or "off")

    async def submit(
        self,
        session_key: str,
        candidate: MemoryEntry,
        *,
        source: str = "memorize",
    ) -> GatewayResult:
        """Arbitrate one write candidate under the session-scoped lock.

        mode=off: legacy direct store, no arbitration, no audit.
        mode=shadow: arbitrate + audit, then legacy store (decision NOT applied).
        mode=active: arbitrate + audit + execute the decision.
        """
        if self._mode == "off":
            await self._memory.store(session_key, candidate)
            return GatewayResult(
                decision="ADD",
                decided_by="legacy_off",
                executed=True,
                mode="off",
            )

        lock_key = f"{_GATEWAY_LOCK_KEY_PREFIX}{session_key}"
        try:
            token = await self._lock.acquire(lock_key, ttl_ms=_GATEWAY_LOCK_TTL_MS)
        except Exception as exc:
            # SDD: no lockless fallback — surface a retryable error.
            raise GatewayLockError(
                f"memory arbitration lock busy for {session_key}: {exc}"
            ) from exc

        try:
            return await self._arbitrate_locked(session_key, candidate, source)
        finally:
            try:
                await self._lock.release(lock_key, token)
            except Exception:
                logger.warning("arbitration lock release failed for %s", session_key, exc_info=True)

    # ── arbitration core (lock held) ─────────────────────────────

    async def _arbitrate_locked(
        self, session_key: str, candidate: MemoryEntry, source: str
    ) -> GatewayResult:
        started = time.monotonic()
        mode = self._mode

        neighbors = await self._find_neighbors(session_key, candidate)

        # SDD B5: write-time scope stamping — deterministic default from the
        # entry type; the LLM path may refine it (validated against the
        # allowlist). Stamped once here so shadow legacy-store AND active
        # execution both carry scope.
        scope = classify_scope(candidate.type)
        if not neighbors:
            decision, target_id, merged, reason = (
                GatewayDecision.ADD, None, None,
                "fast path: no near neighbor",
            )
            decided_by = "fast_path"
        else:
            decision, target_id, merged, reason, decided_by, llm_scope = (
                await self._llm_arbitrate(session_key, candidate, neighbors)
            )
            if llm_scope in ("user", "project", "agent"):
                scope = llm_scope
        if candidate.scope is None:
            candidate = candidate.model_copy(update={"scope": scope})

        latency_ms = int((time.monotonic() - started) * 1000)

        result = GatewayResult(
            decision=decision.value,
            decided_by=decided_by,
            executed=False,
            mode=mode,
            target_id=target_id,
            reason=reason,
            merged_content=merged,
            latency_ms=latency_ms,
        )

        # SUPERSEDE gate: not in the initial effect set — degrade to ADD.
        executed_decision = decision
        if decision == GatewayDecision.SUPERSEDE and not bool(
            getattr(self._settings, "gateway_supersede_active", False)
        ):
            result.notes.append("supersede_downgraded_to_add")
            executed_decision = GatewayDecision.ADD

        if mode == "shadow":
            # Production behavior unchanged: legacy store regardless of decision.
            await self._memory.store(session_key, candidate)
            result.decided_by = decided_by
            result.executed = True
            result.notes.append("shadow_mode_legacy_store")
        else:
            await self._execute(
                session_key, candidate, executed_decision, target_id, neighbors, merged
            )
            result.executed = True

        await self._audit(
            session_key,
            candidate=candidate,
            neighbors=neighbors,
            decision=decision.value,
            decided_by=decided_by,
            mode=mode,
            executed=result.executed,
            target_id=target_id,
            reason=reason,
            latency_ms=latency_ms,
            source=source,
        )
        return result

    async def _find_neighbors(
        self, session_key: str, candidate: MemoryEntry
    ) -> list[MemoryEntry]:
        """Search existing entries with the candidate content as query.

        The search already excludes archived/contested/decayed (B2 status
        filter) — quarantined entries are not arbitration anchors.
        """
        limit = max(1, int(getattr(self._settings, "gateway_neighbor_limit", 3) or 3))
        try:
            results = await self._memory.search(
                session_key,
                candidate.content,
                layers=[candidate.layer],
                limit=limit,
            )
            return [e for e in results if getattr(e, "id", None) != candidate.id]
        except Exception:
            logger.warning(
                "gateway neighbor search failed for %s; treating as no-neighbor (ADD)",
                session_key,
                exc_info=True,
            )
            return []

    async def _llm_arbitrate(
        self,
        session_key: str,
        candidate: MemoryEntry,
        neighbors: list[MemoryEntry],
    ) -> tuple[GatewayDecision, str | None, str | None, str | None, str, str | None]:
        """Pure-evidence LLM arbitration. Fail-open → ADD on any error.

        Returns (decision, target_id, merged_content, reason, decided_by,
        scope) — scope is the LLM's type-independent classification
        (validated by the caller), None on every fail-open path.
        """
        try:
            neighbor_ids = [n.id for n in neighbors]
            signal_counts: dict[str, dict[str, int]] = {}
            counter = getattr(self._memory, "count_validation_signals", None)
            if counter is not None:
                signal_counts = await counter(session_key, neighbor_ids)

            lines: list[str] = []
            for i, n in enumerate(neighbors, 1):
                signals = signal_counts.get(n.id, {})
                pos = int(signals.get("positive", 0))
                neg = int(signals.get("negative", 0))
                # RAW data only — no pre-judgment labels (SDD discipline 1).
                lines.append(
                    f"[{i}] id={n.id}\n"
                    f"    检索相关性: {getattr(n, 'score', None)}\n"
                    f"    use_count: {getattr(n, 'use_count', 0)}, "
                    f"confidence: {getattr(n, 'confidence', None)}\n"
                    f"    验证事件: 正={pos}, 负={neg}\n"
                    f"    内容: {n.content}"
                )

            prompt = _ARBITRATION_PROMPT.format(
                candidate=candidate.content,
                neighbors="\n".join(lines),
            )

            from prismclaw.schema.llm.request import PackedLLMRequest

            response = await self._llm.complete(
                request=PackedLLMRequest(
                    messages=[{"role": "user", "content": prompt}],
                    system="",
                    context_payload=None,
                    cache_strategy={},
                )
            )
            parsed = _parse_arbitration_output(getattr(response, "text", "") or "")
            if parsed is None:
                logger.warning("gateway arbitration output unparseable; fail-open ADD")
                return GatewayDecision.ADD, None, None, "llm output unparseable", "llm_error_fallback", None

            decision_str = parsed.get("decision", "").upper()
            if decision_str not in _ALLOWED_DECISIONS:
                return GatewayDecision.ADD, None, None, f"invalid decision {decision_str!r}", "llm_error_fallback", None

            target_id = parsed.get("target_id") or ""
            if decision_str in ("UPDATE", "SUPERSEDE", "CONTEST") and target_id not in {
                n.id for n in neighbors
            }:
                # Hallucinated/absent target — never act on an unseen entry.
                return GatewayDecision.ADD, None, None, "target_id not in neighbor set", "llm_error_fallback", None

            merged = parsed.get("merged_content") or None
            if decision_str == "UPDATE" and not (merged or "").strip():
                return GatewayDecision.ADD, None, None, "UPDATE without merged_content", "llm_error_fallback", None

            scope_value = str(parsed.get("scope", "") or "").strip().lower()
            return (
                GatewayDecision(decision_str),
                target_id or None,
                merged,
                parsed.get("reason"),
                "llm",
                scope_value if scope_value in ("user", "project", "agent") else None,
            )
        except Exception:
            logger.warning(
                "gateway LLM arbitration failed for %s; fail-open ADD",
                session_key,
                exc_info=True,
            )
            return GatewayDecision.ADD, None, None, "llm call failed", "llm_error_fallback", None

    async def _execute(
        self,
        session_key: str,
        candidate: MemoryEntry,
        decision: GatewayDecision,
        target_id: str | None,
        neighbors: list[MemoryEntry],
        merged: str | None,
    ) -> None:
        """Execute a decision against the in-lock neighbor set.

        The target is resolved from `neighbors` (the same search results the
        LLM saw) — never re-loaded: semantic search cannot reliably resolve
        an entry by id, and the in-lock set cannot be stale.
        """
        now = time.time()
        if decision == GatewayDecision.ADD:
            await self._memory.store(session_key, candidate)
            return

        if decision == GatewayDecision.NOOP:
            return  # drop; audit carries the decision

        target = None
        for entry in neighbors:
            if getattr(entry, "id", None) == target_id:
                target = entry
                break
        if target is None:
            # Target not in the seen set (should not happen — the LLM path
            # validates membership; SUPERSEDE downgrade keeps ADD semantics).
            await self._memory.store(session_key, candidate)
            return

        if decision == GatewayDecision.UPDATE:
            chain_id = target.version_chain_id or target.id
            merged_entry = target.model_copy(
                update={
                    "content": merged or candidate.content,
                    "version_chain_id": chain_id,
                    "updated_at": now,
                }
            )
            await self._memory.store(session_key, merged_entry)
            return

        if decision == GatewayDecision.SUPERSEDE:
            chain_id = target.version_chain_id or target.id
            # Old entry: temporally invalidated (not deleted — point-in-time
            # queries stay answerable), loser of the version chain.
            old = target.model_copy(
                update={
                    "valid_to": now,
                    "winner_flag": False,
                    "updated_at": now,
                    "version_chain_id": chain_id,
                }
            )
            await self._memory.store(session_key, old)
            # New entry: the surviving successor.
            new = candidate.model_copy(
                update={
                    "version_chain_id": chain_id,
                    "supersedes": [*candidate.supersedes, target.id],
                    "updated_at": now,
                }
            )
            await self._memory.store(session_key, new)
            return

        if decision == GatewayDecision.CONTEST:
            # Quarantine BOTH sides pending future evidence (B3 lifecycle).
            new = candidate.model_copy(
                update={
                    "status": "contested",
                    "confidence": _CONTEST_CONFIDENCE,
                    "updated_at": now,
                }
            )
            await self._memory.store(session_key, new)
            old_confidence = getattr(target, "confidence", None)
            old = target.model_copy(
                update={
                    "status": "contested",
                    "confidence": (old_confidence or 1.0) * 0.5,
                    "updated_at": now,
                }
            )
            await self._memory.store(session_key, old)
            return

    async def _audit(
        self,
        session_key: str,
        *,
        candidate: MemoryEntry,
        neighbors: list[MemoryEntry],
        decision: str,
        decided_by: str,
        mode: str,
        executed: bool,
        target_id: str | None,
        reason: str | None,
        latency_ms: int,
        source: str,
    ) -> None:
        auditor = getattr(self._memory, "record_arbitration_audit", None)
        if auditor is None:
            return
        try:
            await auditor(
                session_key,
                candidate={
                    "id": candidate.id,
                    "layer": candidate.layer,
                    "type": candidate.type,
                    "content": candidate.content,  # fulltext, zero truncation
                    "origin_stage": candidate.origin_stage,
                    "source": source,
                },
                near_neighbors=[
                    {
                        "id": n.id,
                        "content": n.content,
                        "score": getattr(n, "score", None),
                        "confidence": getattr(n, "confidence", None),
                    }
                    for n in neighbors
                ],
                decision=decision,
                decided_by=decided_by,
                mode=mode,
                executed=executed,
                target_id=target_id,
                reason=reason,
                latency_ms=latency_ms,
            )
        except Exception:
            logger.warning("gateway audit write failed for %s", session_key, exc_info=True)


def _parse_arbitration_output(raw: str) -> dict[str, Any] | None:
    """Tolerant JSON extraction: fenced blocks, embedded objects."""
    if not raw or not raw.strip():
        return None
    cleaned = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if brace:
            cleaned = brace.group(0)
    try:
        parsed = json.loads(cleaned)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None
