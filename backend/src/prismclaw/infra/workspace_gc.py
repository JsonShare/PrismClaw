"""可重算工作区产物的 TTL 回收。

``.work`` 下有多类“只写不删”的中间产物：OCR 缓存、文档图片抽取缓存、
预览、输出溢出片。它们都能从源数据重算，留着只会单调增长。本模块按
mtime 扫一遍并删除超过窗口的条目，空目录一并收掉。

不处理的两类：

- ``.work/cache/projects/``：技能项目目录可能仍在被使用，由技能侧管理；
- ``.work/audit/``：由 ``engine/review/lifecycle.py`` 的 blob GC 按引用关系处理。

会话目录（``sessions/<sid>/``）也会被扫：OCR 缓存、抽取出的文档图片
（``context_blocks/images/``）都跟着会话走，这里按同样的 mtime 窗口兜底回收；
正常删除会话时整目录已由 ``DELETE /sessions/{id}`` 收掉。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "EPHEMERAL_SUBDIRS",
    "SESSION_EPHEMERAL_SUBDIRS",
    "SweepReport",
    "sweep_base",
    "sweep_workspace",
]

# 相对工作区根的“可重算”目录，与 SDD 5.2.9 第八节一致。
EPHEMERAL_SUBDIRS: tuple[tuple[str, ...], ...] = (
    ("cache", "ocr"),
    ("cache", "extracted"),
    ("cache", "previews"),
    ("cache", "doc"),
    ("run", "spills"),
)

# 会话目录下额外多一类：文档抽取出的图片（模型通过 context_blocks/ 前缀读取）。
SESSION_EPHEMERAL_SUBDIRS: tuple[tuple[str, ...], ...] = EPHEMERAL_SUBDIRS + (
    ("context_blocks", "images"),
)


@dataclass(slots=True)
class SweepReport:
    """一轮 TTL 回收的统计。"""

    scanned: int = 0
    removed_files: int = 0
    removed_dirs: int = 0
    freed_bytes: int = 0
    failed: int = 0


def _prune(root: Path, cutoff: float, report: SweepReport, *, dry_run: bool) -> None:
    """递归删除 ``root`` 下 mtime 早于 ``cutoff`` 的条目，并收掉空目录。"""
    try:
        entries = list(os.scandir(root))
    except OSError:
        report.failed += 1
        return

    for entry in entries:
        report.scanned += 1
        try:
            if entry.is_symlink() or entry.is_file(follow_symlinks=False):
                if entry.stat(follow_symlinks=False).st_mtime >= cutoff:
                    continue
                size = entry.stat(follow_symlinks=False).st_size
                if not dry_run:
                    os.unlink(entry.path)
                report.removed_files += 1
                report.freed_bytes += size
                continue

            if entry.is_dir(follow_symlinks=False):
                sub = Path(entry.path)
                _prune(sub, cutoff, report, dry_run=dry_run)
                # rmdir 只在空目录成功，因此“还有未过期内容”的情况自动保留。
                if not dry_run:
                    try:
                        os.rmdir(sub)
                        report.removed_dirs += 1
                    except OSError:
                        pass
        except OSError:
            report.failed += 1


def sweep_workspace(
    workspace: Path,
    *,
    older_than_days: int = 7,
    dry_run: bool = False,
) -> SweepReport:
    """回收单个工作区下（含各会话目录）的可重算产物。"""
    report = SweepReport()
    cutoff = time.time() - older_than_days * 86400
    for parts in EPHEMERAL_SUBDIRS:
        root = workspace / ".work" / Path(*parts)
        if root.is_dir():
            _prune(root, cutoff, report, dry_run=dry_run)

    sessions_root = workspace / "sessions"
    if sessions_root.is_dir():
        for session in sessions_root.iterdir():
            if not session.is_dir():
                continue
            for parts in SESSION_EPHEMERAL_SUBDIRS:
                # context_blocks/ 直接在会话根下；其余在 .work/ 下。
                base = session if parts[0] == "context_blocks" else session / ".work"
                root = base / Path(*parts)
                if root.is_dir():
                    _prune(root, cutoff, report, dry_run=dry_run)
    return report


def sweep_base(
    workspace_base: Path,
    *,
    older_than_days: int = 7,
    dry_run: bool = False,
) -> SweepReport:
    """对 ``<base>/web_<user_id>/<workspace_id>/`` 逐个工作区回收并汇总。

    单个工作区失败只累加 ``failed``，不中断整轮。
    """
    aggregate = SweepReport()
    if not workspace_base.is_dir():
        return aggregate

    for workspace in sorted(workspace_base.glob("web_*/*")):
        if not workspace.is_dir():
            continue
        part = sweep_workspace(workspace, older_than_days=older_than_days, dry_run=dry_run)
        aggregate.scanned += part.scanned
        aggregate.removed_files += part.removed_files
        aggregate.removed_dirs += part.removed_dirs
        aggregate.freed_bytes += part.freed_bytes
        aggregate.failed += part.failed
    return aggregate
