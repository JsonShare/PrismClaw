"""E3: 记忆价值评估函数。

多因子评分，用于 Mid tier 的价值驱动降级循环。
所有权重因子通过常量暴露，可在 env 中配置。
"""
from __future__ import annotations

import math
import time

from prismclaw.schema.memory.memory_types import MemoryEntry

# ── 权重常量（初始启发式） ──
_IMPORTANCE_WEIGHTS = {"L3": 3.0, "L2": 1.5}
_CONFIDENCE_WEIGHT = 3.0
_STABILITY_WEIGHT = 2.0
_IMPORTANCE_WEIGHT = 2.0
_RECENCY_WEIGHT = 2.0
_USE_COUNT_WEIGHT = 0.1
_USE_COUNT_CAP = 5.0
_CONNECTIVITY_WEIGHT = 0.5
_CONNECTIVITY_CAP = 2.0

# ── 衰减 lambda（每日） ──
_DECAY_SHORT_TERM = 0.05   # expires_at 存在时
_DECAY_DECISION = 0.0      # type=decision 时永不衰减
_DECAY_DEFAULT = 0.005     # 其他


def memory_value(
    entry: MemoryEntry,
    *,
    now: float | None = None,
    edge_degree: int = 0,
) -> float:
    """计算记忆条目的多因子价值评分。

    Args:
        entry: 记忆条目
        now: 当前时间戳（默认 time.time()）
        edge_degree: 图谱连接度（E5 未上线时传 0）
    """
    if now is None:
        now = time.time()

    # 重要性（层级权重）
    importance = _IMPORTANCE_WEIGHTS.get(entry.layer, 1.0)

    # 可信度
    confidence = float(entry.confidence if entry.confidence is not None else 0.5)

    # 稳定性
    stability = float(entry.stability_score if entry.stability_score is not None else 0.5)

    # 时效性
    age_days = max(0.0, (now - float(entry.updated_at or entry.created_at)) / 86400.0)
    if entry.type == "decision":
        recency = 1.0
    elif entry.expires_at is not None:
        recency = max(0.0, 1.0 - _DECAY_SHORT_TERM * age_days)
    else:
        recency = max(0.0, 1.0 - _DECAY_DEFAULT * age_days)

    # 使用频率
    use_count = float(getattr(entry, "use_count", 0) or 0)
    use_score = min(1.0 + use_count * _USE_COUNT_WEIGHT, _USE_COUNT_CAP)

    # 图谱连接度（E5 上线后有效，否则为 0）
    connectivity = min(math.log1p(edge_degree) * _CONNECTIVITY_WEIGHT, _CONNECTIVITY_CAP)

    # 发布态权重
    publish = str(getattr(entry, "publish_state", "") or "").strip().lower()
    publish_weight = {
        "published": 1.0, "reviewed": 0.9, "canary": 0.7,
    }.get(publish, 0.5)

    return (
        importance * _IMPORTANCE_WEIGHT
        + confidence * _CONFIDENCE_WEIGHT
        + stability * _STABILITY_WEIGHT
        + recency * _RECENCY_WEIGHT
        + use_score
        + connectivity
    ) * publish_weight
