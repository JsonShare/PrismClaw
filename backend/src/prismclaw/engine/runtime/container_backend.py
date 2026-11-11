"""P4-OPS-01：ContainerBackend Protocol + SwarmBackend 提取 + K8sJobBackend 骨架。

WorkspaceContainerManager 不再直接耦合 Docker Swarm API，
而是通过 ContainerBackend Protocol 委托具体后端实现。

后端实现：
  - SwarmBackend: 现有 Docker Swarm 逻辑（从 container_manager.py 提取）
  - K8sJobBackend: Kubernetes Job + PVC + NetworkPolicy（Phase 4b 接入真实 K8s API）
  - FirecrackerBackend: Firecracker microVM（P4-OPS-02，jailer + rootfs template）

Protocol 接口：
  - create_workspace → WorkspaceContainer
  - destroy_workspace → bool
  - get_workspace → WorkspaceContainer | None
  - list_workspaces → list[WorkspaceContainer]
  - health_check → bool (async)
"""
from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from prismclaw.engine.security.gate.policies import WorkspaceTag
from prismclaw.engine.runtime.resource_tiers import get_resource_tier

logger = logging.getLogger(__name__)


# ── ContainerBackend Protocol ──────────────────────────────────────────


@runtime_checkable
class ContainerBackend(Protocol):
    """容器运行时后端协议。

    WorkspaceContainerManager 通过此协议与具体容器运行时交互，
    支持 Docker Swarm / Kubernetes / Firecracker 切换。
    """

    def create_workspace(
        self,
        workspace_id: str,
        tag: WorkspaceTag,
        *,
        hmac_secret: str = "dev-secret",
        hmac_key_version: str = "1",
    ) -> Any:
        """创建工作区容器。返回 WorkspaceContainer。"""
        ...

    def destroy_workspace(self, workspace_id: str) -> bool:
        """销毁工作区容器。"""
        ...

    def get_workspace(self, workspace_id: str) -> Any | None:
        """获取工作区容器信息。返回 WorkspaceContainer 或 None。"""
        ...

    def list_workspaces(self) -> list[Any]:
        """列出所有工作区容器。返回 list[WorkspaceContainer]。"""
        ...

    async def health_check(self, workspace_id: str) -> bool:
        """检查工作区容器健康状态。"""
        ...


# ── SwarmBackend ──────────────────────────────────────────────────────


