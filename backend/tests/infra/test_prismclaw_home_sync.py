"""启动资产同步 —— 失败与漂移必须可检测。

历史 bug：拷贝失败只写 debug 日志，启动照常，运行时继续用旧 agents，
导致 repo 里的修改静默失效。
"""
from __future__ import annotations

from pathlib import Path

from prismclaw.infra import prismclaw_home as ph


def _make_source(project: Path) -> Path:
    source = project / "backend" / ".prismclaw"
    (source / "agents").mkdir(parents=True)
    (source / "agents" / "a.md").write_text("新版内容", encoding="utf-8")
    (source / "IDENTITY.md").write_text("身份", encoding="utf-8")
    return source


def test_verify_detects_missing_file(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    (src / "agents").mkdir(parents=True)
    (src / "agents" / "a.md").write_text("x", encoding="utf-8")
    (dst / "agents").mkdir(parents=True)

    assert ph._verify_synced(src, dst, ("agents",)) == ["agents/a.md 缺失"]


def test_verify_detects_extra_file(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    (src / "agents").mkdir(parents=True)
    (dst / "agents").mkdir(parents=True)
    (dst / "agents" / "stale.md").write_text("old", encoding="utf-8")

    assert ph._verify_synced(src, dst, ("agents",)) == ["agents/stale.md 多余"]


def test_verify_reports_no_drift_after_copy(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    (src / "agents").mkdir(parents=True)
    (src / "agents" / "a.md").write_text("same", encoding="utf-8")
    ph.shutil.copytree(src, dst)

    assert ph._verify_synced(src, dst, ("agents",)) == []


def test_verify_ignores_runtime_artifacts(tmp_path: Path) -> None:
    """__pycache__ / *.pyc 不参与一致性判定，否则每次同步都会误报。"""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    for root in (src, dst):
        (root / "agents").mkdir(parents=True)
        (root / "agents" / "a.md").write_text("x", encoding="utf-8")
        (root / "agents" / "__pycache__").mkdir()
        (root / "agents" / "__pycache__" / "a.pyc").write_bytes(b"\x00")

    assert ph._verify_synced(src, dst, ("agents",)) == []


def test_sync_end_to_end_reports_ok(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    project = tmp_path / "proj"
    _make_source(project)

    result = ph.sync_prismclaw_home(project)

    assert result.ok, f"failed={result.failed} drifted={result.drifted}"
    assert result.synced >= 1
    synced_file = home / ".prismclaw" / "assets" / "agents" / "a.md"
    assert synced_file.read_text(encoding="utf-8") == "新版内容"


def test_sync_refreshes_stale_target(tmp_path: Path, monkeypatch) -> None:
    """目标已有旧版本时必须被覆盖，而不是跳过。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    stale = home / ".prismclaw" / "assets" / "agents"
    stale.mkdir(parents=True)
    (stale / "a.md").write_text("旧版内容", encoding="utf-8")

    project = tmp_path / "proj"
    _make_source(project)

    result = ph.sync_prismclaw_home(project)

    assert result.ok
    assert (stale / "a.md").read_text(encoding="utf-8") == "新版内容"
