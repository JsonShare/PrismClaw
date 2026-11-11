from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from typing import Any

from prismclaw.schema.llm.request import PackedLLMRequest
from prismclaw.schema.memory.memory_types import ArchiveEntry, L4ArtifactEntry, MemoryEntry
from prismclaw.engine.foundation.metrics import emit

logger = logging.getLogger(__name__)

_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")
_MAX_ENTRY_PREVIEW_CHARS = 220
_MAX_ARCHIVE_PREVIEW_CHARS = 260
# SDD D16：LLM selector 单次调用预算（现场 3.9-18.9s 占检索耗时的主因）。
# LLMStreamFn.complete 无 timeout 参数，只能 wait_for 包装——取消后服务端
# 请求可能继续执行、token 费用照计（已知代价，换取回合不被单点拖垮）。
_SELECTOR_LIMIT_S = 8.0

# SDD B4 (§5.5) ranking v2 constants.
# Decay lambdas per day, tiered by write-time temporal scoping: entries with
# expires_at (short_term, SDD A4) decay fast; decision-type entries never
# decay (permanent by nature); everything else decays slowly.
_DECAY_LAMBDA_SHORT_TERM = 0.05    # half-life ≈ 14 days
_DECAY_LAMBDA_DEFAULT = 0.005      # half-life ≈ 139 days
_DECAY_LAMBDA_DECISION = 0.0
# use factor: (1 + log1p(use)) / (1 + log1p(CAP)) — saturates at 10 uses,
# and NEVER zero: an unused entry keeps a ~0.3 baseline. A multiplicative
# zero here would cold-start-lock every new memory (never retrieved →
# score 0 → never retrieved).
_USE_FACTOR_CAP = 10
_DEFAULT_CONFIDENCE = 0.5

_SELECTOR_SYSTEM = """你是一个 AI 代理运行时的持久记忆选择器。
请为当前请求挑选最相关的记忆候选。

规则：
- 优先选择稳定的用户偏好、可复用的流程以及高价值事实。
- 仅当归档中包含与当前会话直接相关的上下文时，才优先选择归档。
- 有元数据时利用元数据偏置：confidence、stability_score、origin_stage、freshness、use_count。
- 下列候选已被检索为可能相关。只要任一候选与请求有关（同一主题、实体或领域），
  你就必须至少选择其中最相关的一项——当存在相关候选却返回空选择时，
  会静默丢弃代理的长期记忆。
- 仅当所有候选都与请求毫无关联时，才返回空列表。
- 不要全选；只选择能够回答或支撑请求的内容。
- 只返回纯 JSON，不要 markdown。

格式要求：
{
  "selected_memory_ids": ["mem1", "mem2"],
  "selected_archive_ids": ["arc1"]
}
"""


