"""P4-OPS-02：Firecracker microVM Backend — 骨架实现。

每个工作区对应一个 Firecracker microVM，通过 jailer 隔离运行。

设计：
  - 每个 workspace = 一个 Firecracker microVM（jailer + rootfs template clone）
  - jailer 提供 uid/gid 隔离 + chroot + seccomp
  - rootfs 从模板目录 clone（Copy-on-Write 或完整拷贝）
  - VMM 进程通过 firecracker --config-file 启动
  - endpoint_url = "http://vm-{id}:9001"

当前为骨架实现，仅支持 InMemory 存储，不调用 Firecracker / jailer 二进制。
真实 Firecracker API 接入在后续 Phase 中完成。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from prismclaw.engine.security.gate.policies import WorkspaceTag
from prismclaw.engine.runtime.resource_tiers import get_resource_tier

logger = logging.getLogger(__name__)


class FirecrackerBackend:
    """Firecracker microVM 后端 — P4-OPS-02。

    每个工作区 = 一个 Firecracker microVM：
      - jailer 提供 uid/gid 隔离 + chroot + seccomp
      - rootfs 从模板目录 clone
      - VMM 进程通过 firecracker --config-file 启动
      - endpoint_url = "http://vm-{id}:9001"

    当前为骨架实现，仅支持 InMemory 存储，不调用 Firecracker / jailer 二进制。
    真实 Firecracker API 接入在后续 Phase 中完成。
    """

    _FC_BINARY: str = "/usr/bin/firecracker"
    _JAILER_BINARY: str = "/usr/bin/jailer"

    def __init__(
        self,
        *,
        jailer_binary: str = "/usr/bin/jailer",
        rootfs_template_dir: str = "/var/lib/prismclaw/rootfs-templates",
        kernel_image_path: str = "/var/lib/prismclaw/vmlinux",
        jailer_uid: int = 7000,
        jailer_gid: int = 7000,
    ) -> None:
        self._jailer_binary = jailer_binary
        self._rootfs_template_dir = rootfs_template_dir
        self._kernel_image_path = kernel_image_path
        self._jailer_uid = jailer_uid
        self._jailer_gid = jailer_gid
        # workspace_id → WorkspaceContainer
        self._containers: dict[str, Any] = {}
        # workspace_id → VMM process handle (skeleton: pid or None)
        self._vmm_processes: dict[str, int | None] = {}

    def create_workspace(
        self,
        workspace_id: str,
        tag: WorkspaceTag,
        *,
        hmac_secret: str = "dev-secret",
        hmac_key_version: str = "1",
    ) -> Any:
        """创建工作区（Firecracker microVM）— 骨架实现。

        真实实现流程：
          1. 生成 VM ID（uuid4）
          2. 配置 jailer（uid/gid 隔离、chroot 目录、seccomp 级别）
          3. 从 rootfs_template_dir clone rootfs 到 /srv/jailer/firecracker/{vm_id}/rootfs
          4. 生成 firecracker config（boot-source、drives、network、machine-config）
          5. 通过 jailer 启动 VMM：jailer --id {vm_id} --exec-file /usr/bin/firecracker
             --uid {jailer_uid} --gid {jailer_gid} -- --config-file /path/to/config.json
          6. 返回 WorkspaceContainer(endpoint_url="http://vm-{id}:9001")
        """
        # 延迟导入避免循环依赖（同 SwarmBackend / K8sJobBackend）
        from prismclaw.engine.runtime.container_manager import WorkspaceContainer

        if workspace_id in self._containers:
            return self._containers[workspace_id]

        resource = get_resource_tier(tag.template_id)
        vm_id = uuid.uuid4().hex[:12]
        service_name = f"vm-{vm_id}"

        # ── 骨架：仅记录日志，不调用 Firecracker 二进制 ──
        logger.warning(
            "FirecrackerBackend: SKEL — would configure jailer "
            "(uid=%d, gid=%d) for vm_id=%s",
            self._jailer_uid, self._jailer_gid, vm_id,
        )
        logger.warning(
            "FirecrackerBackend: SKEL — would clone rootfs from %s "
            "for workspace %s",
            self._rootfs_template_dir, workspace_id,
        )
        logger.warning(
            "FirecrackerBackend: SKEL — would start VMM via "
            "%s --config-file with kernel=%s",
            self._JAILER_BINARY, self._kernel_image_path,
        )

        container = WorkspaceContainer(
            workspace_id=workspace_id,
            service_name=service_name,
            tag=tag,
            endpoint_url=f"http://vm-{vm_id}:9001",
            status="created",
        )
        self._containers[workspace_id] = container
        self._vmm_processes[workspace_id] = None

        logger.info(
            "FirecrackerBackend: created workspace %s (skeleton, vm_id=%s, template=%s, tier=%s)",
            workspace_id, vm_id, tag.template_id, resource.sandbox_tier.value,
        )
        return container

    def destroy_workspace(self, workspace_id: str) -> bool:
        """销毁工作区（Firecracker microVM）— 骨架实现。

        真实实现流程：
          1. 向 VMM 进程发送 SIGTERM
          2. 等待进程退出（超时后 SIGKILL）
          3. 清理 rootfs clone 目录
          4. 清理 jailer chroot 目录
        """
        container = self._containers.pop(workspace_id, None)
        self._vmm_processes.pop(workspace_id, None)
        if container is None:
            return False

        logger.warning(
            "FirecrackerBackend: SKEL — would send SIGTERM to VMM for "
            "workspace %s (service=%s) and clean up rootfs clone",
            workspace_id, container.service_name,
        )
        logger.info(
            "FirecrackerBackend: destroyed workspace %s (skeleton)", workspace_id,
        )
        return True

    def get_workspace(self, workspace_id: str) -> Any | None:
        """获取工作区 microVM 信息。"""
        return self._containers.get(workspace_id)

    def list_workspaces(self) -> list[Any]:
        """列出所有工作区 microVM。"""
        return list(self._containers.values())

    async def health_check(self, workspace_id: str) -> bool:
        """检查工作区 microVM 健康状态 — 骨架实现。

        真实实现流程：
          1. 检查 VMM 进程是否存活（os.kill(pid, 0)）
          2. 可选：通过 internal API GET /health 检查 VM 内部状态
        """
        container = self._containers.get(workspace_id)
        if container is None:
            return False

        # 骨架：仅检查内存状态
        vmm_pid = self._vmm_processes.get(workspace_id)
        if vmm_pid is not None:
            # 真实实现会 os.kill(vmm_pid, 0)
            logger.warning(
                "FirecrackerBackend: SKEL — would check VMM process %d "
                "for workspace %s",
                vmm_pid, workspace_id,
            )

        return container.status == "created"
