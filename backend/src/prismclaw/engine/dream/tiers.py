"""SDD B3 tier modules (docs/memory-dream-sdd.md §5.3).

Three-tier dream consolidation, replacing the legacy "run by clock, judge
by nothing" phases:

- light (session end / idle): deterministic dedup — EXACT normalized-text
  duplicates only, zero LLM. ≥3-member groups additionally file a draft
  procedural suggestion (invisible until governance publishes it).
- mid (daily slot): pure bookkeeping — validation-event writeback,
  expires_at/valid_to decay, CONTEST quarantine exits. Zero LLM.
- deep (evidence-gated): the ONLY semantic merge. A pair merges only when
  BOTH sides carry same-direction positive validation history; the loser is
  temporally invalidated (valid_to) — reversible, never deleted.

Design notes:
- Deviation from SDD (wiring constraint): tier products do not route through
  the B2 gateway — the dream engine is constructed before the gateway in the
  assembly chain. Light-tier suggestions land as publish_state='draft'
  (filtered from retrieval by default), deep merges execute under this flag.
  Both are flag-gated default-off, preserving rollout safety.
- Retrieval already excludes status IN (archived, contested, decayed) at SQL
  level (B2), so mid-tier lifecycle changes take effect on the read path
  immediately.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from prismclaw.schema.storage.storage_types import MemoryStore
from prismclaw.schema.memory.memory_types import MemoryEntry

logger = logging.getLogger(__name__)

_MERGE_PAIR_SCAN_LIMIT = 200


def _normalize(content: str) -> str:
    return " ".join(content.lower().split())[:512]


async def _scan_active(memory: MemoryStore, session_key: str, limit: int) -> list[MemoryEntry]:
    try:
        entries = await memory.search(session_key, "", layers=["L2", "L3"], limit=limit)
    except Exception:
        logger.warning("tier scan failed for %s", session_key, exc_info=True)
        return []
    return [
        e for e in entries
        if str(getattr(e, "publish_state", "") or "").strip().lower() not in {"draft", "rejected"}
    ]


async def _retire(memory: MemoryStore, session_key: str, entry: MemoryEntry, reason: str) -> bool:
    """L3 → soft archive (history kept); L2 exact dup → delete (zero info loss)."""
    if entry.layer == "L3":
        try:
            return bool(await memory.archive_entry(session_key, entry.id, reason=reason))
        except Exception:
            logger.warning("tier retire failed for %s", entry.id, exc_info=True)
            return False
    try:
        await memory.delete(session_key, entry.id)
        return True
    except Exception:
        logger.warning("tier retire failed for %s", entry.id, exc_info=True)
        return False


# ════════════════════════════════════════════════════════════════
# Light tier — deterministic dedup, zero LLM
# ════════════════════════════════════════════════════════════════


async def run_light_tier(
    memory: MemoryStore,
    session_key: str,
    *,
    scan_limit: int = _MERGE_PAIR_SCAN_LIMIT,
) -> dict[str, int]:
    """Exact-duplicate dedup + ≥3-repeat procedural suggestion (draft).

    Returns {"deduped": n, "groups": g, "suggestions": s}.
    """
    entries = await _scan_active(memory, session_key, scan_limit)
    grouped: dict[tuple[str, str, str], list[MemoryEntry]] = {}
    for entry in entries:
        normalized = _normalize(entry.content)
        if not normalized:
            continue
        grouped.setdefault((entry.layer, entry.type, normalized), []).append(entry)

    deduped = 0
    groups = 0
    suggestions = 0
    for (_layer, _type, _normalized), group in grouped.items():
        if len(group) < 2:
            continue
        groups += 1
        survivor = max(group, key=lambda item: (item.updated_at, item.created_at))
        for entry in group:
            if entry.id == survivor.id:
                continue
            if await _retire(memory, session_key, entry, reason=f"light_tier:{survivor.id}"):
                deduped += 1

        # ≥3 exact repeats = repeated behavior worth a procedural pattern.
        # Filed as DRAFT: invisible to retrieval until governance publishes.
        if len(group) >= 3 and survivor.layer == "L2":
            suggestion = MemoryEntry(
                id=f"sugg_{time.time_ns():x}",
                layer="L3",
                type="procedural",
                content=survivor.content,
                source_session_id=survivor.source_session_id,
                created_at=time.time(),
                updated_at=time.time(),
                confidence=0.4,
                origin_stage="light_tier_suggestion",
                publish_state="draft",
            )
            try:
                await memory.store(session_key, suggestion)
                suggestions += 1
            except Exception:
                logger.warning(
                    "light tier suggestion store failed for %s", session_key, exc_info=True
                )

    if groups:
        logger.info(
            "[SDD-B3/light] %s: groups=%d deduped=%d suggestions=%d",
            session_key, groups, deduped, suggestions,
        )
    return {"deduped": deduped, "groups": groups, "suggestions": suggestions}


# ════════════════════════════════════════════════════════════════
# Mid tier — evidence bookkeeping, zero LLM
# ════════════════════════════════════════════════════════════════

_CONFIDENCE_FLOOR = 0.05
_CONFIDENCE_CEIL = 0.95
_STRONG_NEG_PENALTY = 0.8       # one strong negative × current confidence
_WEAK_POS_BONUS = 0.02          # per weak positive
_STABILITY_POS_BONUS = 0.05
_STABILITY_NEG_PENALTY = 0.1


_DEMOTE_FRACTION = 0.2


async def run_mid_tier(
    memory: MemoryStore,
    session_key: str,
    *,
    contest_exit_days: int = 30,
    scan_limit: int = _MERGE_PAIR_SCAN_LIMIT,
    budget_entries: int = 0,
    edge_store: Any = None,
) -> dict[str, int]:
    """Daily bookkeeping: validation writeback + decay + CONTEST exits.

    Deterministic only — every adjustment is a fixed function of raw
    validation-event counts. Returns per-action counters.

    Idempotency (consume-once): the writeback counts only events NEWER than
    the entry's last_validated_at watermark and stamps that watermark on
    application — re-running with an unchanged event set is a no-op.
    Without this, the daily slot would re-apply the full history every day
    (×0.8^n compounding to the floor, +0.02/day drift to the ceiling) and
    destroy the confidence signal B4 ranks on. CONTEST exits below use the
    full-history view on purpose: they are idempotent state transitions
    (any disproving evidence ever → decayed), not accumulations.
    """
    now = time.time()
    updates: list[tuple[str, dict[str, Any]]] = []
    writebacks = decayed = contest_exited = 0

    # 1. Validation writeback on active entries — PENDING events only.
    active = await _scan_active(memory, session_key, scan_limit)
    try:
        signals = await memory.aggregate_validation_signals(session_key)
    except Exception:
        signals = {}
    try:
        pending = await memory.aggregate_pending_validation_signals(session_key)
    except Exception:
        pending = {}
    for entry in active:
        counts = pending.get(entry.id)
        if not counts:
            continue
        strong_neg = counts.get("strong_negative", 0)
        weak_pos = counts.get("weak_positive", 0)
        weak_neg = counts.get("weak_negative", 0)
        if strong_neg == 0 and weak_pos == 0 and weak_neg == 0:
            continue
        confidence = float(entry.confidence if entry.confidence is not None else 0.5)
        if strong_neg:
            confidence *= _STRONG_NEG_PENALTY ** strong_neg
        confidence += _WEAK_POS_BONUS * weak_pos
        confidence -= 0.01 * weak_neg
        confidence = max(_CONFIDENCE_FLOOR, min(_CONFIDENCE_CEIL, confidence))

        stability = float(entry.stability_score if entry.stability_score is not None else 0.5)
        if weak_pos:
            stability = min(1.0, stability + _STABILITY_POS_BONUS * weak_pos)
        if strong_neg or weak_neg:
            stability = max(0.0, stability - _STABILITY_NEG_PENALTY * (strong_neg + weak_neg))

        updates.append((entry.id, {
            "confidence": confidence,
            "stability_score": stability,
            # Consume the events: the watermark advances past every event
            # counted above, so the next run sees only what arrived since.
            "last_validated_at": now,
        }))
        writebacks += 1

    # 2. Expired entries (expires_at / valid_to in the past) → decayed.
    for entry in active:
        expired = (
            (entry.expires_at is not None and float(entry.expires_at) < now)
            or (entry.valid_to is not None and float(entry.valid_to) < now)
        )
        if expired:
            updates.append((entry.id, {"status": "decayed"}))
            decayed += 1

    # 3. CONTEST quarantine exits.
    lister = getattr(memory, "list_entries_by_status", None)
    contested: list[MemoryEntry] = []
    if lister is not None:
        try:
            contested = await lister(session_key, ["contested"], limit=scan_limit)
        except Exception:
            logger.warning("mid tier contested scan failed for %s", session_key, exc_info=True)
    for entry in contested:
        counts = signals.get(entry.id) or {}
        strong_neg = counts.get("strong_negative", 0)
        pos = counts.get("weak_positive", 0) + counts.get("strong_positive", 0)
        # updated_at is the contested-at proxy (gateway bumps it on quarantine).
        age_days = (now - float(entry.updated_at)) / 86400.0
        if strong_neg >= 1:
            updates.append((entry.id, {"status": "decayed"}))  # disproven
            contest_exited += 1
        elif pos >= 2:
            updates.append((entry.id, {"status": "active", "confidence": 0.6}))  # vindicated
            contest_exited += 1
        elif age_days > contest_exit_days:
            updates.append((entry.id, {"status": "decayed"}))  # evidence never came
            contest_exited += 1

    if writebacks or decayed or contest_exited:
        logger.info(
            "[SDD-B3/mid] %s: writebacks=%d decayed=%d contest_exits=%d",
            session_key, writebacks, decayed, contest_exited,
        )

    # E3: 价值驱动的降级迁移。
    # budget_entries > 0 时启用：当 active 条目数超过预算，
    # 按价值评分降级最低价值的条目。渐进式降级（demote_fraction），
    # 避免单次冲击过大。
    demoted = 0
    if budget_entries > 0:
        from prismclaw.engine.dream.scoring import memory_value

        # 收集本次运行已标记为 decayed 的条目 ID，排除重复降级
        decayed_ids = {eid for eid, fields in updates if fields.get("status") == "decayed"}

        _protected_publish = {"published", "canary", "reviewed"}
        active_for_demote = [
            e for e in active if e.status == "active"
            and e.id not in decayed_ids
            # 不降级刚写入的条目（<1天）和发布态条目
            and (now - float(e.updated_at or e.created_at)) > 86400.0
            and str(getattr(e, "publish_state", "") or "").strip().lower() not in _protected_publish
        ]
        if len(active_for_demote) > budget_entries:
            # E5 联动：批量查询 edge_degree，供 memory_value 评估连接度
            edge_degrees: dict[str, int] = {}
            if edge_store is not None:
                try:
                    for e in active_for_demote:
                        deg = await edge_store.edge_degree(session_key, e.id)
                        if deg > 0:
                            edge_degrees[e.id] = deg
                except Exception:
                    logger.warning("edge degree query failed for %s", session_key, exc_info=True)

            scored = [(e, memory_value(e, now=now, edge_degree=edge_degrees.get(e.id, 0))) for e in active_for_demote]
            scored.sort(key=lambda x: x[1])  # 升序，最低价值在前
            # 渐进式降级：每次最多降级超出部分的 demote_fraction，最少 1 条
            excess = len(active_for_demote) - budget_entries
            demote_count = max(1, int(excess * _DEMOTE_FRACTION))
            demote_count = min(demote_count, excess)

            # L3 和 L2 分别收集，L3 走 archive_entry（保持语义正确）
            # 但延迟到 updates 刷盘之后执行，避免部分降级不可回滚
            l3_demote_ids: list[str] = []
            for entry, _value in scored[:demote_count]:
                if entry.layer == "L3":
                    l3_demote_ids.append(entry.id)
                else:
                    updates.append((entry.id, {"status": "decayed"}))
                demoted += 1

            # 先刷盘 L2 降级 + 其他 updates
            if updates:
                try:
                    await memory.apply_entry_updates(session_key, updates)
                except Exception:
                    logger.warning("mid tier updates failed for %s", session_key, exc_info=True)
                    # L2 刷盘失败则不执行 L3 archive，保持一致性
                    l3_demote_ids = []
                else:
                    # E5 联动：对 L2 decayed 条目同步标记关联边为 inactive
                    if edge_store is not None:
                        for eid, fields in updates:
                            if fields.get("status") == "decayed":
                                try:
                                    await edge_store.deactivate_edges_for_entry(session_key, eid)
                                except Exception:
                                    logger.warning("edge deactivate failed for %s", eid, exc_info=True)

            # L2 刷盘成功后执行 L3 归档
            l3_demoted = 0
            for entry_id in l3_demote_ids:
                try:
                    ok = await memory.archive_entry(session_key, entry_id, reason="mid_tier_demote")
                    if ok:
                        l3_demoted += 1
                        # E5 联动：降级条目时同步标记关联边为 inactive
                        if edge_store is not None:
                            try:
                                await edge_store.deactivate_edges_for_entry(session_key, entry_id)
                            except Exception:
                                logger.warning("edge deactivate failed for %s", entry_id, exc_info=True)
                except Exception:
                    logger.warning("mid tier demote archive failed for %s", entry_id, exc_info=True)
            # 修正 demoted 计数：仅计入 L3 实际成功归档数
            # 之前 demoted 已包含全部 demote_count 条，需减去 L3 失败数
            l3_failed = len(l3_demote_ids) - l3_demoted
            demoted -= l3_failed

            if demoted:
                logger.info(
                    "[SDD-B3/mid] %s: demoted=%d (budget=%d, active=%d, excess=%d)",
                    session_key, demoted, budget_entries, len(active_for_demote), excess,
                )

            # updates 已提前刷盘，清空避免重复刷盘
            updates = []
        else:
            # 未超预算，正常刷盘
            pass

    if updates:
        try:
            await memory.apply_entry_updates(session_key, updates)
        except Exception:
            logger.warning("mid tier updates failed for %s", session_key, exc_info=True)

    return {
        "writebacks": writebacks,
        "decayed": decayed,
        "contest_exited": contest_exited,
        "demoted": demoted,
    }


# ════════════════════════════════════════════════════════════════
# Deep tier — evidence-gated semantic merge (the ONLY LLM merge)
# ════════════════════════════════════════════════════════════════


async def run_deep_tier(
    memory: MemoryStore,
    llm_client: Any,
    session_key: str,
    *,
    min_positive: int = 1,
    scan_limit: int = _MERGE_PAIR_SCAN_LIMIT,
    correction_learning_enabled: bool = False,
    edge_store: Any = None,
) -> dict[str, int]:
    """Weekly semantic merge. Admission: BOTH sides carry positive validation
    history (≥ min_positive each). Loser gets valid_to (reversible), survivor
    carries the merged content on the version chain.

    NOTE on the SDD gate: the doc requires "at least one STRONG positive",
    but B1's signal set has no strong positive (only strong negative — user
    corrections). Implemented as net positive history (any attribution);
    documented deviation.
    """
    entries = await _scan_active(memory, session_key, scan_limit)
    try:
        signals = await memory.aggregate_validation_signals(session_key)
    except Exception:
        signals = {}

    def _positives(entry_id: str) -> int:
        counts = signals.get(entry_id) or {}
        return counts.get("weak_positive", 0) + counts.get("strong_positive", 0)

    # Evidence-gated candidate pool.
    pool = [e for e in entries if _positives(e.id) >= min_positive]
    if len(pool) < 2:
        return {"pairs_examined": 0, "merged": 0}

    # Pair via mutual neighbor search (cheap approximation of full pairwise).
    examined = 0
    merged = 0
    used_ids: set[str] = set()
    for entry in pool:
        if entry.id in used_ids:
            continue
        try:
            neighbors = await memory.search(
                session_key, entry.content, layers=[entry.layer], limit=3
            )
        except Exception:
            continue
        for neighbor in neighbors:
            if neighbor.id == entry.id or neighbor.id in used_ids:
                continue
            if neighbor.layer != entry.layer:
                continue
            if _positives(neighbor.id) < min_positive:
                continue  # evidence gate: BOTH sides
            examined += 1
            survivor, loser = (
                (entry, neighbor)
                if (entry.updated_at, entry.created_at) >= (neighbor.updated_at, neighbor.created_at)
                else (neighbor, entry)
            )
            merged_content = await _llm_merge(llm_client, survivor.content, loser.content)
            if not merged_content:
                continue
            now = time.time()
            chain_id = survivor.version_chain_id or survivor.id
            try:
                await memory.store(session_key, survivor.model_copy(update={
                    "content": merged_content,
                    "version_chain_id": chain_id,
                    "supersedes": sorted({*survivor.supersedes, loser.id}),
                    "confidence": min(0.95, max(
                        float(survivor.confidence or 0.5), float(loser.confidence or 0.5)
                    ) + 0.05),
                    "stability_score": min(1.0, float(survivor.stability_score or 0.5) + 0.05),
                    "updated_at": now,
                }))
                await memory.store(session_key, loser.model_copy(update={
                    "valid_to": now,          # reversible soft-invalidation
                    "winner_flag": False,
                    "version_chain_id": chain_id,
                    "updated_at": now,
                }))
                used_ids.update({survivor.id, loser.id})
                merged += 1
            except Exception:
                logger.warning(
                    "deep tier merge store failed for %s", session_key, exc_info=True
                )
            break  # one merge per survivor per run

    if examined:
        logger.info(
            "[SDD-B3/deep] %s: pairs_examined=%d merged=%d", session_key, examined, merged
        )

    # E4: 纠正自学习。
    # 当 active 条目有 ≥2 strong_negative 信号且同 entity_key 有较新条目时，
    # 提炼策略记忆（L3 procedure）SUPERSEDE 旧条目。零 LLM，纯规则。
    correction_result = {"learned": 0, "skipped_low_signal": 0, "skipped_no_newer": 0}
    if correction_learning_enabled:
        correction_result = await _learn_from_corrections(
            memory, session_key, min_negatives=2, scan_limit=scan_limit,
        )

    # E5: 死边清理。删除 source/target 条目已不存在的 inactive 边。
    dead_edges_cleaned = 0
    if edge_store is not None:
        try:
            dead_edges_cleaned = await edge_store.cleanup_dead_edges(session_key)
        except Exception:
            logger.warning("deep tier edge cleanup failed for %s", session_key, exc_info=True)

    # E6: Deep tier 离线关系抽取。对 pool 中 active 条目批量抽取语义关系。
    relations_extracted = 0
    if edge_store is not None and llm_client is not None:
        try:
            from prismclaw.engine.memory.relation_extractor import RelationExtractor
            extractor = RelationExtractor()
            batch_text = "\n".join(
                f"[{e.entity_key}] {e.content[:500]}…"
                for e in pool if e.status == "active" and e.entity_key
            )
            # 防御性截断：避免 batch_text 超出 LLM 上下文窗口
            _MAX_BATCH_CHARS = 32_000
            if len(batch_text) > _MAX_BATCH_CHARS:
                batch_text = batch_text[:_MAX_BATCH_CHARS]
            if batch_text.strip():
                relations = await extractor.extract(batch_text, llm_client=llm_client)
                if relations:
                    relations_extracted = await extractor.resolve_and_store(
                        session_key, relations, memory=memory, edge_store=edge_store,
                    )
        except Exception:
            logger.warning("[E6] deep tier relation extraction failed for %s", session_key, exc_info=True)

    return {
        "pairs_examined": examined,
        "merged": merged,
        "correction_learned": correction_result["learned"],
        "correction_skipped_signal": correction_result["skipped_low_signal"],
        "correction_skipped_no_newer": correction_result["skipped_no_newer"],
        "dead_edges_cleaned": dead_edges_cleaned,
        "relations_extracted": relations_extracted,
    }


# ════════════════════════════════════════════════════════════════
# E4: 纠正自学习 — 从高频纠正信号中提炼策略记忆
# ════════════════════════════════════════════════════════════════


async def _learn_from_corrections(
    memory: MemoryStore,
    session_key: str,
    *,
    min_negatives: int = 2,
    scan_limit: int = _MERGE_PAIR_SCAN_LIMIT,
) -> dict[str, int]:
    """从高频纠正信号中提炼策略记忆（零 LLM，纯规则）。

    触发条件（IE4）：
    - 一条 active 条目有 >= min_negatives 个 strong_negative
    - 同一 session_key 下有 >=1 条同 entity_key 的较新 active 条目

    提炼产出：L3 procedure，content=纠正后条目的 content（不含旧信息），
    supersedes=[被纠正条目id]。原条目标记为 decayed。

    注意：使用 aggregate_validation_signals（全量信号）而非
    aggregate_pending_validation_signals（pending 信号），因为 mid tier 的
    writeback 已消费 pending 信号并盖章 last_validated_at，deep tier 再调
    pending 接口会看到空信号。E4 需要知道的是累计纠正次数，不是增量。
    """
    now = time.time()

    try:
        signals = await memory.aggregate_validation_signals(session_key)
    except Exception:
        logger.warning("correction learning signals failed for %s", session_key, exc_info=True)
        return {"learned": 0, "skipped_low_signal": 0, "skipped_no_newer": 0}

    if not signals:
        return {"learned": 0, "skipped_low_signal": 0, "skipped_no_newer": 0}

    active = await _scan_active(memory, session_key, scan_limit)
    learned = 0
    skipped_low_signal = 0
    skipped_no_newer = 0
    retirement_updates: list[tuple[str, dict[str, Any]]] = []

    for entry in active:
        neg_count = signals.get(entry.id, {}).get("strong_negative", 0)
        if neg_count < min_negatives:
            continue  # 信号不足，不计数为 skipped

        # 查找同 entity_key 的较新条目
        entity_key = getattr(entry, "entity_key", None) or ""
        if not entity_key:
            skipped_low_signal += 1  # 无 entity_key 无法定位纠正目标
            continue

        newer = [
            e for e in active
            if getattr(e, "entity_key", None) == entity_key
            and e.id != entry.id
            and (e.created_at or 0) > (entry.created_at or 0)
            and e.status == "active"
        ]
        if not newer:
            skipped_no_newer += 1
            continue

        correct = max(newer, key=lambda e: e.updated_at or e.created_at)

        # content 只存正确信息，不含旧信息和"纠正"语境
        # 旧信息通过 supersedes 关联
        strategy = MemoryEntry(
            id=f"learn_{time.time_ns():x}",
            layer="L3",
            type="procedure",
            content=correct.content,
            scope=entry.scope,
            entity_key=entity_key,
            source_session_id=correct.source_session_id,
            created_at=now,
            updated_at=now,
            confidence=0.7,
            origin_stage="correction_learning",
            supersedes=[entry.id],
            version_chain_id=entry.version_chain_id or entry.id,
        )
        try:
            await memory.store(session_key, strategy)
            # 原条目标记为 decayed，避免新旧信息同时出现在检索中
            retirement_updates.append((entry.id, {"status": "decayed"}))
            learned += 1
        except Exception:
            logger.warning(
                "correction learning store failed for %s", session_key, exc_info=True
            )

    # 批量退役被纠正的原条目
    if retirement_updates:
        try:
            await memory.apply_entry_updates(session_key, retirement_updates)
        except Exception:
            logger.warning(
                "correction learning retirement failed for %s", session_key, exc_info=True
            )

    if learned:
        logger.info(
            "[SDD-B3/deep/E4] %s: learned=%d skipped_signal=%d skipped_no_newer=%d",
            session_key, learned, skipped_low_signal, skipped_no_newer,
        )

    return {
        "learned": learned,
        "skipped_low_signal": skipped_low_signal,
        "skipped_no_newer": skipped_no_newer,
    }


_MERGE_PROMPT = """合并以下两条关于同一主题的记忆为一条完整表述。保留双方的全部信息增量，不要丢失任何细节；若两者冲突以较新表述为准。

记忆A：{a}

记忆B：{b}

直接输出合并后的单条记忆内容，不要任何解释。"""


async def _llm_merge(llm_client: Any, a: str, b: str) -> str | None:
    try:
        from prismclaw.schema.llm.request import PackedLLMRequest

        response = await llm_client.complete(
            request=PackedLLMRequest(
                messages=[{"role": "user", "content": _MERGE_PROMPT.format(a=a, b=b)}],
                system="",
                context_payload=None,
                cache_strategy={},
            )
        )
        text = (getattr(response, "text", "") or "").strip()
        return text or None
    except Exception:
        logger.warning("deep tier LLM merge failed", exc_info=True)
        return None
