"""容器部署的启动自检。

静默降级的代价见 SDD §十三：挂载缺失时服务照常启动，但技能/插件/运行时依赖
全部为零，问题直到用户真正调用工具才暴露。这里在启动期做一次显式检查，失败
即终止，把「看起来启动了」变成「启动失败并指出缺什么」。

只在容器内生效（检测 ``/.dockerenv`` / ``/run/.containerenv`` / ``/proc/1/cgroup``），
宿主机开发与单元测试不受影响——物理部署允许 runtime 回落到 ``~/.prismclaw/runtime``。
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["ContainerCheckError", "is_container", "validate_container_layout"]


class ContainerCheckError(RuntimeError):
    """容器内挂载或权限不符合预期，启动必须中止。"""


def is_container() -> bool:
    """是否运行在容器内。Docker / Podman / K8s 三种痕迹任一命中即可。"""
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return "docker" in cgroup or "kubepods" in cgroup


def _probe_writable(root: Path) -> str | None:
    """写一个探针文件验证目录可写；返回错误信息，成功返回 None。"""
    probe = root / f".prismclaw-startup-probe-{os.getpid()}"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        return str(exc)
    return None


def validate_container_layout(*, assets_root: Path, runtime_root: Path) -> None:
    """容器模式下校验静态能力目录与 runtime 卷，不满足即抛异常。

    Args:
        assets_root: ``~/.prismclaw/assets``（启动同步的落点）
        runtime_root: runtime 卷根（容器内固定 ``/runtime``）

    Raises:
        ContainerCheckError: 容器内 assets 为空、runtime 未挂载或不可写
    """
    if not is_container():
        return

    empty = [name for name in ("skills", "plugins") if not any((assets_root / name).glob("*"))]
    if len(empty) == 2:
        raise ContainerCheckError(
            f"静态能力目录为空：{assets_root}/skills 与 {assets_root}/plugins 都没有内容。"
            "镜像可能漏拷 backend/.prismclaw，或启动同步失败——技能、插件、子代理会全部为零。"
        )

    if not runtime_root.is_dir():
        raise ContainerCheckError(
            f"runtime 卷未挂载：{runtime_root} 不存在。"
            "app 与 runtime-agent 必须把同一个 prismclaw-runtime 卷挂到该路径。"
        )

    error = _probe_writable(runtime_root)
    if error is not None:
        raise ContainerCheckError(
            f"runtime 卷不可写（{runtime_root}）：{error}。"
            "UID 不匹配时先运行 compose 的 runtime-init 服务对齐权限。"
        )
