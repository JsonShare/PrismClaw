"""能力目录路径解析的回归测试。

覆盖收敛后的单一真源：
- ``canonicalize_fs_path``：模型省略 ``assets`` 段时的纠正
- ``resolve_capability_command``：command 被误当技能目录时的回退
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismclaw.engine.toolkit.file.workspace import (
    WorkspaceBoundaryError,
    WorkspaceResolver,
    canonicalize_fs_path,
    is_remote_url,
    resolve_capability_command,
)


@pytest.fixture()
def prismclaw_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 ``Path.home()`` 指到临时目录，隔离真实 ~/.prismclaw。"""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path / ".prismclaw"


def test_canonicalize_restores_missing_assets(prismclaw_home: Path) -> None:
    actual = prismclaw_home / "assets" / "experts" / "p" / "s"
    actual.mkdir(parents=True)

    got = canonicalize_fs_path(prismclaw_home / "experts" / "p" / "s")

    assert got == actual


def test_canonicalize_leaves_existing_and_unrelated_paths(prismclaw_home: Path) -> None:
    existing = prismclaw_home / "experts" / "p"
    existing.mkdir(parents=True)

    assert canonicalize_fs_path(existing) == existing
    unrelated = prismclaw_home / "experts-data" / "x"
    assert canonicalize_fs_path(unrelated) == unrelated
    assert canonicalize_fs_path(Path("/tmp/elsewhere")) == Path("/tmp/elsewhere")


def test_resolve_within_canonicalizes_reads_but_rejects_external_writes(
    prismclaw_home: Path,
) -> None:
    """别名纠正只服务读路径；绝对路径读出工作区仅放行 assets，写入一律拒绝。"""
    workspace = prismclaw_home / "ws"
    workspace.mkdir(parents=True)
    old = str(prismclaw_home / "experts" / "p" / "x")

    assert WorkspaceResolver(None).resolve_within(workspace, old) == (
        prismclaw_home / "assets" / "experts" / "p" / "x"
    )
    # 写操作不得越出工作区：既不能写进只读的 assets，也不能写其它 home 子目录
    with pytest.raises(WorkspaceBoundaryError, match="越出工作区边界"):
        WorkspaceResolver(None).resolve_within(workspace, old, for_write=True)


def test_resolve_capability_command_falls_back_to_command(prismclaw_home: Path) -> None:
    expert = prismclaw_home / "assets" / "experts" / "p"
    (expert / "commands").mkdir(parents=True)
    command = expert / "commands" / "ai-inventory.md"
    command.write_text("# ai-inventory", encoding="utf-8")

    assert resolve_capability_command(expert / "skills" / "ai-inventory" / "SKILL.md") == command


def test_resolve_capability_command_ignores_other_shapes(prismclaw_home: Path) -> None:
    expert = prismclaw_home / "assets" / "experts" / "p"
    (expert / "commands").mkdir(parents=True)
    (expert / "commands" / "x.md").write_text("x", encoding="utf-8")

    # 非 SKILL.md 形状、已存在的真实技能、无对应 command 均不触发回退
    assert resolve_capability_command(expert / "skills" / "x" / "README.md") is None
    assert resolve_capability_command(expert / "commands" / "x.md") is None
    assert resolve_capability_command(expert / "skills" / "missing" / "SKILL.md") is None


def test_resolve_capability_command_ignores_workspace_shapes(tmp_path: Path) -> None:
    """能力目录之外的同形路径不得回退（避免误读用户项目文件）。"""
    expert = tmp_path / "package"
    (expert / "commands").mkdir(parents=True)
    (expert / "commands" / "x.md").write_text("x", encoding="utf-8")

    assert resolve_capability_command(expert / "skills" / "x" / "SKILL.md") is None


def test_is_remote_url_distinguishes_urls_from_paths() -> None:
    assert is_remote_url("https://pmc.ncbi.nlm.nih.gov/articles/PMC11023754")
    assert is_remote_url("file:///tmp/a")
    assert is_remote_url("git+ssh://host/repo")
    assert not is_remote_url("C:/Users/x")
    assert not is_remote_url("outbox/a.md")
    assert not is_remote_url("/tmp/a")


def test_resolve_within_rejects_remote_url(prismclaw_home: Path) -> None:
    workspace = prismclaw_home / "ws"
    workspace.mkdir(parents=True)

    with pytest.raises(WorkspaceBoundaryError, match="是远程 URL"):
        WorkspaceResolver(None).resolve_within(
            workspace, "https://www.mdpi.com/2072-6694/17/13/2191"
        )
