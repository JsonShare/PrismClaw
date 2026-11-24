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


def test_verify_ignores_unmanaged_files(tmp_path: Path) -> None:
    """目标里多出的文件是用户/插件数据，不属于漂移。"""
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    (src / "agents").mkdir(parents=True)
    (dst / "agents").mkdir(parents=True)
    (dst / "agents" / "user-notes.md").write_text("old", encoding="utf-8")

    assert ph._verify_synced(src, dst, ("agents",)) == []


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


def test_sync_preserves_user_data_in_assets(tmp_path: Path, monkeypatch) -> None:
    """插件若仍把数据写进 assets/，重启不得把它删掉。

    历史故障：sync 每次 ``rmtree`` 整棵 plugins/ 再重拷，legal 插件的
    用户事项（写在插件安装目录里）第一次重启就全丢。
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    project = tmp_path / "proj"
    source = _make_source(project)
    (source / "plugins" / "pkg" / "sub" / "matters").mkdir(parents=True)
    (source / "plugins" / "pkg" / "sub" / "matters" / "_README.md").write_text(
        "模板", encoding="utf-8"
    )

    user_matter = (
        home
        / ".prismclaw"
        / "assets"
        / "plugins"
        / "pkg"
        / "sub"
        / "matters"
        / "acme"
        / "matter.md"
    )
    user_matter.parent.mkdir(parents=True)
    user_matter.write_text("用户事项", encoding="utf-8")

    result = ph.sync_prismclaw_home(project)

    assert result.ok, f"failed={result.failed} drifted={result.drifted}"
    assert user_matter.read_text(encoding="utf-8") == "用户事项"


def test_sync_removes_file_dropped_from_repo(tmp_path: Path, monkeypatch) -> None:
    """上次同步受管、这次 repo 里已不存在 → 目标侧必须清理。

    判定依据是上次同步落下的清单，而非「源里没有就删」（那会误删用户数据）。
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    project = tmp_path / "proj"
    _make_source(project)

    agents = home / ".prismclaw" / "assets" / "agents"
    agents.mkdir(parents=True)
    (agents / "a.md").write_text("旧版内容", encoding="utf-8")
    (agents / "removed.md").write_text("旧文件", encoding="utf-8")
    # 模拟上一次同步落下的清单：两个文件当时都受 repo 管控
    (agents.parent / ".sync-manifest.json").write_text(
        '{"files": ["agents/a.md", "agents/removed.md"]}', encoding="utf-8"
    )

    result = ph.sync_prismclaw_home(project)

    assert result.ok, f"failed={result.failed} drifted={result.drifted}"
    assert not (agents / "removed.md").exists()
    assert (agents / "a.md").read_text(encoding="utf-8") == "新版内容"


def test_missing_source_raises(tmp_path: Path, monkeypatch) -> None:
    """源目录缺失必须抛错，不能建空 assets 静默启动。

    历史故障：镜像漏打 backend/.prismclaw 时，sync 建了空 assets/
    后正常返回，技能/插件/子代理全部为零但服务显示健康。
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    project = tmp_path / "proj"
    project.mkdir()

    try:
        ph.sync_prismclaw_home(project)
    except RuntimeError as exc:
        assert "静态能力源目录不存在" in str(exc)
    else:
        raise AssertionError("源缺失时必须抛 RuntimeError")
    assert not (home / ".prismclaw" / "assets").exists()