async def select_relevant_memories(
    *,
    llm_client: Any,
    retrieval_query: str,
    task_profile: Any,
    memory_candidates: list[MemoryEntry],
    archive_candidates: list[ArchiveEntry | L4ArtifactEntry],
    max_memory: int,
    max_archives: int,
    selector_metrics: dict[str, Any] | None = None,
    ranking_settings: Any = None,
) -> tuple[list[MemoryEntry], list[ArchiveEntry | L4ArtifactEntry]]:
    if selector_metrics is not None:
        selector_metrics["input_memory_count"] = len(memory_candidates)
        selector_metrics["input_archive_count"] = len(archive_candidates)

    # SDD B4: ranking v2 — composite re-ordering + abstention shadow.
    v2 = _ranking_v2_enabled(ranking_settings)
    composites: dict[str, float] = {}
    if v2 and memory_candidates:
        composites = _composite_scores(memory_candidates, ranking_settings)
        _record_ranking_shadow(
            selector_metrics,
            memory_candidates=memory_candidates,
            composites=composites,
            ranking_settings=ranking_settings,
            max_memory=max_memory,
        )
        # Hard abstention (gate-3 released only): nothing deserves injection.
        threshold = _abstention_threshold(ranking_settings)
        if composites and max(composites.values()) < threshold:
            if selector_metrics is not None:
                selector_metrics["abstain_shadow"] = True
            if _abstention_hard(ranking_settings):
                if selector_metrics is not None:
                    selector_metrics["strategy"] = "abstention_hard"
                    selector_metrics["fallback"] = False
                    selector_metrics["selected_memory_ids"] = []
                    selector_metrics["selected_archive_ids"] = []
                logger.info(
                    "[SDD-B4] hard abstention: top composite %.3f < %.3f (%d candidates)",
                    max(composites.values()), threshold, len(memory_candidates),
                )
                return [], []
        # Position bias for the LLM selector: best-first ordering.
        memory_candidates = sorted(
            memory_candidates,
            key=lambda e: composites.get(e.id, 0.0),
            reverse=True,
        )

    if llm_client is None:
        selected_memories, selected_archives = _heuristic_select(
            memory_candidates=memory_candidates,
            archive_candidates=archive_candidates,
            max_memory=max_memory,
            max_archives=max_archives,
            ranking_settings=ranking_settings,
            composites=composites,
        )
        _update_selector_metrics(
            selector_metrics,
            strategy="heuristic_no_llm",
            selected_memories=selected_memories,
            selected_archives=selected_archives,
            fallback=True,
        )
        return selected_memories, selected_archives
    if len(memory_candidates) + len(archive_candidates) <= 1:
        selected_memories = memory_candidates[:max_memory]
        selected_archives = archive_candidates[:max_archives]
        _update_selector_metrics(
            selector_metrics,
            strategy="trivial_passthrough",
            selected_memories=selected_memories,
            selected_archives=selected_archives,
        )
        return selected_memories, selected_archives

    prompt = _build_selection_prompt(
        retrieval_query=retrieval_query,
        task_profile=task_profile,
        memory_candidates=memory_candidates,
        archive_candidates=archive_candidates,
        max_memory=max_memory,
        max_archives=max_archives,
    )

    async def heuristic_fallback(
        strategy: str,
    ) -> tuple[list[MemoryEntry], list[ArchiveEntry | L4ArtifactEntry]]:
        selected_memories, selected_archives = _heuristic_select(
            memory_candidates=memory_candidates,
            archive_candidates=archive_candidates,
            max_memory=max_memory,
            max_archives=max_archives,
            ranking_settings=ranking_settings,
            composites=composites,
        )
        _update_selector_metrics(
            selector_metrics,
            strategy=strategy,
            selected_memories=selected_memories,
            selected_archives=selected_archives,
            fallback=True,
        )
        return selected_memories, selected_archives

    try:
        response = await asyncio.wait_for(
            llm_client.complete(
                request=PackedLLMRequest(
                    messages=[{"role": "user", "content": prompt}],
                    system=_SELECTOR_SYSTEM,
                    context_payload=None,
                    cache_strategy={},
                ),
                temperature=0.0,
            ),
            timeout=_SELECTOR_LIMIT_S,
        )
        payload = _parse_selector_output(response.text)
    except asyncio.TimeoutError:
        emit("memory_selector_budget_exceeded", 1.0, kind="counter")
        logger.warning(
            "memory selector budget exceeded (%.1fs, %d candidates); "
            "falling back to heuristic",
            _SELECTOR_LIMIT_S,
            len(memory_candidates) + len(archive_candidates),
        )
        return await heuristic_fallback("heuristic_timeout")
    except Exception:
        logger.warning("relevant memory side-query failed", exc_info=True)
        if selector_metrics is not None:
            # SDD W6 双径打点: 解析/调用失败径
            selector_metrics["selector_empty_parse"] = True
        return await heuristic_fallback("heuristic_exception")

    selected_memory_ids = set(payload.get("selected_memory_ids") or [])
    selected_archive_ids = set(payload.get("selected_archive_ids") or [])
    selected_memories = [
        entry for entry in memory_candidates if entry.id in selected_memory_ids
    ][:max_memory]
    selected_archives = [
        entry for entry in archive_candidates if entry.id in selected_archive_ids
    ][:max_archives]
    strategy = "llm"
    # SDD W6 (2026-08-21): 信任模型的有效空选择。旧逻辑把"模型正确判定
    # 无相关记忆"也当作失败, 用启发式全量倾倒覆盖 —— 无关提问反而注入
    # 垃圾记忆。回退仅在异常路径(heuristic_exception)保留。新 prompt
    # (MUST select at least one when relevant)已把误选空率从 ~40% 压到
    # 0/8(实测), 此处只保留打点供观察期复核。
    if not selected_memories and memory_candidates:
        if selector_metrics is not None:
            selector_metrics["selector_empty_choice"] = True
        logger.info(
            "[SDD-W6] selector returned empty memory selection "
            "(candidates=%d, query=%.60r) — trusted",
            len(memory_candidates), retrieval_query,
        )
    if not selected_archives and archive_candidates:
        if selector_metrics is not None:
            selector_metrics["selector_empty_archive_choice"] = True
    _update_selector_metrics(
        selector_metrics,
        strategy=strategy,
        selected_memories=selected_memories,
        selected_archives=selected_archives,
        fallback=False,
    )
    return selected_memories, selected_archives


