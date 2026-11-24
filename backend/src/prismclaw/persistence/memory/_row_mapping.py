from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, cast

from sqlalchemy import RowMapping

from prismclaw.persistence.memory._helpers import _parse_supersedes, _rrf_score
from prismclaw.schema.memory.entry import (
    ArchiveEntry,
    L4ArtifactEntry,
    MemoryEntry,
)

logger = logging.getLogger(__name__)


def _memory_entry_from_row(row: RowMapping, *, score: float) -> MemoryEntry:
    return MemoryEntry(
        id=row["id"],
        layer=row["layer"],
        type=row["type"],
        content=row["content"],
        scope=row.get("scope"),
        entity_key=row.get("entity_key"),
        source_session_id=row["source_session_id"],
        source_refs=[str(item) for item in row.get("source_refs_json", []) if str(item)],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_used_at=row["last_used_at"],
        use_count=row["use_count"],
        status=row["status"],
        origin_stage=row["origin_stage"],
        confidence=row["confidence"],
        stability_score=row["stability_score"],
        cluster_id=row["cluster_id"],
        supersedes=_parse_supersedes(row["supersedes_json"]),
        last_validated_at=row["last_validated_at"],
        graph_group_id=row["graph_group_id"],
        version_chain_id=row.get("version_chain_id"),
        publish_state=row.get("publish_state"),
        valid_from=row.get("valid_from"),
        valid_to=row.get("valid_to"),
        expires_at=row.get("expires_at"),
        winner_flag=bool(row.get("winner_flag", True)),
        low_confidence=bool(row["low_confidence"]),
        score=score,
    )


def _row_label(row: object) -> str:
    """尽力取出 row 的 id 用于告警；任何异常都不得中断隔离。"""
    try:
        if isinstance(row, dict):
            return str(row.get("id", "?"))
        return str(getattr(row, "id", "?"))
    except Exception:
        return "?"


def _entries_from_rows(rows: Sequence[RowMapping], *, score: float) -> list[MemoryEntry]:
    """逐行映射，单行非法只丢该行。

    写边界（store）仍由 MemoryEntry 严格校验；读边界必须隔离——一行历史
    遗留数据（如旧词表取值）不应让整批检索/扫描失败。
    """
    entries: list[MemoryEntry] = []
    for row in rows:
        try:
            entries.append(_memory_entry_from_row(row, score=score))
        except Exception:
            logger.warning("memory row dropped: id=%s", _row_label(row), exc_info=True)
    return entries


