from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prismclaw.engine.execution.capabilities.catalog import CapabilityCatalogService
from prismclaw.engine.toolkit.registry.registry import ToolRegistry

if TYPE_CHECKING:
    from prismclaw.engine.execution.skills.runtime import SkillRuntime


@dataclass(slots=True)
class RuntimeCapabilityBundle:
    workspace_path: Path
    tool_registry: ToolRegistry
    capability_catalog: CapabilityCatalogService
    skill_runtime: SkillRuntime
    subagent_registry: Any = None
    extension_manager: Any = None
    skill_provider: Any = None
    project_root: Path | None = None