class SwarmBackend:
    """Docker Swarm 后端 — 从 WorkspaceContainerManager 提取。

    每个工作区对应一个 Swarm service，内含 Runtime-Agent。
    """

    def __init__(self, *, image: str = "prismclaw/runtime-agent:latest") -> None:
        self._image = image
        self._containers: dict[str, Any] = {}  # workspace_id → WorkspaceContainer
        self._docker_client: Any = None

    def _get_docker(self) -> Any:
        """获取 Docker 客户端（延迟初始化）。"""
        if self._docker_client is None:
            try:
                import docker
                self._docker_client = docker.from_env()
            except ImportError:
                raise RuntimeError("docker package not installed; run: pip install docker")
            except Exception as e:
                raise RuntimeError(f"Cannot connect to Docker: {e}")
        return self._docker_client

    def create_workspace(
        self,
        workspace_id: str,
        tag: WorkspaceTag,
        *,
        hmac_secret: str = "dev-secret",
        hmac_key_version: str = "1",
    ) -> Any:
        """创建工作区容器（Swarm service）。"""
        # 延迟导入避免循环依赖：container_manager 定义 WorkspaceContainer，
        # 顶层导入 container_backend，所以此处必须延迟导入
        from prismclaw.engine.runtime.container_manager import WorkspaceContainer

        if workspace_id in self._containers:
            return self._containers[workspace_id]

        resource = get_resource_tier(tag.template_id)
        service_name = f"prismclaw-ws-{workspace_id}"

        service_kwargs: dict[str, Any] = {
            "name": service_name,
            "image": self._image,
            "env": [
                f"PRISMCLAW_WORKSPACE_ID={workspace_id}",
                f"PRISMCLAW_WORKSPACE_ROOT=/workspace",
                # 与 bash tool 的 env_prefix 对齐：路径规范在引擎层统一注入，
                # 容器内的 bash 调用会根据 active_skill_name 再细分 SKILL_CACHE / SKILL_RUNTIME 子目录
                "PRISMCLAW_WORKSPACE=/workspace",
                "PRISMCLAW_TMP_DIR=/workspace/.work/tmp",
                # PRISMCLAW_OUTPUT_DIR 由 bash tool env_prefix 按 session 动态注入
                # （bash.py _output_dir），此处不再硬编码——旧值 /workspace/outputs
                # 是 workspace 根级，与 bash cd 进 sessions/<sid> 后的 session 级
                # 定义冲突。容器内的直接 python/node 脚本如需用，自行 cd 后 export。
                "PRISMCLAW_SKILL_CACHE=/workspace/.work/cache/skills/_default",
                # 全局共享运行时（所有 workspace 共用同一卷，node_modules/pip 依赖只装一次）
                "PRISMCLAW_RUNTIME_ROOT=/runtime",
                "PRISMCLAW_SKILL_RUNTIME=/runtime/skills/_default",
                # 运行时发现机制（与 bash.py env_prefix 对齐）
                # Python site-packages 全局可见
                "PYTHONPATH=/runtime/skills/_default/site-packages",
                # Node node_modules 全局可见
                "NODE_PATH=/runtime/skills/_default/node_modules",
                f"PRISMCLAW_RUNTIME_HMAC_SECRET={hmac_secret}",
                f"PRISMCLAW_RUNTIME_HMAC_KEY_VERSION={hmac_key_version}",
                f"PRISMCLAW_ALLOW_NET={'true' if resource.allow_net else 'false'}",
            ],
            # 注意：prismclaw-runtime 是全局共享卷（不是 per-workspace PVC），
            # 需要用户预先创建（K8s PVC / Docker volume）。所有 workspace 容器共用
            # 同一个 runtime 卷 → node_modules/pip 依赖只装一次，跨 workspace 复用。
            # TODO 并发安全：多个容器同时往 runtime/skills/<skill>/ 写 node_modules
            # 时，npm install / pip install 不是原子操作，可能互相踩坏。
            # 方案：install 前 flock <skill_runtime>/.install.lock，
            # 或 npm install --prefix <tmp> && mv tmp/node_modules <skill_runtime>/
            "mounts": [
                f"{workspace_id}-data:/workspace",
                "prismclaw-runtime:/runtime",
            ],
            "resources": {
                "limits": {
                    "cpus": resource.cpu_limit,
                    "memory": resource.memory_limit,
                },
                "reservations": {
                    "cpus": resource.cpu_reservation,
                    "memory": resource.memory_reservation,
                },
            },
            "endpoint_spec": {
                "mode": "dnsrr",
            },
            "labels": {
                "prismclaw.workspace_id": workspace_id,
                "prismclaw.template_id": tag.template_id,
                "prismclaw.sandbox_tier": resource.sandbox_tier.value,
            },
        }

        if resource.deny_pid_namespace:
            service_kwargs["env"].append("PRISMCLAW_DENY_PID_NS=true")

        try:
            client = self._get_docker()
            client.services.create(**service_kwargs)
            container = WorkspaceContainer(
                workspace_id=workspace_id,
                service_name=service_name,
                tag=tag,
                endpoint_url=f"http://{service_name}:9001",
                status="created",
            )
            self._containers[workspace_id] = container
            logger.info(
                "SwarmBackend: created workspace %s (template=%s, tier=%s)",
                workspace_id, tag.template_id, resource.sandbox_tier.value,
            )
            return container
        except Exception as e:
            logger.error("SwarmBackend: failed to create workspace %s: %s", workspace_id, e)
            raise

    def destroy_workspace(self, workspace_id: str) -> bool:
        """销毁工作区容器。"""
        container = self._containers.get(workspace_id)
        if container is None:
            return False
        try:
            client = self._get_docker()
            service = client.services.get(container.service_name)
            service.remove()
            # Docker 操作成功后才从内存中移除
            self._containers.pop(workspace_id, None)
            logger.info("SwarmBackend: destroyed workspace %s", workspace_id)
            return True
        except Exception as e:
            logger.error("SwarmBackend: failed to destroy workspace %s: %s", workspace_id, e)
            return False

    def get_workspace(self, workspace_id: str) -> Any | None:
        """获取工作区容器信息。"""
        return self._containers.get(workspace_id)

    def list_workspaces(self) -> list[Any]:
        """列出所有工作区容器。"""
        return list(self._containers.values())

    async def health_check(self, workspace_id: str) -> bool:
        """检查工作区容器健康状态。"""
        container = self._containers.get(workspace_id)
        if container is None:
            return False
        try:
            client = self._get_docker()
            service = client.services.get(container.service_name)
            tasks = service.tasks()
            if not tasks:
                return False
            latest = max(tasks, key=lambda t: t.get("CreatedAt", ""))
            return latest.get("Status", {}).get("State") == "running"
        except Exception:
            return False


