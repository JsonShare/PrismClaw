"""T2-4 回归（第三轮审查发现）：DreamPublicationMetrics 删除 legacy 字段后，
_build_quality_metrics 的残留引用已移除——本测试锁定 quality 评分不再访问
已删除的 slots 字段（否则 AttributeError）。"""

from __future__ import annotations

from types import SimpleNamespace

from prismclaw.engine.dream.state.status_service import (
    DreamPublicationMetrics,
    DreamStateWatermarks,
    _build_quality_metrics,
)


def test_publication_metrics_has_no_legacy_fields() -> None:
    metrics = DreamPublicationMetrics()
    assert not hasattr(metrics, "legacy_archive_count")
    assert not hasattr(metrics, "legacy_archive_backlog_count")
    assert metrics.archive_artifact_count == 0


def test_quality_metrics_runs_without_legacy_fields() -> None:
    publications = DreamPublicationMetrics()
    state = SimpleNamespace(dream_lock_owner=None, dream_pending=False)
    latest_batch = None
    watermarks = DreamStateWatermarks()

    quality = _build_quality_metrics(
        state=state,  # type: ignore[arg-type]
        latest_batch=latest_batch,
        audit_entries=[],
        publications=publications,
        watermarks=watermarks,
        dead_letters=[],
        replay_candidates=[],
    )
    assert 0 <= quality.score <= 100