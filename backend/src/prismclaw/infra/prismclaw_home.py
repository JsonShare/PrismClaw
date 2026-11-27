"""启动时把项目内的 ``backend/.prismclaw`` 覆盖到 ``~/.prismclaw/assets``。

这条链路存在的根因是：SKILL.md / PLUGIN.md / agent definition 里写的
全是 home-relative 路径（LLM 和人类写文档天然偏向 ``~`` 前缀），但 repo 里的
权威文件放在 ``backend/.prismclaw/``（项目内、可随代码迭代）。两条路径
体系并存，runtime / profile_loader / SKILL.md 各自读不同位置 → 永远对不上。

启动时同步后，**所有运行时代码和 SKILL.md 文档统一只认 ``~/.prismclaw/assets``**。

同步策略：每次启动先清空 ``~/.prismclaw/assets``，再把 ``backend/.prismclaw/``
整体拷回去（排除 ``.work/``、``.bash_state``、``__pycache__``、``*.pyc`` 等
运行时产物）。

运行时数据（workspaces / runtime / memory / cache / experts-data / data /
server.yaml / .work/ 等）留在 home 根，sync 绝不触碰。

覆盖是全量替换而非增量收敛：``assets/`` 里的一切（历史残留、用户写进去的
文件、旧 mtime 副本）都会被清掉，保证运行时看到的永远是 repo 当前内容。
专家包数据必须写在 ``~/.prismclaw/experts-data/``，不要写进 ``assets/``。
"""

from __future__ import annotations

import filecmp
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# 拷贝时排除的运行时产物 —— dev 开发目录里可能混入 engine 运行痕迹，
# 绝不该随能力目录同步到 ~/.prismclaw/assets。
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


def _list_files(root: Path) -> set[str]:
    """受管文件相对路径集合；排除与 _RUNTIME_IGNORE 一致的运行时产物。"""
    if not root.is_dir():
        return set()
    return {
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and p.suffix != ".pyc"
        and p.name != ".bash_state"
    }


def _verify_tree(source: Path, target: Path) -> list[str]:
    """比对源树与目标树的文件集合与内容，返回漂移项。

    覆盖后仍然不一致说明拷贝没有真正落盘（权限、只读挂载、NFS 缓存等），
    必须让启动失败，而不是让运行时继续使用旧内容。比较内容而非 mtime：
    `copy2` 虽保留 mtime，但部分文件系统会把时间戳取整，用 mtime 判定会
    制造永久假漂移。
    """
    drifted: list[str] = []
    src_files = _list_files(source)
    dst_files = _list_files(target)
    for rel in sorted(src_files - dst_files):
        drifted.append(f"{rel} 缺失")
    for rel in sorted(dst_files - src_files):
        drifted.append(f"{rel} 多余")
    for rel in sorted(src_files & dst_files):
        if not filecmp.cmp(source / rel, target / rel, shallow=False):
            drifted.append(f"{rel} 内容不一致")
    return drifted


def _reset_dir(path: Path) -> None:
    """清空目录（保留目录本身）。非目录的同名路径一并删除。"""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def sync_prismclaw_home(project_root: Path) -> SyncResult:
    """把 ``backend/.prismclaw`` 整体覆盖到 ``~/.prismclaw/assets``。

    每启动调用一次。语义是「覆盖」：先清空 ``assets/``，再整体拷贝源目录，
    保证运行时看到的永远是 repo 当前内容，不做增量比对、不保留任何残留。

    返回 :class:`SyncResult`：``failed`` 记录拷贝失败项，``drifted`` 记录
    覆盖后仍与源不一致的文件。两者任一非空即说明**运行时不使用 repo 的
    最新内容**，调用方必须记 ERROR 并中止启动。
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
    try:
        # 每次启动先清空 assets/，再整体拷贝 backend/.prismclaw/：
        # 运行时看到的永远是 repo 当前内容，不做增量比对、不保留任何残留。
        _reset_dir(target)
        shutil.copytree(source, target, symlinks=False, ignore=_RUNTIME_IGNORE, dirs_exist_ok=True)
        result.synced = 1
    except Exception:
        logger.error("sync: 覆盖失败 %s → %s", source, target, exc_info=True)
        result.failed.append("assets")

    for name in ("IDENTITY.md", "PREFERENCES.md"):
        if not (target / name).is_file():
            logger.warning(
                "sync_prismclaw_home: 必需档案缺失 %s（身份/偏好将不会注入）",
                target / name,
            )

    # 覆盖完成后比对源/目标：拷贝"没报错"不等于"内容一致"。
    result.drifted = _verify_tree(source, target)

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
