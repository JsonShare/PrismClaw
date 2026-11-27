"""Hands stdio to the vendored upstream server, reading the clone named by ``CLM_GLOBAL_LAW_ROOT``.

Upstream: https://github.com/lexifina/global-law (CC0). ``vendor/global-law/tools/`` holds
``mcp_server.py``, ``law.py``, ``usc_cite.py`` and ``freshness.py`` verbatim, except ``law.ROOT``
in ``law.py`` reads ``GLOBAL_LAW_ROOT`` so the server can live in this package.

The upstream server speaks MCP over stdio itself: this module never writes to stdout.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

VENDOR_TOOLS = Path(__file__).resolve().parents[2] / "vendor" / "global-law" / "tools"

_CLONE_HINT = (
    "语料布局（两者都要保留 .git，历史与按日期读取都依赖它）：\n"
    "  ~/.prismclaw/database/global-law   # git clone --depth 1 https://github.com/lexifina/global-law\n"
    "  ~/.prismclaw/database/china-law    # git clone https://github.com/lexifina/china-law\n"
    "启动时会自动把 global-law/china/national/acts 链到 china-law（已有子模块则不动）。\n"
    "可用 CLM_GLOBAL_LAW_ROOT / CLM_ACTS_ROOT 改这两个位置。"
)


def ensure_acts(root: Path, acts_root: Path | None) -> str | None:
    """Point ``root/china/national/acts`` at a separately kept clone, when it is not there.

    Upstream reads a corpus's provisions with git run *inside* the corpus repo, so a symlink to
    another clone works and keeps ``git pull`` on that clone the one way to update the law. A
    submodule already checked out at that path is left alone; the empty folder a plain ``git
    clone`` leaves for an uninitialised submodule is replaced.

    Returns how to make the link by hand when the clone is not writable.
    """
    acts = root / "china" / "national" / "acts"
    manual = f"ln -s {acts_root} {acts}"
    if (acts / ".git").exists():
        return None
    if acts_root is None or not (acts_root / ".git").exists():
        return None
    try:
        if acts.is_symlink():
            acts.unlink()
        elif acts.is_dir():
            if any(acts.iterdir()):
                return None
            acts.rmdir()
        acts.parent.mkdir(parents=True, exist_ok=True)
        acts.symlink_to(acts_root, target_is_directory=True)
    except OSError as exc:
        print(f"china-law-mcp: 无法建立软链（{exc}），请手动执行：\n  {manual}", file=sys.stderr)
        return manual
    print(f"china-law-mcp: 已把 {acts} 指向 {acts_root}", file=sys.stderr)
    return None


def tree_ready(root: Path) -> bool:
    """Whether ``root`` is a global-law clone with China's acts checked out.

    ``acts/.git`` is what the upstream server itself tests before it will read a corpus,
    so an interrupted ``submodule update`` is reported here rather than deep inside it.
    """
    acts = root / "china" / "national" / "acts"
    return (root / "manifest.json").is_file() and (acts / ".git").exists()


def launch(root: Path, jurisdictions: str = "china", acts_root: Path | None = None) -> None:
    if not (root / "manifest.json").is_file():
        print(f"china-law-mcp: 没有可用的 global-law 克隆：{root}", file=sys.stderr)
        print(_CLONE_HINT, file=sys.stderr)
        raise SystemExit(2)

    ensure_acts(root, acts_root)

    if not tree_ready(root):
        print(
            f"china-law-mcp: {root} 里没有可读的中国法语料（china/national/acts）",
            file=sys.stderr,
        )
        print(_CLONE_HINT, file=sys.stderr)
        raise SystemExit(2)
    os.environ["GLOBAL_LAW_ROOT"] = str(root)
    if jurisdictions:
        os.environ["GLOBAL_LAW_JURISDICTIONS"] = jurisdictions
    sys.path.insert(0, str(VENDOR_TOOLS))

    import mcp_server  # noqa: PLC0415 - imported once GLOBAL_LAW_ROOT is in the environment

    mcp_server.main()
