"""启动时把项目内的 ``backend/.prismclaw`` 同步到 ``~/.prismclaw/assets``。

这条链路存在的根因是：SKILL.md / PLUGIN.md / agent definition 里写的
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
from dataclasses import dataclass, field
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


@dataclass(slots=True)
class SyncResult:
    """同步结果 —— 让失败与漂移不再静默。"""

    target: Path
    synced: int = 0
    failed: list[str] = field(default_factory=list)
    drifted: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed and not self.drifted


def _list_files(root: Path) -> dict[str, float]:
    """相对路径 → mtime；排除与 _RUNTIME_IGNORE 一致的运行时产物。"""
    if not root.is_dir():
        return {}
    return {
        str(p.relative_to(root)): p.stat().st_mtime
        for p in root.rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and p.suffix != ".pyc"
        and p.name != ".bash_state"
    }


def _verify_synced(source: Path, target: Path, names: tuple[str, ...]) -> list[str]:
    """比对源与目标的文件集合与 mtime，返回漂移项（空表示完全一致）。"""
    drifted: list[str] = []
    for name in names:
        src = source / name
        if not src.exists():
            continue
        dst = target / name
        if src.is_file():
            if not dst.is_file() or src.stat().st_size != dst.stat().st_size:
                drifted.append(name)
            continue
        src_files = _list_files(src)
        dst_files = _list_files(dst)
        for rel in sorted(set(src_files) - set(dst_files)):
            drifted.append(f"{name}/{rel} 缺失")
        for rel in sorted(set(dst_files) - set(src_files)):
            drifted.append(f"{name}/{rel} 多余")
        for rel in sorted(set(src_files) & set(dst_files)):
            if abs(src_files[rel] - dst_files[rel]) > 1.0:
                drifted.append(f"{name}/{rel} mtime 未同步")
    return drifted


def sync_prismclaw_home(project_root: Path) -> SyncResult:
    """把 backend/.prismclaw 里的静态能力目录同步到 ~/.prismclaw/assets。

    每启动调用一次。删除目标子树然后重拷——因为这些目录完全由 repo 管控，
    没有运行时写入，重拷是幂等且正确的。

    返回 :class:`SyncResult`：``failed`` 记录拷贝失败项，``drifted`` 记录
    拷贝后源/目标仍不一致的文件。两者任一非空即说明**运行时不使用 repo
    的最新内容**，调用方必须记 ERROR（旧实现只写 debug 日志，导致改动
    静默失效）。
    """
    source = _resolve_backend_prismclaw(project_root)
    home = Path.home() / ".prismclaw"

    target = home / "assets"

    if not source.is_dir():
        logger.warning(
            "sync_prismclaw_home: source %s 不存在，跳过（首次部署还没 init）",
            source,
        )
        target.mkdir(parents=True, exist_ok=True)
        return SyncResult(target=target)

    target.mkdir(parents=True, exist_ok=True)

    result = SyncResult(target=target)
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
                    logger.error("sync: 删旧目录失败 %s", dst_entry, exc_info=True)
                    result.failed.append(name)
                    continue
            try:
                shutil.copytree(src_entry, dst_entry, symlinks=False, ignore=_RUNTIME_IGNORE)
                result.synced += 1
            except Exception:
                logger.error("sync: 拷目录失败 %s → %s", src_entry, dst_entry, exc_info=True)
                result.failed.append(name)
        else:
            # 单文件：直接覆盖
            try:
                shutil.copy2(src_entry, dst_entry)
                result.synced += 1
            except Exception:
                logger.error("sync: 拷文件失败 %s → %s", src_entry, dst_entry, exc_info=True)
                result.failed.append(name)

    for name in ("IDENTITY.md", "PREFERENCES.md"):
        if not (target / name).is_file():
            logger.warning(
                "sync_prismclaw_home: 必需档案缺失 %s（身份/偏好将不会注入）",
                target / name,
            )

    # 拷贝完成后比对源/目标：拷贝"没报错"不等于"内容一致"。
    result.drifted = _verify_synced(source, target, _STATIC_TOP_LEVEL)

    logger.info(
        "sync_prismclaw_home: source=%s → target=%s, entries_synced=%d, "
        "failed=%d, drifted=%d",
        source, target, result.synced, len(result.failed), len(result.drifted),
    )
    if result.failed:
        logger.error(
            "sync_prismclaw_home: 拷贝失败 %s —— 运行时不会使用 repo 最新内容",
            result.failed,
        )
    if result.drifted:
        logger.error(
            "sync_prismclaw_home: 同步后仍不一致 %s",
            result.drifted[:10],
        )
    return result


if __name__ == "__main__":  # pragma: no cover - 手动同步入口
    import sys

    _root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    _res = sync_prismclaw_home(_root)
    print(
        f"target={_res.target}\n"
        f"synced={_res.synced} failed={_res.failed} drifted={_res.drifted}"
    )
    raise SystemExit(0 if _res.ok else 1)
