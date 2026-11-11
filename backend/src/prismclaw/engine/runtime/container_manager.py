"""P1-RUN-03 + P4-OPS-01/P4-OPS-03：WorkspaceContainerManager — 编排层。

管理每个工作区的容器生命周期，通过 ContainerBackend Protocol 委托具体实现：
  - SwarmBackend（默认）：Docker Swarm service
  - K8sJobBackend（可选）：Kubernetes Job + PVC（通过 get_container_backend 工厂选择）

WorkspaceContainerManager 本身只做编排：
  - 校验 workspace_id 唯一性
  - 调用 backend 的生命周期方法
  - 跟踪活跃工作区列表
  - 汇总 PreWarmPool 的启动统计（record_startup / adjust_pool hook）

安全设计：
  - endpoint-mode=dnsrr: 不发布端口到 Swarm routing mesh
  - dns=[127.0.0.1]: 容器内 DNS 只解析本地，阻断 DNS 隧道
  - 每个容器挂载独立 volume
  - STRICT tier: 禁止 PID namespace 共享
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from prismclaw.engine.security.gate.policies import WorkspaceTag
from prismclaw.engine.runtime.container_backend import (
    ContainerBackend,
    get_container_backend,
)
from prismclaw.engine.runtime.prewarm_pool import PreWarmPool

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkspaceContainer:
    """工作区容器信息。"""
    workspace_id: str
    service_name: str
    tag: WorkspaceTag
    container_id: str | None = None
    endpoint_url: str | None = None
    status: str = "unknown"


class WorkspaceContainerManager:
    """工作区容器管理器（编排层）。

    P4-OPS-01 重构：不再直接调用 Docker API，
    而是通过 ContainerBackend Protocol 委托。
    backend 选择顺序（P4-OPS-01 接入点）：
      1. __init__ 显式传入 backend 参数
      2. 环境变量 PRISMCLAW_CONTAINER_BACKEND + image 参数 → get_container_backend()
      3. 默认 SwarmBackend

    P4-OPS-03 接入：可接收 PreWarmPool 实例，每次 create_workspace 时
    调用 record_startup 积累历史，调用方也可按需 adjust_pool 做伸缩。
    """

    def __init__(
        self,
        *,
        image: str = "prismclaw/runtime-agent:latest",
        backend: ContainerBackend | None = None,
        backend_name: str | None = None,
        k8s_namespace: str = "prismclaw",
        prewarm_pool: PreWarmPool | None = None,
        record_to_pool: bool = True,
    ) -> None:
        if backend is not None:
            self._backend: ContainerBackend = backend
        else:
            # P4-OPS-01：通过工厂（含 env 读取）选择 Swarm / K8sJob
            self._backend = get_container_backend(
                backend_name, image=image, k8s_namespace=k8s_namespace,
            )
        # P4-OPS-03：预热池整合（可选，未传则不统计）
        self._prewarm_pool = prewarm_pool
        self._record_to_pool = record_to_pool

    @property
    def prewarm_pool(self) -> PreWarmPool | None:
        """返回当前绑定的 PreWarmPool，便于调用方做 adjust_pool / get_metrics。"""
        return self._prewarm_pool

    def create_workspace(
        self,
        workspace_id: str,
        tag: WorkspaceTag,
        *,
        hmac_secret: str = "dev-secret",
        hmac_key_version: str = "1",
    ) -> WorkspaceContainer:
        """创建工作区容器，并记录到 PreWarmPool（如已绑定）。"""
        container = self._backend.create_workspace(
            workspace_id, tag,
            hmac_secret=hmac_secret,
            hmac_key_version=hmac_key_version,
        )
        if self._prewarm_pool is not None and self._record_to_pool:
            try:
                self._prewarm_pool.record_startup(tag.template_id)
            except Exception as e:
                logger.warning("WorkspaceContainerManager: record_startup failed: %s", e)
        return container

    def destroy_workspace(self, workspace_id: str) -> bool:
        """销毁工作区容器。"""
        return self._backend.destroy_workspace(workspace_id)

    def get_workspace(self, workspace_id: str) -> WorkspaceContainer | None:
        """获取工作区容器信息。"""
        return self._backend.get_workspace(workspace_id)

    def list_workspaces(self) -> list[WorkspaceContainer]:
        """列出所有工作区容器。"""
        return self._backend.list_workspaces()

    async def health_check(self, workspace_id: str) -> bool:
        """检查工作区容器健康状态。"""
        return await self._backend.health_check(workspace_id)

    def adjust_prewarm(self, template_id: str) -> int:
        """P4-OPS-03：针对指定 template_id 调整预热池，返回目标大小。

        未绑定 PreWarmPool 时返回 0 并写 warning。
        """
        if self._prewarm_pool is None:
            logger.warning(
                "WorkspaceContainerManager.adjust_prewarm: no PreWarmPool bound (template_id=%s)",
                template_id,
            )
            return 0
        return self._prewarm_pool.adjust_pool(template_id)
