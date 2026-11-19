"""review blob 全基目录回收：只删无引用且超期的 blob。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from prismclaw.engine.review.lifecycle import sweep_workspace_base


def _make_edits(base: Path, user: str, workspace: str) -> Path:
    edits = base / f"web_{user}" / workspace / ".work" / "audit" / "edits"
    (edits / "blobs").mkdir(parents=True)
    return edits


def _blob(edits: Path, sha: str, *, age_days: int) -> Path:
    path = edits / "blobs" / sha
    path.write_text("x", encoding="utf-8")
    old = time.time() - age_days * 86400
    os.utime(path, (old, old))
    return path


def _reference(edits: Path, sha: str) -> None:
    line = {"payload": {"content_ref": f"blobs/{sha}"}}
    (edits / "review_events.jsonl").write_text(
        json.dumps(line) + "\n", encoding="utf-8"
    )


def test_sweep_removes_only_old_unreferenced(tmp_path: Path) -> None:
    edits = _make_edits(tmp_path, "u1", "ws1")
    dead = _blob(edits, "dead", age_days=60)
    live = _blob(edits, "live", age_days=60)
    young = _blob(edits, "young", age_days=1)
    _reference(edits, "live")

    report = sweep_workspace_base(tmp_path, dry_run=False, older_than_days=30)

    assert not dead.exists()
    assert live.exists()
    assert young.exists()
    assert report.removed == 1
    assert report.kept_referenced == 1
    assert report.kept_young == 1


def test_sweep_covers_all_users(tmp_path: Path) -> None:
    first = _make_edits(tmp_path, "u1", "ws1")
    second = _make_edits(tmp_path, "u2", "ws2")
    _blob(first, "a", age_days=60)
    _blob(second, "b", age_days=60)

    report = sweep_workspace_base(tmp_path, dry_run=False, older_than_days=30)

    assert report.removed == 2
    assert not (first / "blobs" / "a").exists()
    assert not (second / "blobs" / "b").exists()


def test_sweep_missing_base_is_noop(tmp_path: Path) -> None:
    report = sweep_workspace_base(tmp_path / "nope", dry_run=False)
    assert report.removed == 0
    assert report.total_blobs == 0
