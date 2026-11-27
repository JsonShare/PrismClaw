"""专家模式的内置工具面集合；MCP 工具不参与这些白名单。"""

EXPERT_MODE_TOOLS = frozenset(
    {
        "read",
        "write",
        "edit",
        "glob",
        "grep",
        "bash",
        "todo_write",
        "present_files",
        "ask_user_question",
        "activate_skill",
        "skill_search",
        "tool_search",
    }
)
TEAM_LEAD_TOOLS = frozenset({"sub_agent", "subagent_control"})
DEFERRED_TEAM_TOOLS = frozenset({"team_create", "team_delete"})

__all__ = [
    "DEFERRED_TEAM_TOOLS",
    "EXPERT_MODE_TOOLS",
    "TEAM_LEAD_TOOLS",
]
