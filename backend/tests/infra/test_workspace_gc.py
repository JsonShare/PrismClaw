"""`.work` 可重算产物的 TTL 回收。"""
from __future__ import annotations

import os
import time
from pathlib import Path

from prismclaw.infra.workspace_gc import EPHEMERAL_SUBDIRS, sweep_base, sweep_workspace


def _write(path: Path, *, age_days: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    ts = time.time() - age_days * 86400
    os.utime(path, (ts, ts))


def test_sweep_workspace_deletes_only_expired(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    old = workspace / ".work" / "cache" / "ocr" / "old.txt"
    young = workspace / ".work" / "cache" / "ocr" / "young.txt"
    _write(old, age_days=30)
    _write(young, age_days=1)

    report = sweep_workspace(workspace, older_than_days=7)

    assert not old.exists()
    assert young.exists()
    assert report.removed_files == 1
    assert report.freed_bytes > 0


def test_sweep_workspace_covers_all_declared_dirs_and_prunes_empty(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    for parts in EPHEMERAL_SUBDIRS:
        _write(workspace / ".work" / Path(*parts) / "nested" / "stale.bin", age_days=60)

    report = sweep_workspace(workspace, older_than_days=7)

    assert report.removed_files == len(EPHEMERAL_SUBDIRS)
    assert report.removed_dirs == len(EPHEMERAL_SUBDIRS)
    for parts in EPHEMERAL_SUBDIRS:
        root = workspace / ".work" / Path(*parts)
        assert not (root / "nested").exists()


def test_sweep_base_skips_missing_and_keeps_young(tmp_path: Path) -> None:
    assert sweep_base(tmp_path / "missing").removed_files == 0

    old = tmp_path / "web_alice" / "default" / ".work" / "run" / "spills" / "old.txt"
    young = tmp_path / "web_bob" / "default" / ".work" / "run" / "spills" / "young.txt"
    _write(old, age_days=30)
    _write(young, age_days=1)

    report = sweep_base(tmp_path, older_than_days=7)

    assert not old.exists()
    assert young.exists()
    assert report.removed_files == 1


def test_sweep_covers_session_caches_and_extracted_images(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    old_img = workspace / "sessions" / "s1" / "context_blocks" / "images" / "k" / "old.jpg"
    old_ocr = workspace / "sessions" / "s1" / ".work" / "cache" / "ocr" / "k" / "x.json"
    young_img = workspace / "sessions" / "s1" / "context_blocks" / "images" / "k" / "new.jpg"
    _write(old_img, age_days=30)
    _write(old_ocr, age_days=30)
    _write(young_img, age_days=1)

    report = sweep_workspace(workspace, older_than_days=7)

    assert not old_img.exists()
    assert not old_ocr.exists()
    assert young_img.exists()
    assert report.removed_files == 2
