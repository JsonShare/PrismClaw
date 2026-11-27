"""Services sub-contracts: hooks, experts, skills, upload, env types."""

from prismclaw.schema.services.auth import UserProfile, UserProfileStore
from prismclaw.schema.services.env_types import CommandResult, ExecutionEnv, FileResult
from prismclaw.schema.services.experts import ExpertLoadProtocol
from prismclaw.schema.services.hooks import (
    HookContextProtocol,
    HookHandlerProtocol,
    HookResultProtocol,
)
from prismclaw.schema.services.skills import (
    MCP_SKILL_NAME_SEPARATOR,
    McpPromptResolver,
    ResolvableEntry,
    SkillAgentConfig,
    SkillExecutionStrategy,
    SkillInvocation,
    SkillInvocationState,
    SkillManifest,
    SkillOriginDescriptorProtocol,
    SkillRequirements,
    mcp_prompt_name,
    mcp_skill_name,
    normalize_skill_ref,
    resolve_skill_name,
)
from prismclaw.schema.services.task_protocol import TaskManagerProtocol
from prismclaw.schema.services.upload import UploadServiceProtocol

__all__ = [
    "TaskManagerProtocol",
    "HookContextProtocol",
    "HookHandlerProtocol",
    "HookResultProtocol",
    "ExpertLoadProtocol",
    "MCP_SKILL_NAME_SEPARATOR",
    "McpPromptResolver",
    "ResolvableEntry",
    "SkillAgentConfig",
    "SkillExecutionStrategy",
    "SkillInvocation",
    "SkillInvocationState",
    "SkillManifest",
    "SkillOriginDescriptorProtocol",
    "SkillRequirements",
    "mcp_prompt_name",
    "mcp_skill_name",
    "normalize_skill_ref",
    "resolve_skill_name",
    "UploadServiceProtocol",
    "CommandResult",
    "ExecutionEnv",
    "FileResult",
    "UserProfile",
    "UserProfileStore",
]
