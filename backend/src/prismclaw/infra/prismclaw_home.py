"""启动时把项目内的 ``backend/.prismclaw`` 同步到 ``~/.prismclaw/assets``。

这条链路存在的根因是：SKILL.md / PLUGIN.md / agent definition 里写的
全是 home-relative 路径（LLM 和人类写文档天然偏向 ``~`` 前缀），但 repo 里的
权威文件放在 ``backend/.prismclaw/``（项目内、可随代码迭代）。两条路径
体系并存，runtime / profile_loader / SKILL.md 各自读不同位置 → 永远对不上。

启动时同步后，**所有运行时代码和 SKILL.md 文档统一只认 ``~/.prismclaw/assets``**。

同步策略（谨慎）：

只同步 **静态能力目录** —— skills / experts / agents / IDENTITY / PREFERENCES
这些由 repo 管控、没有运行时写入的内容。运行时数据（workspaces / runtime /
memory / cache / experts-data / data / server.yaml / .work/ 等）留在 home 根，
sync 绝不触碰。

目标树里若出现 repo 之外的文件（用户或专家包写进去的数据），sync **保留它们**，
只收敛受管文件。整棵 rmtree 重拷会静默删掉这些数据。

旧位置清理（零存量兼容）：首次启动迁 assets/ 前，rmentree home 根裸的 skills/
experts/agents/IDENTITY.md/PREFERENCES.md/.gitignore（项目未上线，全部可牺牲）。

幂等：多次启动 sync 结果相同。旧位置清理只跑一次（清理 marker 存在则跳过）。
"""

from __future__ import annotations

import filecmp
import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# 静态能力目录名 —— backend/.prismclaw 下的 repo 管控内容
# 运行时数据目录（workspaces / runtime / memory / cache / experts-data / data / .work/）
# 永远不在这里，也不应该在 backend/.prismclaw 里出现。
_STATIC_TOP_LEVEL: tuple[str, ...] = (
    "skills",
    "experts",
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
    """比对源与目标的**受管文件**集合与 mtime，返回漂移项。

    只在「源里有的文件」范围内比对：目标里多出来的文件是用户/专家包数据，
    由 :func:`_prune_managed` 有意保留，不属于漂移。
    """
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
        for rel in sorted(set(src_files) & set(dst_files)):
            if abs(src_files[rel] - dst_files[rel]) > 1.0:
                drifted.append(f"{name}/{rel} mtime 未同步")
    return drifted


def _unmanaged_files(src: Path, dst: Path) -> list[str]:
    """目标树中存在、但源树中不存在的相对路径。

    这些是用户/运行时在只读能力目录里留下的文件 —— 覆盖前必须显式拦住，
    否则 ``rmtree`` 会把它们静默删掉。专家包数据本应写在 ``experts-data/``，
    这条检查是第二道防线：即使某个专家包仍按旧习惯写进 ``assets/``，
    也不会在下次启动时无声丢失。
    """
    if not dst.is_dir():
        return []
    src_files = set(_list_files(src))
    return sorted(rel for rel in _list_files(dst) if rel not in src_files)


def _manifest_path(target: Path) -> Path:
    return target / ".sync-manifest.json"


def _read_manifest(target: Path) -> set[str]:
    """读取上次同步落下的受管文件清单（相对 ``assets/`` 的路径）。"""
    path = _manifest_path(target)
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("sync: 忽略损坏的清单 %s", path)
        return set()
    files = data.get("files") if isinstance(data, dict) else None
    return set(files) if isinstance(files, list) else set()


def _write_manifest(target: Path, files: set[str]) -> None:
    path = _manifest_path(target)
    try:
        path.write_text(
            json.dumps({"files": sorted(files)}, ensure_ascii=False, indent=0),
            encoding="utf-8",
        )
    except OSError:
        logger.error("sync: 写清单失败 %s", path, exc_info=True)


def _prune_managed(src: Path, dst: Path, previous: set[str], prefix: str) -> set[str]:
    """把 ``dst`` 收敛为与 ``src`` 一致，返回本次的受管文件清单。

    删除依据是**上次同步的清单**而非「源里没有」：
      * 上次在清单里、这次不在源里 → repo 已移除 → 删除；
      * 从未出现在清单里 → 用户/专家包写入的数据 → 原地保留。

    没有清单时（首次同步）不做任何删除，宁可留下旧文件也不误删用户数据。
    """
    if not dst.is_dir():
        shutil.copytree(src, dst, symlinks=False, ignore=_RUNTIME_IGNORE)
        return {f"{prefix}/{rel}" for rel in _list_files(src)}

    managed = _list_files(src)
    for rel in sorted(set(previous) - set(managed)):
        stale = dst / rel.removeprefix(f"{prefix}/")
        try:
            if stale.is_file():
                stale.unlink()
        except OSError:
            logger.error("sync: 删除旧文件失败 %s", stale, exc_info=True)
    for rel in managed:
        src_file, dst_file = src / rel, dst / rel
        try:
            # 内容相同时跳过写盘。不能用 size+mtime 近似：repo 里同长度
            # 改一行（如改个阈值）会被误判为已同步，改动静默失效。
            if dst_file.is_file() and filecmp.cmp(src_file, dst_file, shallow=False):
                continue
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst_file)
        except OSError:
            logger.error("sync: 同步文件失败 %s", dst_file, exc_info=True)
    return {f"{prefix}/{rel}" for rel in managed}