# ── K8sJobBackend（Phase 4b 骨架）──────────────────────────────────────


class K8sJobBackend:
    """Kubernetes Job 后端 — Phase 4b 接入真实 K8s API。

    设计：
      - 每个工作区 = K8s Job + PVC + NetworkPolicy
      - 冷启动 P50 < 2s 目标（使用 pre-pulled image + PVC reuse）
      - 通过 Helm chart 部署（infra/helm/prismclaw/）

    当 kubernetes 客户端库可用时调用真实 K8s API；
    不可用时降级为 InMemory 存储（开发/测试用）。
    """

    def __init__(
        self,
        *,
        image: str = "prismclaw/runtime-agent:latest",
        namespace: str = "prismclaw",
    ) -> None:
        self._image = image
        self._namespace = namespace
        self._containers: dict[str, Any] = {}
        self._k8s_client: Any = None
        self._k8s_available: bool | None = None

    def _get_k8s_client(self) -> Any:
        """获取 K8s 客户端（延迟初始化）。"""
        if self._k8s_client is not None:
            return self._k8s_client
        try:
            import kubernetes
            from kubernetes import client as k8s_client
            try:
                kubernetes.config.load_incluster_config()
            except kubernetes.config.ConfigException:
                kubernetes.config.load_kube_config()
            self._k8s_client = k8s_client
            self._k8s_available = True
            logger.info("K8sJobBackend: kubernetes client initialized")
        except ImportError:
            self._k8s_available = False
            logger.warning(
                "K8sJobBackend: kubernetes package not installed; "
                "falling back to in-memory mode. Run: pip install kubernetes"
            )
        except Exception as e:
            self._k8s_available = False
            logger.warning("K8sJobBackend: cannot connect to K8s: %s; falling back to in-memory", e)
        return self._k8s_client

    def create_workspace(
        self,
        workspace_id: str,
        tag: WorkspaceTag,
        *,
        hmac_secret: str = "dev-secret",
        hmac_key_version: str = "1",
    ) -> Any:
        """创建工作区（K8s Job + PVC + NetworkPolicy）。"""
        # 延迟导入避免循环依赖（同 SwarmBackend）
        from prismclaw.engine.runtime.container_manager import WorkspaceContainer

        if workspace_id in self._containers:
            return self._containers[workspace_id]

        resource = get_resource_tier(tag.template_id)
        job_name = f"prismclaw-ws-{workspace_id}"

        container = WorkspaceContainer(
            workspace_id=workspace_id,
            service_name=job_name,
            tag=tag,
            endpoint_url=f"http://{job_name}.{self._namespace}.svc:9001",
            status="created",
        )
        self._containers[workspace_id] = container

        k8s = self._get_k8s_client()
        if k8s is not None:
            try:
                self._create_k8s_resources(
                    k8s, job_name, workspace_id, tag, resource,
                    hmac_secret=hmac_secret, hmac_key_version=hmac_key_version,
                )
                logger.info("K8sJobBackend: created workspace %s on K8s cluster", workspace_id)
            except Exception as e:
                logger.error("K8sJobBackend: failed to create K8s resources for %s: %s", workspace_id, e)
        else:
            logger.info("K8sJobBackend: created workspace %s (in-memory fallback)", workspace_id)

        return container

    def _create_k8s_resources(
        self,
        k8s: Any,
        job_name: str,
        workspace_id: str,
        tag: WorkspaceTag,
        resource: Any,
        *,
        hmac_secret: str,
        hmac_key_version: str,
    ) -> None:
        """创建 K8s Job + PVC + NetworkPolicy 资源。"""
        from kubernetes.client import (
            V1Job, V1JobSpec, V1ObjectMeta, V1PodTemplateSpec,
            V1PodSpec, V1Container, V1ContainerPort, V1EnvVar,
            V1ResourceRequirements, V1VolumeMount, V1Volume,
            V1PersistentVolumeClaimVolumeSource,
            V1PersistentVolumeClaim, V1PersistentVolumeClaimSpec,
            V1NetworkPolicy, V1NetworkPolicySpec,
            V1NetworkPolicyIngressRule, V1NetworkPolicyPeer,
            V1LabelSelector,
        )
        batch_api = k8s.BatchV1Api()
        core_api = k8s.CoreV1Api()
        networking_api = k8s.NetworkingV1Api()

        labels = {
            "app": "prismclaw-workspace",
            "prismclaw.workspace_id": workspace_id,
            "prismclaw.template_id": tag.template_id,
        }

        # PVC
        pvc_name = f"{job_name}-data"
        pvc = V1PersistentVolumeClaim(
            metadata=V1ObjectMeta(name=pvc_name, labels=labels),
            spec=V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                resources=V1ResourceRequirements(
                    requests={"storage": "1Gi"},
                ),
            ),
        )
        try:
            core_api.create_namespaced_persistent_volume_claim(self._namespace, pvc)
        except Exception as e:
            logger.warning("K8sJobBackend: PVC creation failed (may already exist): %s", e)

        # Job
        job = V1Job(
            metadata=V1ObjectMeta(name=job_name, labels=labels),
            spec=V1JobSpec(
                template=V1PodTemplateSpec(
                    metadata=V1ObjectMeta(labels=labels),
                    spec=V1PodSpec(
                        containers=[
                            V1Container(
                                name="runtime-agent",
                                image=self._image,
                                ports=[V1ContainerPort(container_port=9001)],
                                env=[
                                    V1EnvVar(name="PRISMCLAW_WORKSPACE_ID", value=workspace_id),
                                    V1EnvVar(name="PRISMCLAW_WORKSPACE_ROOT", value="/workspace"),
                                    V1EnvVar(name="PRISMCLAW_RUNTIME_HMAC_SECRET", value=hmac_secret),
                                    V1EnvVar(name="PRISMCLAW_RUNTIME_HMAC_KEY_VERSION", value=hmac_key_version),
                                    V1EnvVar(
                                        name="PRISMCLAW_ALLOW_NET",
                                        value="true" if resource.allow_net else "false",
                                    ),
                                ],
                                volume_mounts=[
                                    V1VolumeMount(name="workspace-data", mount_path="/workspace"),
                                ],
                                resources=V1ResourceRequirements(
                                    limits={
                                        "cpu": resource.cpu_limit,
                                        "memory": resource.memory_limit,
                                    },
                                    requests={
                                        "cpu": resource.cpu_reservation,
                                        "memory": resource.memory_reservation,
                                    },
                                ),
                            ),
                        ],
                        volumes=[
                            V1Volume(
                                name="workspace-data",
                                persistent_volume_claim=V1PersistentVolumeClaimVolumeSource(
                                    claim_name=pvc_name,
                                ),
                            ),
                        ],
                        restart_policy="OnFailure",
                    ),
                ),
                backoff_limit=3,
            ),
        )
        batch_api.create_namespaced_job(self._namespace, job)

        # NetworkPolicy: deny ingress except from prismclaw namespace
        netpol = V1NetworkPolicy(
            metadata=V1ObjectMeta(name=f"{job_name}-netpol", labels=labels),
            spec=V1NetworkPolicySpec(
                pod_selector=V1LabelSelector(match_labels=labels),
                ingress=[
                    V1NetworkPolicyIngressRule(
                        _from=[
                            V1NetworkPolicyPeer(
                                namespace_selector=V1LabelSelector(
                                    match_labels={"name": self._namespace},
                                ),
                            ),
                        ],
                    ),
                ],
                policy_types=["Ingress"],
            ),
        )
        try:
            networking_api.create_namespaced_network_policy(self._namespace, netpol)
        except Exception as e:
            logger.warning("K8sJobBackend: NetworkPolicy creation failed: %s", e)

    def destroy_workspace(self, workspace_id: str) -> bool:
        container = self._containers.pop(workspace_id, None)
        if container is None:
            return False

        k8s = self._get_k8s_client()
        if k8s is not None:
            try:
                job_name = container.service_name
                batch_api = k8s.BatchV1Api()
                core_api = k8s.CoreV1Api()
                networking_api = k8s.NetworkingV1Api()

                # Delete Job
                try:
                    batch_api.delete_namespaced_job(
                        job_name, self._namespace, propagation_policy="Background",
                    )
                except Exception as e:
                    logger.warning("K8sJobBackend: failed to delete Job %s: %s", job_name, e)

                # Delete PVC
                pvc_name = f"{job_name}-data"
                try:
                    core_api.delete_namespaced_persistent_volume_claim(pvc_name, self._namespace)
                except Exception as e:
                    logger.warning("K8sJobBackend: failed to delete PVC %s: %s", pvc_name, e)

                # Delete NetworkPolicy
                netpol_name = f"{job_name}-netpol"
                try:
                    networking_api.delete_namespaced_network_policy(netpol_name, self._namespace)
                except Exception as e:
                    logger.warning("K8sJobBackend: failed to delete NetworkPolicy %s: %s", netpol_name, e)

                logger.info("K8sJobBackend: destroyed workspace %s on K8s cluster", workspace_id)
            except Exception as e:
                logger.error("K8sJobBackend: failed to destroy K8s resources for %s: %s", workspace_id, e)
        else:
            logger.info("K8sJobBackend: destroyed workspace %s (in-memory fallback)", workspace_id)

        return True

    def get_workspace(self, workspace_id: str) -> Any | None:
        return self._containers.get(workspace_id)

    def list_workspaces(self) -> list[Any]:
        return list(self._containers.values())

    async def health_check(self, workspace_id: str) -> bool:
        container = self._containers.get(workspace_id)
        if container is None:
            return False

        k8s = self._get_k8s_client()
        if k8s is not None:
            try:
                core_api = k8s.CoreV1Api()
                label_selector = f"prismclaw.workspace_id={workspace_id}"
                pods = core_api.list_namespaced_pod(
                    self._namespace, label_selector=label_selector,
                )
                if not pods.items:
                    return False
                for pod in pods.items:
                    if pod.status and pod.status.phase == "Running":
                        return True
                return False
            except Exception as e:
                logger.warning("K8sJobBackend: health check K8s API failed for %s: %s", workspace_id, e)
                return False
        else:
            return container.status == "created"