def _build_selection_prompt(
    *,
    retrieval_query: str,
    task_profile: Any,
    memory_candidates: list[MemoryEntry],
    archive_candidates: list[ArchiveEntry | L4ArtifactEntry],
    max_memory: int,
    max_archives: int,
) -> str:
    task_name = getattr(task_profile, "name", "general") or "general"
    task_priority = getattr(task_profile, "priority", "balanced") or "balanced"
    memory_lines = [
        (
            f'- id={entry.id} layer={entry.layer} type={entry.type} '
            f'score={entry.score if entry.score is not None else "na"} '
            f'confidence={getattr(entry, "confidence", None) if getattr(entry, "confidence", None) is not None else "na"} '
            f'stability_score={getattr(entry, "stability_score", None) if getattr(entry, "stability_score", None) is not None else "na"} '
            f'origin_stage={getattr(entry, "origin_stage", "na")} '
            f'use_count={getattr(entry, "use_count", 0)} '
            f'freshness={_freshness_bucket(getattr(entry, "updated_at", None))} '
            f'content="{_clip(entry.content, _MAX_ENTRY_PREVIEW_CHARS)}"'
        )
        for entry in memory_candidates
    ]
    archive_lines = [
        (
            f'- id={entry.id} session={entry.session_id} '
            f'kind={getattr(entry, "artifact_kind", "session_archive")} '
            f'similarity={entry.similarity if entry.similarity is not None else "na"} '
            f'score={getattr(entry, "score", None) if getattr(entry, "score", None) is not None else "na"} '
            f'freshness={_freshness_bucket(getattr(entry, "created_at", None))} '
            f'summary="{_clip(entry.summary, _MAX_ARCHIVE_PREVIEW_CHARS)}"'
        )
        for entry in archive_candidates
    ]
    return (
        f"request: {retrieval_query}\n"
        f"task_profile: {task_name} / {task_priority}\n"
        f"max_memory: {max_memory}\n"
        f"max_archives: {max_archives}\n\n"
        "memory_candidates:\n"
        f"{chr(10).join(memory_lines) if memory_lines else '(none)'}\n\n"
        "archive_candidates:\n"
        f"{chr(10).join(archive_lines) if archive_lines else '(none)'}"
    )


def _parse_selector_output(text: str) -> dict[str, Any]:
    raw = text.strip()
    start = raw.find("{")
    if start != -1:
        try:
            payload, _ = json.JSONDecoder().raw_decode(raw[start:])
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass
    match = _JSON_OBJECT_RE.search(raw)
    if match:
        raw = match.group(0)
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("selector output must be a JSON object")
    return payload


