"""P1-RUN-06：RESOURCE_TIERS 资源配置。

每个 template_id 对应不同的容器资源配额和 SandboxTier 映射：
  - default-v1:    默认场景，STANDARD 隔离，轻量资源（基础设施，不可删除）
  - restricted-v1: 受限场景，STRICT 隔离，严格资源 + WORM 审计（基础设施，fail-closed 默认）

域特定模板通过 register_resource_tier() 注册，由对应 DomainBundle
在 on_build_runtime() 中完成。

设计原则：
  - 每个 tier 映射到具体的 Docker Swarm 资源限制
  - SandboxTier 通过 BwrapPolicy 在 Runtime-Agent 内生效
  - 资源配额防止恶意/异常工具耗尽宿主机资源
"""
from __future__ import annotations

from dataclasses import dataclass

from prismclaw.schema.content.workspace import SandboxTier


@dataclass(frozen=True)
class ResourceTier:
    """单个资源档位配置。"""
    name: str
    sandbox_tier: SandboxTier

    # Docker Swarm 资源限制
    cpu_limit: str          # e.g. "0.5", "1.0", "2.0"
    memory_limit: str       # e.g. "512m", "1g", "2g"
    cpu_reservation: str    # e.g. "0.25", "0.5"
    memory_reservation: str # e.g. "256m", "512m"

    # 模板标识
    template_id: str = ""

    # 文件系统限制 (BwrapPolicy 映射)
    allow_read_workspace: bool = True
    allow_read_user_safe: bool = True    # ~/.prismclaw 等
    allow_write_workspace: bool = True
    allow_net: bool = True               # True = 域名白名单, False = 完全禁止
    deny_pid_namespace: bool = False     # True = 进程命名空间隔离

    # 审计增强
    worm_audit: bool = False             # Write-Once-Read-Many 审计日志
    four_eyes: bool = False              # 双人确认

    # 预热池配置
    prewarm_count: int = 1               # 常驻预热容器数量
    prewarm_idle_timeout_seconds: int = 300  # 空闲容器超时


# ── 基础设施资源配置（不可删除，fail-closed 依赖） ────────────────────────

RESOURCE_TIERS: dict[str, ResourceTier] = {
    "default-v1": ResourceTier(
        name="default",
        sandbox_tier=SandboxTier.STANDARD,
        template_id="default-v1",
        cpu_limit="1.0",
        memory_limit="1g",
        cpu_reservation="0.25",
        memory_reservation="256m",
        allow_read_workspace=True,
        allow_read_user_safe=True,
        allow_write_workspace=True,
        allow_net=True,
        deny_pid_namespace=False,
        worm_audit=False,
        four_eyes=False,
        prewarm_count=2,
        prewarm_idle_timeout_seconds=600,
    ),

    "restricted-v1": ResourceTier(
        name="restricted",
        sandbox_tier=SandboxTier.STRICT,
        template_id="restricted-v1",
        cpu_limit="2.0",
        memory_limit="2g",
        cpu_reservation="0.5",
        memory_reservation="512m",
        allow_read_workspace=True,
        allow_read_user_safe=False,   # STRICT: 禁止读工作区外
        allow_write_workspace=True,
        allow_net=False,              # STRICT: 完全禁止网络
        deny_pid_namespace=True,      # STRICT: 进程命名空间隔离
        worm_audit=True,
        four_eyes=True,               # 受限场景：双人确认
        prewarm_count=1,
        prewarm_idle_timeout_seconds=180,
    ),
}

# fail-closed 默认：未知 template_id 回退到 restricted-v1
_FAIL_CLOSED_TIER_KEY = "restricted-v1"


def register_resource_tier(template_id: str, tier: ResourceTier) -> None:
    """注册域特定资源配置。由 DomainBundle 在 on_build_runtime() 中调用。

    不允许覆盖基础设施模板（default-v1 / restricted-v1）。
    """
    if template_id in ("default-v1", "restricted-v1"):
        raise ValueError(f"Cannot override infrastructure tier: {template_id}")
    RESOURCE_TIERS[template_id] = tier


def get_resource_tier(template_id: str) -> ResourceTier:
    """获取工作区模板的资源配置。未知 template_id fail-closed 到 restricted-v1。"""
    return RESOURCE_TIERS.get(template_id, RESOURCE_TIERS[_FAIL_CLOSED_TIER_KEY])
