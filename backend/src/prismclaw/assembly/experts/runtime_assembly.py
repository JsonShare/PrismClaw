from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prismclaw.assembly.experts.expert_lifecycle import ExpertLifecycleManager
from prismclaw.engine.experts.registry.expert_registry import ExpertRegistry
from prismclaw.infra.config.capability_policy import CapabilityCatalog, CapabilityPolicyRegistry
from prismclaw.infra.environment_catalog import EnvironmentCatalog
from prismclaw.infra.settings import Settings
from prismclaw.infra.task_manager import TaskManager
from prismclaw.schema.shared import ProjectCapabilityLayout

if TYPE_CHECKING:
    from prismclaw.engine.mcp.client_manager import MCPClientManager


def _default_capability_policy_path() -> Path:
    """优先正式文件，回退 example 模板。"""
    root = Path(__file__).resolve().parents[4] / "configs"
    prod = root / "capability_policy.yaml"
    if prod.exists():
        return prod
    example = root / "capability_policy.example.yaml"
    return example


_DEFAULT_CAPABILITY_POLICY_PATH = _default_capability_policy_path()


class _EmptyCapabilityCatalog:
    """运行时拿不到专家目录时的空目录：不校验任何模块 ID，全部策略落到默认分支。"""

    def known_ids(self, kind: str) -> tuple[str, ...]:
        return ()


@dataclasses.dataclass(slots=True)
class ExpertRuntimeAssembly:
    expert_lifecycle_manager: ExpertLifecycleManager
    extension_manager: Any
    skill_provider: Any
    runtime_mcp_manager: Any
    capability_policy_registry: CapabilityPolicyRegistry
    expert_registry: ExpertRegistry
    environment_catalog: EnvironmentCatalog | None = None
    capability_layout: ProjectCapabilityLayout | None = None


async def build_expert_runtime(
    settings: Settings,
    *,
    workspace_default: Path | str,
    project_root: Path | str | None,
    task_manager: TaskManager | None,
    base_mcp_manager: MCPClientManager | None,
    capability_layout: ProjectCapabilityLayout | None = None,
    module_catalog: CapabilityCatalog | None = None,
    capability_policy_path: Path | str | None = None,
) -> ExpertRuntimeAssembly:
    expert_lifecycle_manager = ExpertLifecycleManager(
        settings,
        workspace_default=workspace_default,
        project_root=project_root,
        additional_project_roots=[Path(workspace_default).expanduser()],  # noqa: ASYNC240
        task_manager=task_manager,
        base_mcp_manager=base_mcp_manager,
        capability_layout=capability_layout,
    )
    expert_runtime = await expert_lifecycle_manager.get_bundle()
    catalog = module_catalog
    if catalog is None:
        expert_registry = getattr(expert_runtime, "expert_registry", None)
        catalog = (
            expert_registry
            if isinstance(expert_registry, CapabilityCatalog)
            else _EmptyCapabilityCatalog()
        )
    capability_policy_registry = CapabilityPolicyRegistry(
        capability_policy_path or _DEFAULT_CAPABILITY_POLICY_PATH,
        catalog=catalog,
    )
    # 读侧 facade：专家目录 + 能力策略的唯一快照入口
    expert_registry = expert_runtime.expert_registry or ExpertRegistry(())
    environment_catalog = EnvironmentCatalog(
        expert_registry=expert_registry,
        capability_policy_registry=capability_policy_registry,
    )
    return ExpertRuntimeAssembly(
        expert_lifecycle_manager=expert_lifecycle_manager,
        extension_manager=expert_runtime.extension_manager,
        skill_provider=expert_runtime.skill_provider,
        runtime_mcp_manager=expert_runtime.runtime_mcp_manager,
        capability_policy_registry=capability_policy_registry,
        expert_registry=expert_registry,
        environment_catalog=environment_catalog,
        capability_layout=expert_runtime.capability_layout,
    )
