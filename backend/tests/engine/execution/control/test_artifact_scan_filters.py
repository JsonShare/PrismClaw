"""产物扫描不得把系统/隐藏条目当成交付物。

背景：bash 曾把 cwd 追加写入 `.work/bash_state/_last_pwd`。模型在 bash 里
`cd outputs` 后，该文件落进会话交付目录，产物面板出现 `_last_pwd` /
`_LAST_PWD` 这类无效条目。
"""
from __future__ import annotations

from pathlib import Path

from prismclaw.engine.execution.control.artifacts import ArtifactScanner, Scope


def _write(path: Path, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _scan(root: Path) -> dict[str, tuple[int, int]]:
    return ArtifactScanner(root).scan(
        Scope(kind="session", root=str(root), prefixes=("outputs",))
    )


def test_scan_excludes_bash_state(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    _write(outputs / "report.md")
    _write(outputs / ".work/bash_state/_last_pwd")
    _write(outputs / "_LAST_PWD")

    found = _scan(tmp_path)

    assert "outputs/report.md" in found
    assert not any(
        "_last_pwd" in key or "_LAST_PWD" in key or ".work" in key for key in found
    )


def test_scan_excludes_hidden_and_pycache(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    _write(outputs / "data.csv")
    _write(outputs / ".hidden")
    _write(outputs / "__pycache__/mod.pyc")

    assert set(_scan(tmp_path)) == {"outputs/data.csv"}


def test_scan_keeps_normal_nested_artifacts(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    _write(outputs / "charts/a.png")
    _write(outputs / "README.md")

    assert set(_scan(tmp_path)) == {"outputs/charts/a.png", "outputs/README.md"}
