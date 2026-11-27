"""启动资产同步 —— 每次启动全量覆盖，失败必须中止启动。

历史 bug：拷贝失败只写 debug 日志，启动照常，运行时继续用旧 agents，
导致 repo 里的修改静默失效。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from prismclaw.infra import prismclaw_home as ph


def _make_source(project: Path) -> Path:
    source = project / "backend" / ".prismclaw"
    (source / "agents").mkdir(parents=True)
    (source / "agents" / "a.md").write_text("新版内容", encoding="utf-8")
    (source / "IDENTITY.md").write_text("身份", encoding="utf-8")
    return source


def _setup_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def test_verify_reports_missing_file(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    (src / "agents").mkdir(parents=True)
    (src / "agents" / "a.md").write_text("x", encoding="utf-8")
    (dst / "agents").mkdir(parents=True)

    assert ph._verify_tree(src, dst) == ["agents/a.md 缺失"]


def test_verify_reports_content_mismatch(tmp_path: Path) -> None:
    """同尺寸不同内容必须算漂移——不能用 size 近似。"""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    for root, body in ((src, "abcd"), (dst, "abce")):
        (root / "agents").mkdir(parents=True)
        (root / "agents" / "a.md").write_text(body, encoding="utf-8")

    assert ph._verify_tree(src, dst) == ["agents/a.md 内容不一致"]


def test_verify_reports_extra_file(tmp_path: Path) -> None:
    """覆盖语义下目标侧多余文件也是漂移（下次启动会被清掉）。"""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    (src / "agents").mkdir(parents=True)
    (dst / "agents").mkdir(parents=True)
    (dst / "agents" / "user-notes.md").write_text("stale", encoding="utf-8")

    assert ph._verify_tree(src, dst) == ["agents/user-notes.md 多余"]


def test_verify_ignores_runtime_artifacts(tmp_path: Path) -> None:
    """__pycache__ / *.pyc 不参与一致性判定，否则每次同步都会误报。"""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    for root in (src, dst):
        (root / "agents").mkdir(parents=True)
        (root / "agents" / "a.md").write_text("x", encoding="utf-8")
        (root / "agents" / "__pycache__").mkdir()
        (root / "agents" / "__pycache__" / "a.pyc").write_bytes(b"\x00")

    assert ph._verify_tree(src, dst) == []


def test_sync_end_to_end_reports_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _setup_home(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    _make_source(project)

    result = ph.sync_prismclaw_home(project)

    assert result.ok, f"failed={result.failed} drifted={result.drifted}"
    synced_file = home / ".prismclaw" / "assets" / "agents" / "a.md"
    assert synced_file.read_text(encoding="utf-8") == "新版内容"


def test_sync_overwrites_stale_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """目标已有旧版本时必须被覆盖，而不是跳过。"""
    home = _setup_home(tmp_path, monkeypatch)
    stale = home / ".prismclaw" / "assets" / "agents"
    stale.mkdir(parents=True)
    (stale / "a.md").write_text("旧版内容", encoding="utf-8")

    project = tmp_path / "proj"
    _make_source(project)

    result = ph.sync_prismclaw_home(project)

    assert result.ok, f"failed={result.failed} drifted={result.drifted}"
    assert (stale / "a.md").read_text(encoding="utf-8") == "新版内容"


def test_sync_wipes_unmanaged_files_in_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """assets/ 下的一切都在覆盖范围：残留文件不得跨启动存活。"""
    home = _setup_home(tmp_path, monkeypatch)
    residual = home / ".prismclaw" / "assets" / "experts" / "pkg" / "matters"
    residual.mkdir(parents=True)
    (residual / "old.md").write_text("残留", encoding="utf-8")

    project = tmp_path / "proj"
    _make_source(project)

    result = ph.sync_prismclaw_home(project)

    assert result.ok, f"failed={result.failed} drifted={result.drifted}"
    assert not (home / ".prismclaw" / "assets" / "experts").exists()


def test_sync_replaces_file_content_regardless_of_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同尺寸、同 mtime 的文件也必须被重新写入（曾因跳过写盘导致启动失败）。"""
    home = _setup_home(tmp_path, monkeypatch)
    target_dir = home / ".prismclaw" / "assets" / "skills" / "s"
    target_dir.mkdir(parents=True)
    target = target_dir / "SKILL.md"
    target.write_text("abcd", encoding="utf-8")

    project = tmp_path / "proj"
    source = project / "backend" / ".prismclaw"
    (source / "skills" / "s").mkdir(parents=True)
    (source / "skills" / "s" / "SKILL.md").write_text("abce", encoding="utf-8")
    stamp = 1_700_000_000
    os.utime(target, (stamp, stamp))
    os.utime(source / "skills" / "s" / "SKILL.md", (stamp, stamp))

    result = ph.sync_prismclaw_home(project)

    assert result.ok, f"failed={result.failed} drifted={result.drifted}"
    assert target.read_text(encoding="utf-8") == "abce"


def test_sync_drops_file_removed_from_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """repo 里删掉的文件，下次启动后目标侧也必须消失。"""
    home = _setup_home(tmp_path, monkeypatch)
    agents = home / ".prismclaw" / "assets" / "agents"
    agents.mkdir(parents=True)
    (agents / "removed.md").write_text("旧文件", encoding="utf-8")

    project = tmp_path / "proj"
    _make_source(project)

    result = ph.sync_prismclaw_home(project)

    assert result.ok, f"failed={result.failed} drifted={result.drifted}"
    assert not (agents / "removed.md").exists()
    assert (agents / "a.md").read_text(encoding="utf-8") == "新版内容"


def test_missing_source_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """源目录缺失必须抛错，不能建空 assets 静默启动。

    历史故障：镜像漏打 backend/.prismclaw 时，sync 建了空 assets/
    后正常返回，技能/专家包/子代理全部为零但服务显示健康。
    """
    home = _setup_home(tmp_path, monkeypatch)
    project = tmp_path / "proj"
    project.mkdir()

    try:
        ph.sync_prismclaw_home(project)
    except RuntimeError as exc:
        assert "静态能力源目录不存在" in str(exc)
    else:
        raise AssertionError("源缺失时必须抛 RuntimeError")
    assert not (home / ".prismclaw" / "assets").exists()