def _artifact_entry_from_row(
    row: RowMapping, *, score: float, similarity: float | None
) -> L4ArtifactEntry:
    if isinstance(row, L4ArtifactEntry):
        return row
    payload = row.get("payload_json")
    if not isinstance(payload, dict):
        payload = {}
    source_refs = row.get("source_refs_json")
    if not isinstance(source_refs, list):
        source_refs = []
    return L4ArtifactEntry(
        id=row["id"],
        artifact_kind=row["artifact_kind"],
        session_id=row["session_id"],
        summary=row["summary"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        graph_group_id=row["graph_group_id"],
        source_refs=[str(item) for item in source_refs],
        origin_stage=row["origin_stage"],
        publish_state=row.get("publish_state"),
        payload={str(key): value for key, value in payload.items()},
        score=score,
        similarity=similarity,
        low_confidence=bool(row["low_confidence"]),
    )


def _merge_memory_candidates(
    text_rows: list[RowMapping],
    vector_rows: list[RowMapping],
    *,
    limit: int,
    per_layer_limits: dict[str, int] | None = None,
) -> list[MemoryEntry]:
    merged: dict[str, dict[str, Any]] = {}
    for source, rows in (("text", text_rows), ("vector", vector_rows)):
        for rank, row in enumerate(rows, start=1):
            entry_id = str(row["id"])
            bucket = merged.setdefault(
                entry_id,
                {
                    "row": row,
                    "text_rank": None,
                    "vector_rank": None,
                    "text_score": 0.0,
                    "vector_score": 0.0,
                },
            )
            bucket["row"] = row
            bucket[f"{source}_rank"] = rank
            source_score = float(row.get("retrieval_score") or 0.0)
            bucket[f"{source}_score"] = max(bucket[f"{source}_score"], source_score)

    ranked: list[MemoryEntry] = []
    for payload in merged.values():
        combined_score = 0.0
        if payload["text_rank"] is not None:
            combined_score += _rrf_score(int(payload["text_rank"]))
        if payload["vector_rank"] is not None:
            combined_score += _rrf_score(int(payload["vector_rank"]))
        if combined_score <= 0.0:
            combined_score = max(float(payload["text_score"]), float(payload["vector_score"]))
        try:
            ranked.append(_memory_entry_from_row(payload["row"], score=combined_score))
        except Exception:
            # 检索边界隔离：单条历史脏数据不得让整轮检索返回空。
            logger.warning(
                "memory search row dropped: id=%s", _row_label(payload["row"]), exc_info=True
            )

    ranked.sort(
        key=lambda item: (
            float(item.score or 0.0),
            float(item.updated_at),
            float(item.created_at),
        ),
        reverse=True,
    )
    if not per_layer_limits:
        return ranked[:limit]

    remaining = {layer: max(0, int(value)) for layer, value in per_layer_limits.items()}
    selected: list[MemoryEntry] = []
    for entry in ranked:
        layer_key = str(entry.layer)
        if layer_key not in remaining:
            continue
        if remaining[layer_key] <= 0:
            continue
        remaining[layer_key] -= 1
        selected.append(entry)
    return selected


class _HasId(Protocol):
    @property
    def id(self) -> object: ...


def _row_id(row: Mapping[str, Any] | object) -> str:
    if isinstance(row, Mapping):
        return str(row["id"])
    return str(cast(_HasId, row).id)


def _row_score(row: Mapping[str, Any] | object, key: str, default: float = 0.0) -> float:
    if isinstance(row, Mapping):
        return float(row.get(key) or default)
    return float(getattr(row, key, default) or default)


def _merge_artifact_candidates(
    text_rows: list[RowMapping],
    vector_rows: list[RowMapping],
    *,
    limit: int,
) -> list[L4ArtifactEntry]:
    merged: dict[str, dict[str, Any]] = {}
    for source, rows in (("text", text_rows), ("vector", vector_rows)):
        for rank, row in enumerate(rows, start=1):
            entry_id = _row_id(row)
            bucket = merged.setdefault(
                entry_id,
                {
                    "row": row,
                    "text_rank": None,
                    "vector_rank": None,
                    "text_score": 0.0,
                    "vector_score": 0.0,
                    "similarity": None,
                },
            )
            bucket["row"] = row
            bucket[f"{source}_rank"] = rank
            bucket[f"{source}_score"] = max(
                float(bucket[f"{source}_score"]),
                _row_score(row, "retrieval_score"),
            )
            if source == "vector":
                sim = (
                    row["similarity"] if isinstance(row, dict) else getattr(row, "similarity", None)
                )
                if sim is not None:
                    bucket["similarity"] = float(sim)

    ranked: list[L4ArtifactEntry] = []
    for payload in merged.values():
        combined_score = 0.0
        if payload["text_rank"] is not None:
            combined_score += _rrf_score(int(payload["text_rank"]))
        if payload["vector_rank"] is not None:
            combined_score += _rrf_score(int(payload["vector_rank"]))
        if combined_score <= 0.0:
            combined_score = max(float(payload["text_score"]), float(payload["vector_score"]))
        entry = _artifact_entry_from_row(
            payload["row"],
            score=combined_score,
            similarity=payload["similarity"],
        )
        if isinstance(payload["row"], L4ArtifactEntry):
            entry = entry.model_copy(
                update={"score": combined_score, "similarity": payload["similarity"]}
            )
        ranked.append(entry)

    ranked.sort(
        key=lambda item: (
            float(item.score or item.similarity or 0.0),
            float(item.updated_at or item.created_at),
            float(item.created_at),
        ),
        reverse=True,
    )
    return ranked[:limit]


def _build_search_meta_from_rows(
    selected_entries: Sequence[MemoryEntry | ArchiveEntry | L4ArtifactEntry],
    *,
    text_rows: list[Any],
    vector_rows: list[Any],
    vector_requested: bool,
    vector_fallback: bool,
    recency_rows: list[Any] | None = None,
) -> dict[str, Any]:
    text_ids = {str(row["id"]) for row in text_rows}
    vector_ids = {str(row["id"]) for row in vector_rows}
    recency_ids = {str(row["id"]) for row in (recency_rows or [])}
    source_mix: dict[str, int] = {}
    for entry in selected_entries:
        entry_id = str(entry.id)
        if entry_id in text_ids and entry_id in vector_ids:
            bucket = "hybrid"
        elif entry_id in vector_ids:
            bucket = "vector_only"
        elif entry_id in text_ids:
            bucket = "text_only"
        elif entry_id in recency_ids:
            bucket = "recency_only"
        else:
            bucket = "unknown"
        source_mix[bucket] = source_mix.get(bucket, 0) + 1
    return {
        "text_candidate_count": len(text_rows),
        "vector_candidate_count": len(vector_rows),
        "selected_count": len(selected_entries),
        "source_mix": source_mix,
        "vector_requested": vector_requested,
        "vector_fallback": vector_requested and vector_fallback,
    }
