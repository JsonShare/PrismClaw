from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from china_law_mcp.official import VENDOR_TOOLS, ensure_acts, launch, tree_ready


@pytest.mark.parametrize("name", ["mcp_server.py", "law.py", "usc_cite.py", "freshness.py"])
def test_vendor_holds_every_module_mcp_server_imports(name: str) -> None:
    assert (VENDOR_TOOLS / name).is_file(), f"missing {name}: the server fails on first use"


def _clone(root: Path, *, acts_git: bool = True) -> Path:
    """The layout the user keeps: a manifest at the root, the acts repo inside china/national."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text("{}", encoding="utf-8")
    acts = root / "china" / "national" / "acts"
    if acts_git:
        acts.mkdir(parents=True)
        (acts / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    return root


def test_tree_ready_needs_manifest_and_a_checked_out_acts_repo(tmp_path: Path) -> None:
    assert tree_ready(tmp_path) is False
    _clone(tmp_path, acts_git=False)
    assert tree_ready(tmp_path) is False, "an empty acts folder is not a corpus"
    acts = tmp_path / "china" / "national" / "acts"
    acts.mkdir(parents=True)
    (acts / ".git").write_text("gitdir: x\n", encoding="utf-8")
    assert tree_ready(tmp_path) is True


def test_ensure_acts_links_a_separately_kept_clone(tmp_path: Path) -> None:
    root = _clone(tmp_path / "global-law", acts_git=False)
    acts_root = tmp_path / "china-law"
    acts_root.mkdir()
    (acts_root / ".git").mkdir()

    ensure_acts(root, acts_root)

    link = root / "china" / "national" / "acts"
    assert link.is_symlink() and link.resolve() == acts_root.resolve()
    assert tree_ready(root)
    ensure_acts(root, acts_root)  # idempotent: a second call changes nothing
    assert link.is_symlink()


def test_ensure_acts_replaces_the_empty_folder_a_plain_clone_leaves(tmp_path: Path) -> None:
    root = _clone(tmp_path / "global-law", acts_git=False)
    (root / "china" / "national" / "acts").mkdir(parents=True)
    acts_root = tmp_path / "china-law"
    (acts_root / ".git").mkdir(parents=True)

    ensure_acts(root, acts_root)

    assert (root / "china" / "national" / "acts").is_symlink()
    assert tree_ready(root)


def test_ensure_acts_does_not_touch_a_folder_holding_files(tmp_path: Path) -> None:
    root = _clone(tmp_path / "global-law", acts_git=False)
    partial = root / "china" / "national" / "acts"
    partial.mkdir(parents=True)
    (partial / "第001条.md").write_text("第一条\n", encoding="utf-8")
    acts_root = tmp_path / "china-law"
    (acts_root / ".git").mkdir(parents=True)

    ensure_acts(root, acts_root)

    assert not partial.is_symlink(), "unexpected files mean someone else's checkout: leave it"


def test_ensure_acts_leaves_an_existing_checkout_alone(tmp_path: Path) -> None:
    root = _clone(tmp_path / "global-law")
    acts_root = tmp_path / "china-law"
    (acts_root / ".git").mkdir(parents=True)
    ensure_acts(root, acts_root)
    assert not (root / "china" / "national" / "acts").is_symlink()


def test_launch_explains_the_layout_when_the_manifest_is_missing(
    tmp_path: Path, capsys
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        launch(tmp_path)
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "global-law" in err and "china-law" in err


def test_vendored_server_registers_every_module_and_its_tools() -> None:
    """Catches a missing vendored module: importing the server alone would not (lazy imports)."""
    code = (
        f"import sys;sys.path.insert(0,{str(VENDOR_TOOLS)!r});"
        "import law,usc_cite,freshness,mcp_server;"
        "print(' '.join(sorted(mcp_server.HANDLERS)))"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    registered = set(done.stdout.split())
    # tools/list hides the case-law tools unless a case corpus is served: assert names, not a count
    core = {"read_law", "provision_history", "search_law", "find_provision", "compare_dates"}
    assert core <= registered


def test_vendored_law_reads_the_clone_named_by_the_environment(tmp_path: Path) -> None:
    """The one upstream change: law.ROOT is GLOBAL_LAW_ROOT when it is set."""
    code = f"import sys;sys.path.insert(0,{str(VENDOR_TOOLS)!r});import law;print(law.ROOT)"
    env = dict(os.environ, GLOBAL_LAW_ROOT=str(tmp_path))
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True
    )
    assert done.stdout.strip() == str(tmp_path.resolve())
