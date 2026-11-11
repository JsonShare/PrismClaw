"""启动时把项目内的 ``backend/.prismclaw`` 同步到 ``~/.prismclaw/assets``。

这条链路存在的根因是：SKILL.md / PRISMCLAW.md / agent definition 里写的
全是 home-relative 路径（LLM 和人类写文档天然偏向 ``~`` 前缀），但 repo 里的
权威文件放在 ``backend/.prismclaw/``（项目内、可随代码迭代）。两条路径
体系并存，runtime / profile_loader / SKILL.md 各自读不同位置 → 永远对不上。

启动时同步后，**所有运行时代码和 SKILL.md 文档统一只认 ``~/.prismclaw/assets``**。

同步策略（谨慎）：

只同步 **静态能力目录** —— skills / plugins / agents / IDENTITY / PREFERENCES
这些由 repo 管控、没有运行时写入的内容。运行时数据（workspaces / runtime /
memory / cache / plugins-data / data / server.yaml / .work/ 等）留在 home 根，
sync 绝不触碰。

旧位置清理（零存量兼容）：首次启动迁 assets/ 前，rmentree home 根裸的 skills/
plugins/agents/IDENTITY.md/PREFERENCES.md/.gitignore（项目未上线，全部可牺牲）。

幂等：多次启动 sync 结果相同。旧位置清理只跑一次（清理 marker 存在则跳过）。
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# 静态能力目录名 —— backend/.prismclaw 下的 repo 管控内容
# 运行时数据目录（workspaces / runtime / memory / cache / plugins-data / data / .work/）
# 永远不在这里，也不应该在 backend/.prismclaw 里出现。
_STATIC_TOP_LEVEL: tuple[str, ...] = (
    "skills",
    "plugins",
    "agents",
    "IDENTITY.md",
    "PREFERENCES.md",
    ".gitignore",
)

_MIGRATION_MARKER = ".assets-migrated-v1"
_WORKSPACE_CLEANUP_MARKER = ".prism-cleanup-done"

# sync 时排除的运行时产物 —— dev 开发目录里可能混入 engine 运行痕迹，
# 绝不该随静态能力同步到 ~/.prismclaw/assets。
#   .bash_state            bash 持久会话恢复钩子（含 dev 绝对路径）
#   .work/                 workspace 系统目录（tmp/runtime_jobs/bash_state 等）
#   __pycache__/           Python 编译缓存
#   *.pyc                  Python 编译产物
_RUNTIME_IGNORE = shutil.ignore_patterns(
    ".bash_state",
    ".work",
    "__pycache__",
    "*.pyc",
)


def _resolve_backend_prismclaw(project_root: Path) -> Path:
    candidate = project_root / "backend" / ".prismclaw"
    if candidate.is_dir():
        return candidate
    # 容器/单目录部署时，backend/ 前缀可能不存在
    fallback = project_root / ".prismclaw"
    if fallback.is_dir():
        return fallback
    return candidate


def _cleanup_legacy_top_level(home: Path) -> None:
    """清理 T0 前的旧结构：~/.prismclaw 根裸的 skills/plugins/agents/... 条目。

    一次性。cleanup_stale_workspace_dirs 之外的独立清理职责。项目未上线，
    零兼容负担——直接 rmtree，不留 marker 之外的痕迹。
    """
    marker = home / _MIGRATION_MARKER
    if marker.exists():
        return

    removed: list[str] = []
    for name in _STATIC_TOP_LEVEL:
        entry = home / name
        if entry.exists():
            try:
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
                removed.append(name)
            except Exception:
                logger.warning(
                    "cleanup_legacy: 删 %s 失败", entry, exc_info=True,
                )

    # 写 marker，幂等
    try:
        marker.touch()
    except Exception:
        logger.debug("cleanup_legacy: 写 marker 失败", exc_info=True)

    if removed:
        logger.info(
            "cleanup_legacy_top_level: 删除旧位置条目 %s（首次迁移）", removed
        )


def sync_prismclaw_home(project_root: Path) -> Path:
    """把 backend/.prismclaw 里的静态能力目录同步到 ~/.prismclaw/assets。

    每启动调用一次。删除目标子树然后重拷——因为这些目录完全由 repo 管控，
    没有运行时写入，重拷是幂等且正确的。

    返回目标目录 Path（``~/.prismclaw/assets``）。
    """
    source = _resolve_backend_prismclaw(project_root)
    home = Path.home() / ".prismclaw"

    # 首次迁移：删 home 根旧位置
    _cleanup_legacy_top_level(home)

    target = home / "assets"

    if not source.is_dir():
        logger.warning(
            "sync_prismclaw_home: source %s 不存在，跳过（首次部署还没 init）",
            source,
        )
        target.mkdir(parents=True, exist_ok=True)
        return target

    target.mkdir(parents=True, exist_ok=True)

    synced = 0
    for name in _STATIC_TOP_LEVEL:
        src_entry = source / name
        if not src_entry.exists():
            continue

        dst_entry = target / name

        if src_entry.is_dir():
            # 目录：删 → 拷。repo 管控，没有运行时写入。
            if dst_entry.exists():
                try:
                    shutil.rmtree(dst_entry)
                except Exception:
                    logger.debug("sync: 删旧目录失败 %s", dst_entry, exc_info=True)
                    continue
            try:
                shutil.copytree(src_entry, dst_entry, symlinks=False, ignore=_RUNTIME_IGNORE)
                synced += 1
            except Exception:
                logger.exception("sync: 拷目录失败 %s → %s", src_entry, dst_entry)
        else:
            # 单文件：直接覆盖
            try:
                shutil.copy2(src_entry, dst_entry)
                synced += 1
            except Exception:
                logger.exception("sync: 拷文件失败 %s → %s", src_entry, dst_entry)

    logger.info(
        "sync_prismclaw_home: source=%s → target=%s, entries_synced=%d",
        source, target, synced,
    )
    return target


# workspace 根的旧目录名（已迁入 sessions/<sid>/ 或 inputs/，删除以保持干净）
# T2 后：这些都是通用名，用户合法项目目录也可能叫这些名字——T2 里 cleanup 加
# 结构指纹守卫，这里保留作 T2 前的基线逻辑。
_STALE_WORKSPACE_ROOT_DIRS: frozenset[str] = frozenset({
    "uploads",
    "outputs",
    "context_blocks",
    "tool_results",
    "tmp",
})

# web_<uid>/ 用户根下错建的 7 个系统目录（openai_compat 旧实现把它们建在
# 用户根而非 workspace 根）。这些名字是保留名，不可作为 workspace id。
_STALE_USER_ROOT_DIRS: tuple[str, ...] = (
    ".envs",
    "node_modules",
    "skills",
    "sandbox",
    "tmp",
    "cache",
)

# 具名残留：精确路径、用户已确认可删（不做通用名匹配）。
_NAMED_LEGACY_PATHS: tuple[str, ...] = (
    "web_admin/default/rust_inhibitor_mining",
)


def cleanup_stale_workspace_dirs() -> None:
    """启动时一次性清理旧布局残留。

    三条机制避免误删用户合法项目目录：
      1. 具名残留按精确路径删除（``web_admin/default/rust_inhibitor_mining``）；
      2. 通用名清理加结构指纹守卫：只有已建 ``.work/`` 新结构的 workspace 才检查；
         ``web_<uid>/`` 用户根下的保留名目录（不可能作为 workspace id）直接清理；
      3. 一次性 marker（``workspaces/.prism-cleanup-done``）：清理完成后写入，
         后续启动直接跳过——``outputs/`` / ``tmp/`` 在 T2 后是合法用户目录名，
         不能每次启动都扫一遍。
    """
    ws_root = Path.home() / ".prismclaw" / "workspaces"
    if not ws_root.is_dir():
        return
    marker = ws_root / _WORKSPACE_CLEANUP_MARKER
    if marker.exists():
        return

    removed = 0

    def _rm(target: Path) -> None:
        nonlocal removed
        try:
            shutil.rmtree(target)
            removed += 1
        except Exception:
            logger.warning("cleanup_stale: 删 %s 失败", target, exc_info=True)

    # 具名残留（精确路径，用户已确认）
    for rel in _NAMED_LEGACY_PATHS:
        target = ws_root / rel
        if target.is_dir():
            _rm(target)

    for user_root in ws_root.glob("web_*"):
        if not user_root.is_dir():
            continue
        # 用户根下错建的系统目录（旧 openai_compat 路径）
        for stale in _STALE_USER_ROOT_DIRS:
            target = user_root / stale
            if target.is_dir():
                _rm(target)
        for ws_dir in user_root.iterdir():
            if not ws_dir.is_dir():
                continue
            # 指纹守卫：只有已建 .work/ 新结构的 workspace 才清理
            if not (ws_dir / ".work").is_dir():
                continue
            for stale in _STALE_WORKSPACE_ROOT_DIRS:
                target = ws_dir / stale
                if target.is_dir():
                    _rm(target)

    if removed:
        logger.info("cleanup_stale_workspace_dirs: 删除旧目录 %d 个", removed)
    try:
        marker.touch()
    except Exception:
        logger.debug("cleanup_stale: 写 marker 失败", exc_info=True)