# ── 全局 backend 工厂（P4 接入点）──────────────────────────────────────

from prismclaw.engine.runtime.firecracker_backend import FirecrackerBackend  # noqa: E402

# 合法值映射 — 避免拼写错误传入产生幽灵实例
_BACKEND_CLASSES: dict[str, type] = {
    "swarm": SwarmBackend,
    "docker-swarm": SwarmBackend,
    "k8s": K8sJobBackend,
    "kubernetes": K8sJobBackend,
    "k8s-job": K8sJobBackend,
    "firecracker": FirecrackerBackend,
    "fc": FirecrackerBackend,
    "microvm": FirecrackerBackend,
}


def get_container_backend(
    backend_name: str | None = None,
    *,
    image: str = "prismclaw/runtime-agent:latest",
    k8s_namespace: str = "prismclaw",
) -> ContainerBackend:
    """获取容器运行时后端（单例工厂，P4-OPS-01 官方入口）。

    选择顺序：
      1. 参数 backend_name（显式传入优先）
      2. 环境变量 ``PRISMCLAW_CONTAINER_BACKEND``
      3. 默认 ``swarm``（SwarmBackend）

    合法取值：``swarm`` / ``docker-swarm`` / ``k8s`` / ``kubernetes`` / ``k8s-job``
    / ``firecracker`` / ``fc`` / ``microvm``。
    未知值会报错并降级到 SwarmBackend。

    注意：
      - K8sJobBackend 目前为 Phase 4b 骨架；真实 K8s API 调用在 4b 中接入。
      - FirecrackerBackend 为 P4-OPS-02 骨架；真实 Firecracker API 在后续 Phase 接入。
      - 此处每次调用返回新实例 — 单例化由调用方（WorkspaceContainerManager）负责。
    """
    import os

    name = (backend_name or os.environ.get("PRISMCLAW_CONTAINER_BACKEND", "swarm")).lower()
    if name not in _BACKEND_CLASSES:
        logger.warning(
            "get_container_backend: unknown backend %r, fallback to swarm (valid: %s)",
            name, ", ".join(sorted(_BACKEND_CLASSES.keys())),
        )
        name = "swarm"

    cls = _BACKEND_CLASSES[name]
    kwargs: dict[str, Any] = {}
    if cls is SwarmBackend:
        kwargs["image"] = image
    elif cls is K8sJobBackend:
        kwargs["image"] = image
        kwargs["namespace"] = k8s_namespace
    # FirecrackerBackend takes no 'image' param; uses its own defaults

    instance = cls(**kwargs)
    logger.info("get_container_backend: instantiated %s (requested=%s)", cls.__name__, name)
    return instance
