from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prismclaw.engine.execution.capabilities.catalog import RuntimeCatalog
    from prismclaw.engine.execution.skills.runtime import SkillRuntime
    from prismclaw.engine.experts.registry.expert_registry import ExpertRegistry
    from prismclaw.engine.mcp.client_manager import MCPClientManager
    from prismclaw.engine.toolkit.registry.registry import ToolRegistry
    from prismclaw.infra.settings import BuiltinToolSettings

from prismclaw.engine.subagent.team_tools import TeamCreateTool, TeamDeleteTool
from prismclaw.engine.toolkit.builtin.bash import BashTool
from prismclaw.engine.toolkit.builtin.file_tools import (
    EditTool,
    ListDirectoryTool,
    ReadTool,
    WriteTool,
)
from prismclaw.engine.toolkit.builtin.skill_activate import ActivateSkillTool
from prismclaw.engine.toolkit.builtin.task_tools import (
    AskUserQuestionTool,
    DeclareBlockedTool,
)
from prismclaw.engine.toolkit.file.present_files import PresentFilesTool
from prismclaw.engine.toolkit.file.workspace import WorkspaceResolver
from prismclaw.engine.toolkit.memory.goal_tools import GoalTool
from prismclaw.engine.toolkit.misc.todo_write import TodoWriteTool
from prismclaw.engine.toolkit.registry.discovery import SkillSearchTool, ToolSearchTool

logger = logging.getLogger(__name__)


def register_builtin_tools(
    registry: ToolRegistry,
    resolver: WorkspaceResolver,
    capability_catalog: RuntimeCatalog | None = None,
    skill_runtime: SkillRuntime | None = None,
    *,
    expert_registry: ExpertRegistry | None = None,
) -> None:
    registry.register(BashTool())
    registry.register(ListDirectoryTool(resolver))
    registry.register(ReadTool(resolver))
    registry.register(WriteTool(resolver))
    registry.register(EditTool(resolver))
    registry.register(AskUserQuestionTool())
    registry.register(DeclareBlockedTool())
    registry.register(PresentFilesTool(resolver))
    registry.register(TodoWriteTool())
    registry.register(GoalTool())
    if expert_registry is not None and capability_catalog is not None:
        registry.register(
            TeamCreateTool(
                expert_registry=expert_registry,
                capability_catalog=capability_catalog,
            )
        )
        registry.register(TeamDeleteTool())

    if capability_catalog is not None:
        registry.register(ToolSearchTool(capability_catalog=capability_catalog))
        registry.register(SkillSearchTool(capability_catalog=capability_catalog))
        if skill_runtime is not None:
            registry.register(
                ActivateSkillTool(
                    skill_runtime=skill_runtime,
                    capability_catalog=capability_catalog,
                )
            )


def register_tier2_builtin_tools(
    registry: ToolRegistry,
    resolver: WorkspaceResolver,
    builtin_tool_settings: BuiltinToolSettings,
    *,
    mcp_manager: MCPClientManager | None = None,
) -> None:
    # grep/glob always registered (no conditional gating). Web search/fetch/
    # extract moved to a standalone MCP web-retrieval service, which is wired
    # in through the MCP client manager instead of builtin registration.
    from prismclaw.engine.toolkit.file.glob import GlobTool
    from prismclaw.engine.toolkit.file.grep import GrepTool

    registry.register(GrepTool(resolver))
    registry.register(GlobTool(resolver))

    # fetch_mcp_resource remains conditionally registered
    if builtin_tool_settings.fetch_mcp_resource_enabled and mcp_manager is not None:
        from prismclaw.engine.toolkit.mcp.fetch_mcp_resource import FetchMcpResourceTool

        registry.register(FetchMcpResourceTool(mcp_manager))
