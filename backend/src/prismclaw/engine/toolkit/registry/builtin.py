from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from prismclaw.engine.execution.skills.runtime import SkillRuntime

from prismclaw.engine.toolkit.registry.discovery import SkillSearchTool, ToolSearchTool
from prismclaw.engine.toolkit.builtin.skill_activate import ActivateSkillTool
from prismclaw.engine.toolkit.builtin.bash import BashTool
from prismclaw.engine.toolkit.builtin.file_tools import ListDirectoryTool, ReadTool, WriteTool, EditTool
from prismclaw.engine.toolkit.builtin.task_tools import CancelTaskTool, AskUserQuestionTool, DeclareBlockedTool
from prismclaw.engine.toolkit.file.workspace import WorkspaceResolver
from prismclaw.engine.toolkit.misc.todo_write import TodoWriteTool
from prismclaw.engine.toolkit.memory.goal_tools import GoalTool
from prismclaw.engine.toolkit.file.present_files import PresentFilesTool

logger = logging.getLogger(__name__)


def register_builtin_tools(
    registry,
    resolver: WorkspaceResolver,
    capability_catalog: Any = None,
    skill_runtime: SkillRuntime | None = None,
) -> None:
    registry.register(BashTool())
    registry.register(CancelTaskTool())
    registry.register(ListDirectoryTool(resolver))
    registry.register(ReadTool(resolver))
    registry.register(WriteTool(resolver))
    registry.register(EditTool(resolver))
    registry.register(AskUserQuestionTool())
    registry.register(DeclareBlockedTool())
    registry.register(PresentFilesTool(resolver))
    registry.register(TodoWriteTool())
    registry.register(GoalTool())
    
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
    registry,
    resolver: WorkspaceResolver,
    builtin_tool_settings: Any,
    *,
    mcp_manager: Any = None,
) -> None:
    # grep/glob always registered (no conditional gating). Web search/fetch/
    # extract moved to a standalone MCP web-retrieval service, which is wired
    # in through the MCP client manager instead of builtin registration.
    from prismclaw.engine.toolkit.file.grep import GrepTool
    from prismclaw.engine.toolkit.file.glob import GlobTool

    registry.register(GrepTool(resolver))
    registry.register(GlobTool(resolver))

    # fetch_mcp_resource remains conditionally registered
    if builtin_tool_settings.fetch_mcp_resource_enabled and mcp_manager is not None:
        from prismclaw.engine.toolkit.mcp.fetch_mcp_resource import FetchMcpResourceTool

        registry.register(FetchMcpResourceTool(mcp_manager))