def sync_prismclaw_home(project_root: Path) -> SyncResult:
    """把 backend/.prismclaw 里的静态能力目录同步到 ~/.prismclaw/assets。

    每启动调用一次。语义是「收敛」而非「重拷」：repo 管控的文件以源为准
    （同名覆盖、源已删除的旧文件清掉），**非 repo 管控的文件原地保留**。
    后者是用户/专家包数据，整棵删除会让它们静默丢失。

    返回 :class:`SyncResult`：``failed`` 记录同步失败项，``drifted`` 记录
    副本后仍与源不一致的受管文件。两者任一非空即说明**运行时不使用 repo
    的最新内容**，调用方必须记 ERROR（旧实现只写 debug 日志，导致改动
    静默失效）。
    """
    source = _resolve_backend_prismclaw(project_root)
    home = Path.home() / ".prismclaw"

    target = home / "assets"

    if not source.is_dir():
        # 源缺失时**不能**建空 assets 继续跑：技能/专家包/子代理会全部为零，
        # 但启动成功、UI 无报错，故障只在用户点开具体功能时才暴露。
        raise RuntimeError(
            f"静态能力源目录不存在：{source}。"
            f"镜像必须包含 backend/.prismclaw（见 deploy/Dockerfile），"
            f"否则技能/专家包/子代理全部不可用。"
        )

    target.mkdir(parents=True, exist_ok=True)

    result = SyncResult(target=target)
    previous = _read_manifest(target)
    current: set[str] = set()
    for name in _STATIC_TOP_LEVEL:
        src_entry = source / name
        if not src_entry.exists():
            continue

        dst_entry = target / name

        if src_entry.is_dir():
            # 目录：只收敛 repo 管控的文件，未受管文件（用户/专家包数据）原地保留。
            if dst_entry.exists() and not dst_entry.is_dir():
                # 目标被同名文件占位 → 视为冲突，记录失败，不擅自删除
                logger.error("sync: 目标 %s 是文件而非目录", dst_entry)
                result.failed.append(name)
                continue
            unmanaged = _unmanaged_files(src_entry, dst_entry)
            if unmanaged:
                logger.warning(
                    "sync: %s 下有 %d 个非 repo 管控文件（用户/专家包数据），将保留不删：%s",
                    name,
                    len(unmanaged),
                    unmanaged[:5],
                )
            try:
                current |= _prune_managed(src_entry, dst_entry, previous, name)
                result.synced += 1
            except Exception:
                logger.error("sync: 同步目录失败 %s → %s", src_entry, dst_entry, exc_info=True)
                result.failed.append(name)
        else:
            # 单文件：直接覆盖
            try:
                shutil.copy2(src_entry, dst_entry)
                current.add(name)
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
    if not result.failed:
        _write_manifest(target, current)

    logger.info(
        "sync_prismclaw_home: source=%s → target=%s, entries_synced=%d, failed=%d, drifted=%d",
        source,
        target,
        result.synced,
        len(result.failed),
        len(result.drifted),
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
    print(f"target={_res.target}\nsynced={_res.synced} failed={_res.failed} drifted={_res.drifted}")
    raise SystemExit(0 if _res.ok else 1)
