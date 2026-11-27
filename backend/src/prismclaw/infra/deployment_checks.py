"""启动自检：静态能力目录与容器 runtime 卷。

静默降级的代价见 SDD §十三：挂载缺失时服务照常启动，但技能/专家包/运行时依赖
全部为零，问题直到用户真正调用工具才暴露。这里把「看起来启动了」变成
「启动失败并指出缺什么」。

两类检查的环境适用范围**不同**，所以拆成两个函数而不是一个带开关的统一入口：

* :func:`validate_assets` —— 任何环境都执行。``backend/.prismclaw`` 是随代码
  分发的权威源，宿主机与容器都必然有；为空即说明同步链路坏了，没有合理降级。
* :func:`validate_runtime_volume` —— 仅容器内执行。宿主机允许 runtime 回落到
  ``~/.prismclaw/runtime``，此时「卷未挂载」不是错误。
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "DeploymentCheckError",
    "is_container",
    "validate_assets",
    "validate_runtime_volume",
]


class DeploymentCheckError(RuntimeError):
    """启动前置条件不满足，必须中止。"""


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


def validate_assets(assets_root: Path) -> None:
    """校验启动同步后的静态能力目录非空；任何环境都执行。

    Args:
        assets_root: 启动同步的落点（``sync_prismclaw_home`` 返回的 ``target``）

    Raises:
        DeploymentCheckError: ``skills`` 与 ``experts`` 同时为空
    """
    empty = [name for name in ("skills", "experts") if not any((assets_root / name).glob("*"))]
    if len(empty) == 2:
        raise DeploymentCheckError(
            f"静态能力目录为空：{assets_root}/skills 与 {assets_root}/experts 都没有内容。"
            "镜像可能漏拷 backend/.prismclaw，或启动同步失败——技能、专家包、子代理会全部为零。"
        )


def validate_runtime_volume(runtime_root: Path) -> None:
    """校验容器 runtime 卷已挂载且可写；宿主机直接返回。

    Raises:
        DeploymentCheckError: 容器内卷不存在或不可写
    """
    if not is_container():
        return

    if not runtime_root.is_dir():
        raise DeploymentCheckError(
            f"runtime 卷未挂载：{runtime_root} 不存在。"
            "app 与 runtime-agent 必须把同一个 prismclaw-runtime 卷挂到该路径。"
        )

    error = _probe_writable(runtime_root)
    if error is not None:
        raise DeploymentCheckError(
            f"runtime 卷不可写（{runtime_root}）：{error}。"
            "UID 不匹配时先运行 compose 的 runtime-init 服务对齐权限。"
        )
