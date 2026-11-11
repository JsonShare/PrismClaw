from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from prismclaw.assembly.plugins.plugin_lifecycle import PluginLifecycleManager
from prismclaw.infra.settings import Settings
from prismclaw.infra.task_manager import TaskManager
from prismclaw.schema.shared import ProjectCapabilityLayout


@dataclasses.dataclass(slots=True)
class PluginRuntimeAssembly:
    plugin_lifecycle_manager: PluginLifecycleManager
    extension_manager: Any
    skill_provider: Any
    hook_dispatcher: Any
    runtime_mcp_manager: Any
    capability_layout: ProjectCapabilityLayout | None = None


async def build_plugin_runtime(
    settings: Settings,
    *,
    workspace_default: Path | str,
    project_root: Path | str | None,
    task_manager: TaskManager | None,
    base_mcp_manager: Any,
    capability_layout: ProjectCapabilityLayout | None = None,
) -> PluginRuntimeAssembly:
    plugin_lifecycle_manager = PluginLifecycleManager(
        settings,
        workspace_default=workspace_default,
        project_root=project_root,
        additional_project_roots=[Path(workspace_default).expanduser()],
        task_manager=task_manager,
        base_mcp_manager=base_mcp_manager,
        capability_layout=capability_layout,
    )
    plugin_runtime = await plugin_lifecycle_manager.get_bundle()
    return PluginRuntimeAssembly(
        plugin_lifecycle_manager=plugin_lifecycle_manager,
        extension_manager=plugin_runtime.extension_manager,
        skill_provider=plugin_runtime.skill_provider,
        hook_dispatcher=plugin_runtime.hook_dispatcher,
        runtime_mcp_manager=plugin_runtime.runtime_mcp_manager,
        capability_layout=plugin_runtime.capability_layout,
    )