def _clip(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    head = limit // 2
    tail = limit - head
    return f"{cleaned[:head]} ... {cleaned[-tail:]}"


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


def _heuristic_select(
    *,
    memory_candidates: list[MemoryEntry],
    archive_candidates: list[ArchiveEntry | L4ArtifactEntry],
    max_memory: int,
    max_archives: int,
    ranking_settings: Any = None,
    composites: dict[str, float] | None = None,
) -> tuple[list[MemoryEntry], list[ArchiveEntry | L4ArtifactEntry]]:
    if composites:
        # SDD B4: composite ordering (decay applied per ranking_decay_apply
        # inside _composite_scores — the uncertainty-period switch).
        ranked_memories = sorted(
            memory_candidates,
            key=lambda entry: composites.get(entry.id, 0.0),
            reverse=True,
        )
    else:
        # 两阶段排序（SDD D5 / §2.8f）：相关性（检索序）先行，层级只在同带内
        # 微调——检索 score 是 RRF（1/(60+rank)）值域极窄，不能直接分桶，
        # 按候选内排序位次折算 relevance band（≤3→3; ≤10→2; ≤20→1; >20→0）。
        scored = sorted(
            memory_candidates,
            key=lambda entry: _safe_float(getattr(entry, "score", None)),
            reverse=True,
        )
        band_by_id = {
            str(entry.id): _relevance_band(rank)
            for rank, entry in enumerate(scored, start=1)
        }
        ranked_memories = sorted(
            scored,
            key=lambda entry: _memory_rank_key(
                entry, relevance_band=band_by_id.get(str(entry.id), 0)
            ),
            reverse=True,
        )
    ranked_archives = sorted(
        archive_candidates,
        key=_archive_rank_key,
        reverse=True,
    )
    return ranked_memories[:max_memory], ranked_archives[:max_archives]


# ── SDD B4 (§5.5): ranking v2 ────────────────────────────────────


def _ranking_v2_enabled(ranking_settings: Any) -> bool:
    return bool(getattr(ranking_settings, "ranking_v2_enabled", False))


def _decay_applied(ranking_settings: Any) -> bool:
    return bool(getattr(ranking_settings, "ranking_decay_apply", False))


def _abstention_hard(ranking_settings: Any) -> bool:
    return bool(getattr(ranking_settings, "abstention_hard_enabled", False))


def _abstention_threshold(ranking_settings: Any) -> float:
    value = getattr(ranking_settings, "abstention_score_threshold", None)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    return 0.1


def _decay_lambda(entry: MemoryEntry) -> float:
    """Per-day decay rate, tiered by write-time temporal scoping (SDD §5.5)."""
    if getattr(entry, "expires_at", None) is not None:
        return _DECAY_LAMBDA_SHORT_TERM
    if str(getattr(entry, "type", "") or "").lower() == "decision":
        return _DECAY_LAMBDA_DECISION
    return _DECAY_LAMBDA_DEFAULT


def _composite_scores(
    candidates: list[MemoryEntry], ranking_settings: Any
) -> dict[str, float]:
    """Multiplicative composite per SDD §5.5:

        relevance × decay × log-capped use × confidence × expansion_bias

    - relevance: batch-local rank normalization 1/(1+rank) by entry.score
      DESC — robust to the heterogeneous score scales across the BM25 /
      vector / RRF retrieval paths (raw magnitudes are not comparable).
    - decay: exp(-λ·age_days) with λ tiered by temporal scope. When
      ranking_decay_apply is False (uncertainty period) the factor is
      computed for the counterfactual record but neutralized (=1.0) for
      ordering.
    - use: (1+log1p(use))/(1+log1p(10)), saturating at 10 uses with a
      ~0.3 floor for never-used entries — rich-get-richer capped, cold
      start preserved.
    - confidence: defaults to 0.5 when unset.
    - expansion_bias: 0.6 for edge_expansion entries (E7), 1.0 otherwise.
    """
    decay_applied = _decay_applied(ranking_settings)
    by_score = sorted(
        candidates,
        key=lambda e: (_safe_float(getattr(e, "score", None)), _safe_float(getattr(e, "updated_at", None))),
        reverse=True,
    )
    scores: dict[str, float] = {}
    now = time.time()
    use_denom = 1.0 + math.log1p(_USE_FACTOR_CAP)
    for rank, entry in enumerate(by_score):
        relevance = 1.0 / (1.0 + rank)
        lam = _decay_lambda(entry)
        if decay_applied and lam > 0.0:
            updated = getattr(entry, "updated_at", None)
            if isinstance(updated, (int, float)) and not isinstance(updated, bool):
                age_days = max(0.0, (now - float(updated)) / 86400.0)
                decay = math.exp(-lam * age_days)
            else:
                decay = 1.0
        else:
            decay = 1.0
        use = float(getattr(entry, "use_count", 0) or 0)
        use_factor = min(1.0, (1.0 + math.log1p(max(0.0, use))) / use_denom)
        confidence = getattr(entry, "confidence", None)
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            confidence = _DEFAULT_CONFIDENCE
        # E7: edge_expansion 条目施加 0.6 乘性偏置
        expansion_bias = 0.6 if getattr(entry, "retrieval_path", None) == "edge_expansion" else 1.0
        scores[entry.id] = relevance * decay * use_factor * float(confidence) * expansion_bias
    return scores


def _record_ranking_shadow(
    selector_metrics: dict[str, Any] | None,
    *,
    memory_candidates: list[MemoryEntry],
    composites: dict[str, float],
    ranking_settings: Any,
    max_memory: int,
) -> None:
    """Uncertainty-period bookkeeping (SDD §5.5): decay counterfactual.

    When ranking_decay_apply is off, ordering already ignores decay; compute
    the with-decay ordering and record whether the top-k WOULD differ — the
    4-week counterfactual dataset for the manual release decision.
    """
    if selector_metrics is None or not composites:
        return
    selector_metrics["ranking_v2"] = True
    selector_metrics["top_composite"] = round(max(composites.values()), 4)
    if _decay_applied(ranking_settings):
        return
    # Counterfactual: recompute with decay forced on (shadow settings).
    class _ShadowSettings:
        ranking_v2_enabled = True
        ranking_decay_apply = True

    with_decay = _composite_scores(memory_candidates, _ShadowSettings())
    top_ids_no_decay = sorted(composites, key=composites.get, reverse=True)[:max_memory]
    top_ids_decay = sorted(with_decay, key=with_decay.get, reverse=True)[:max_memory]
    differs = top_ids_no_decay != top_ids_decay
    selector_metrics["decay_counterfactual_differs"] = differs
    if differs:
        logger.info(
            "[SDD-B4] decay shadow: top-%d would change with decay applied "
            "(no-decay=%s decay=%s)",
            max_memory,
            top_ids_no_decay,
            top_ids_decay,
        )


def _relevance_band(rank: int) -> int:
    """检索序 → 相关性分桶（SDD §2.8f：RRF score 值域极窄，按位次折算）。"""
    if rank <= 3:
        return 3
    if rank <= 10:
        return 2
    if rank <= 20:
        return 1
    return 0


def _memory_rank_key(
    entry: MemoryEntry, *, relevance_band: int
) -> tuple[float, ...]:
    # 首位是相关性带（SDD D5）：相关性支配选择；层级 bias 降级为同带内的
    # 次级 tie-break，不再凌驾于"与查询相关"之上。
    layer_tier = 1.0 if getattr(entry, "layer", "") == "L3" else 0.8
    confidence = _safe_float(getattr(entry, "confidence", None))
    stability = _safe_float(getattr(entry, "stability_score", None))
    retrieval_score = _safe_float(getattr(entry, "score", None))
    freshness = _freshness_score(getattr(entry, "updated_at", None))
    use_count = min(1.0, max(0.0, float(getattr(entry, "use_count", 0) or 0)) / 10.0)
    origin_bias = _origin_stage_bias(getattr(entry, "origin_stage", None))
    # E7: edge_expansion 条目对 confidence 和 origin_bias 施加 0.6 乘性偏置
    # 与 _composite_scores 的乘性偏置保持一致
    if getattr(entry, "retrieval_path", None) == "edge_expansion":
        confidence *= 0.6
        origin_bias *= 0.6
    return (
        float(relevance_band),
        layer_tier,
        confidence,
        stability,
        freshness,
        use_count,
        origin_bias,
        retrieval_score,
    )


def _archive_rank_key(entry: ArchiveEntry | L4ArtifactEntry) -> tuple[float, ...]:
    kind_bias = _artifact_kind_bias(getattr(entry, "artifact_kind", None))
    retrieval_score = _safe_float(getattr(entry, "score", None))
    similarity = _safe_float(getattr(entry, "similarity", None))
    freshness = _freshness_score(getattr(entry, "created_at", None))
    return (kind_bias, retrieval_score, similarity, freshness)


def _safe_float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _freshness_score(timestamp: Any) -> float:
    bucket = _freshness_bucket(timestamp)
    return {
        "fresh": 1.0,
        "recent": 0.8,
        "warm": 0.5,
        "stale": 0.2,
        "unknown": 0.0,
    }[bucket]


def _origin_stage_bias(value: Any) -> float:
    return {
        "online": 1.0,
        "light": 0.8,
        "deep": 0.7,
        "rem": 0.5,
    }.get(str(value or "").lower(), 0.0)


def _artifact_kind_bias(value: Any) -> float:
    normalized = str(value or "").strip().lower()
    return {
        "pattern": 1.0,
        "graph": 0.95,
        "relation": 0.9,
        "archive": 0.85,
        "session_archive": 0.7,
    }.get(normalized, 0.7)


def _update_selector_metrics(
    selector_metrics: dict[str, Any] | None,
    *,
    strategy: str,
    selected_memories: list[MemoryEntry],
    selected_archives: list[ArchiveEntry | L4ArtifactEntry],
    fallback: bool = False,
) -> None:
    if selector_metrics is None:
        return
    selector_metrics["strategy"] = strategy
    selector_metrics["fallback"] = fallback
    selector_metrics["selected_memory_ids"] = [entry.id for entry in selected_memories]
    selector_metrics["selected_archive_ids"] = [
        _archive_identifier(entry) for entry in selected_archives
    ]


def _archive_identifier(entry: ArchiveEntry | L4ArtifactEntry) -> str:
    entry_id = getattr(entry, "id", None)
    if isinstance(entry_id, str) and entry_id:
        return entry_id
    session_id = getattr(entry, "session_id", None)
    if isinstance(session_id, str) and session_id:
        return session_id
    return "<unknown-archive>"
