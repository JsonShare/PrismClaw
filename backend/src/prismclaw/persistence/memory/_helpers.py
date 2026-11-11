from __future__ import annotations

import hashlib
import json
from typing import Any


_RRF_K = 60.0


def _compute_content_hash(entry: Any) -> str:
    """E2: 计算内容完整性哈希。

    注意（S5 已知限制）：无 salt，DBA 可同时修改 content 和 content_hash
    完美伪造。价值仅限"检测应用层误改"和"合规审计证据"。
    """
    payload = f"{entry.id}\n{entry.content}\n{entry.entity_key}\n{entry.scope}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _parse_supersedes(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except Exception:
            return [raw]
        if isinstance(payload, list):
            return [str(item) for item in payload]
    return []


def _sql_text(sql: str):
    from sqlalchemy import text as sa_text  # type: ignore[import-not-found]

    return sa_text(sql)


def _clean_query(query: str) -> str:
    return " ".join(query.split())


def _rrf_score(rank: int) -> float:
    if rank <= 0:
        return 0.0
    return 1.0 / (_RRF_K + float(rank))


def _candidate_limit(limit: int, per_layer_limits: dict[str, int] | None = None) -> int:
    requested = max(1, limit)
    if per_layer_limits:
        requested = max(requested, sum(max(0, int(v)) for v in per_layer_limits.values()))
    return max(10, requested * 3)


def _build_layer_filter_sql(layers: list[str] | None) -> tuple[str, dict[str, str]]:
    if not layers:
        return "", {}
    params = {f"layer_{index}": layer for index, layer in enumerate(layers)}
    placeholders = ", ".join(f":layer_{index}" for index in range(len(layers)))
    return f" AND layer IN ({placeholders})", params
