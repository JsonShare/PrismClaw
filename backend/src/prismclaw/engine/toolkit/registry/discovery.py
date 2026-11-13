from __future__ import annotations

from typing import Any

from prismclaw.engine.execution.capabilities.catalog import CapabilityCatalogService
from prismclaw.engine.execution.capabilities.discovery_runtime import (
    SkillDiscoveryRuntime,
    SubagentDiscoveryRuntime,
    ToolDiscoveryRuntime,
)

from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext, error_result, text_result
from prismclaw.schema.content.content_types import StateDeltaArtifact
from prismclaw.schema.services.skills import SkillInvocationState


def _coerce_skill_invocation(context: ToolContext) -> SkillInvocationState | None:
    raw_state = context.invoked_skill_state
    if not isinstance(raw_state, dict):
        return None
    raw = raw_state.get("active_runtime")
    if isinstance(raw, SkillInvocationState):
        return raw
    if not isinstance(raw, dict):
        return None
    try:
        return SkillInvocationState.model_validate(raw)
    except Exception:
        return None


def _coerce_discovered_tools(context: ToolContext) -> set[str] | None:
    raw_turn_state = context.turn_state
    if raw_turn_state is None:
        return None
    from prismclaw.engine.state.turn_state import TurnState

    if isinstance(raw_turn_state, TurnState):
        discovered = raw_turn_state.discovered_tool_names()
        return discovered or None
    if not isinstance(raw_turn_state, dict):
        return None
    try:
        restored = TurnState.model_validate(raw_turn_state)
    except Exception:
        return None
    discovered = restored.discovered_tool_names()
    return discovered or None


def _coerce_permission_tier(context: ToolContext) -> str:
    mode = context.mode
    return mode if isinstance(mode, str) and mode else "work"


def _coerce_headless(context: ToolContext) -> bool:
    """是否为无头执行（无法暂停等用户回答）。由 runner 写入 ToolContext。"""
    return bool(context.headless)


def _render_tool_fallback(query: str, all_surface: list[Any], *, skill_search_visible: bool) -> str:
    """0 结果兜底：列出全部可见工具，避免模型因"没找到"而盲目重试同一 query。"""
    reroute = ""
    if skill_search_visible:
        reroute = (
            " 如果你要找的是可复用工作流或领域技能，而不是具体工具，"
            "请用 `skill_search` 检索。"
        )
    lines = [
        f"按关键词评分未匹配到工具“{query}”。",
        "",
        "### 全部可见工具（请按名称/描述选择最接近的一项）：",
        "",
    ]
    for entry in all_surface:
        desc = (getattr(entry, "description", "") or "")[:120]
        lines.append(f"- `{entry.canonical_name}`: {desc}")
    lines.append(
        "\n---\n"
        "若其中某项工具符合需求，请直接使用；否则改用通用工具。"
        f"{reroute} "
        "不要用相同或近似查询重复运行 tool_search。"
    )
    return "\n".join(lines)


def _render_tool_matches(query: str, matches: list[Any], *, skill_search_visible: bool) -> str:
    if not matches:
        reroute = ""
        if skill_search_visible:
            reroute = (
                " 如果你要找的是可复用工作流或领域技能，而不是具体工具，"
                "请用 `skill_search` 检索。"
            )
        return (
            f"未找到匹配“{query}”的工具。若上下文中的现有工具已足够，请直接使用。"
            f"{reroute}"
        )
    lines = [
        f"基于“{query}”找到 {len(matches)} 个相关工具。",
        "",
        "### 可用工具：",
    ]
    for match in matches:
        lines.append(f"#### 工具：`{match.name}`")
        if match.canonical_name != match.name:
            lines.append(
                f"**规范名称**：`{match.canonical_name}`（可调用的运行时名称为 `{match.name}`）"
            )
        lines.append(f"**描述**：{match.description or '未提供描述。'}")
        lines.append(f"**类别**：`{match.tool_class}`")
        lines.append(f"**副作用**：`{match.effect_class}`")
        lines.append("")
    lines.append(
        "---\n请使用运行时已提供的工具 schema 直接调用上述工具之一。"
    )
    return "\n".join(lines)


def _render_skill_fallback(query: str, all_skills: list[Any]) -> str:
    """0 结果兜底：列出全部技能，避免模型因"没找到"而盲目重试同一 query。"""
    lines = [
        f"按关键词评分未匹配到技能“{query}”。",
        "",
        "### 全部可用技能（请按名称/描述选择最接近的一项）：",
        "**关键：调用 `activate_skill` 工具时必须使用下方完全一致的名称字符串——"
        "不要重新输入、缩写或改变大小写。**",
        "",
    ]
    for match in all_skills:
        lines.append(f"- `{match.name}`：{match.description or '未提供描述。'}")
        if match.marketplace_name:
            desc = f" — {match.marketplace_description}" if match.marketplace_description else ""
            lines.append(f"  插件：{match.marketplace_name}{desc}")
    lines.append(
        "\n---\n"
        "若上方有技能符合你的任务，请立即用其精确名称调用 `activate_skill`。"
        "若都不符合，请改用通用工具（Read、Write、Edit、Bash）——"
        "不要用相同或近似查询重复运行 skill_search。"
    )
    return "\n".join(lines)


def _render_skill_matches(query: str, matches: list[Any]) -> str:
    """渲染技能搜索结果：name + description（无 instructions）。"""
    if not matches:
        return (
            f"未找到匹配“{query}”的技能。"
            "该任务没有对应技能。"
            "请直接使用通用工具（Read、Write、Edit、Bash）继续。"
        )
    lines = [
        f"根据“{query}”找到 {len(matches)} 个相关技能。",
        "",
        "### 可用技能：",
        "**下一步：用下方完全一致的名称字符串调用 `activate_skill`"
        "（逐字符复制；不要缩写或改变大小写）。在你调用 `activate_skill` 之前，"
        "该技能尚未激活。**",
        "",
    ]
    for match in matches:
        lines.append(f"- `{match.name}`：{match.description or '未提供描述。'}")
        if match.marketplace_name:
            desc = f" — {match.marketplace_description}" if match.marketplace_description else ""
            lines.append(f"  插件：{match.marketplace_name}{desc}")
    lines.append(
        "\n---\n"
        "请立即用最匹配技能的精确名称调用 `activate_skill`，然后遵循注入的指令。"
        "若这些技能都不匹配任务，请改用通用工具——不要猜测其他技能名。"
        "同样不要用近似查询重复运行 skill_search。"
    )
    return "\n".join(lines)


def _subagent_origin(match: Any) -> str:
    parts = []
    if match.package:
        parts.append(f"包 {match.package}")
    if match.sub_plugin:
        parts.append(f"子插件 {match.sub_plugin}")
    return " / ".join(parts)


def _render_subagent_matches(query: str, matches: list[Any]) -> str:
    lines = [
        f"根据“{query}”找到 {len(matches)} 个插件子代理。",
        "",
        "### 可用子代理：",
        "**下一步：用下方完全一致的名称调用 `sub_agent`（agents=[{agent_name, task}]）；"
        "逐字符复制，不要缩写或改变大小写。**",
        "",
    ]
    for match in matches:
        lines.append(f"- `{match.name}`：{match.description or '未提供描述。'}")
        origin = _subagent_origin(match)
        if origin:
            lines.append(f"  {origin}")
    lines.append(
        "\n---\n"
        "若这些子代理都不匹配任务，请自行处理或改用核心子代理——不要猜测其他名称，"
        "也不要用近似查询重复运行 subagent_search。"
    )
    return "\n".join(lines)


def _render_subagent_fallback(query: str, all_agents: list[Any]) -> str:
    if not all_agents:
        return (
            f"未找到匹配“{query}”的插件子代理，该范围内没有可用条目。"
            "核心子代理已列在 `<subagents>`；任务简单时请直接自行处理。"
        )
    lines = [
        f"按关键词未匹配到插件子代理“{query}”。",
        "",
        "### 全部插件子代理（请按名称/描述选择最接近的一项）：",
        "**调用 `sub_agent` 时必须使用下方完全一致的名称。**",
        "",
    ]
    for match in all_agents:
        lines.append(f"- `{match.name}`：{match.description or '未提供描述。'}")
        origin = _subagent_origin(match)
        if origin:
            lines.append(f"  {origin}")
    lines.append(
        "\n---\n"
        "核心子代理已列在 `<subagents>`；若无合适插件子代理，请自行处理。"
        "不要用相同或近似查询重复运行 subagent_search。"
    )
    return "\n".join(lines)


class ToolSearchTool:
    name = "tool_search"
    description = (
        "在当前可用工具中检索，找到适合某项能力或任务的工具。"
        "仅用于发现工具；如需发现技能，请使用 `skill_search`。"
    )
    tool_class = "read"
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "用自然语言描述你需要的能力，或直接给出精确的工具名。",
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    side_effect = False
    effect_class = "pure_read"

    def __init__(self, *, capability_catalog: CapabilityCatalogService):
        registry = capability_catalog.tool_registry
        if registry is None:
            raise RuntimeError("ToolSearchTool requires capability_catalog.tool_registry")
        self._registry = registry
        self._runtime = ToolDiscoveryRuntime(
            capability_catalog=capability_catalog
        )

    async def execute(self, args: dict[str, Any], context: ToolContext):
        call_id = args.get("_call_id", "")
        query = args.get("query", "")
        if not isinstance(query, str) or not query.strip():
            return error_result(call_id, "缺少必填参数 'query'")

        skill_invocation = _coerce_skill_invocation(context)
        discovered_tools = _coerce_discovered_tools(context)
        all_surface = self._runtime.visible_surface(
            skill_invocation=skill_invocation,
            discovered_tools=discovered_tools,
            mode=_coerce_permission_tier(context),
            subagent_depth=context.subagent_depth,
            headless=_coerce_headless(context),
        )
        matches = self._runtime.search_visible_surface(
            query,
            all_surface,
            limit=5,
        )
        discovered: list[dict[str, Any]] = []
        if matches:
            for match in matches:
                discovered.append(
                    {
                        "canonical_name": match.canonical_name,
                        "query": query.strip(),
                        "discovered_via": "tool_search",
                        "source_tool_name": self.name,
                        "tool_call_id": call_id or None,
                    }
                )

        skill_search_visible = any(
            entry.canonical_name == "skill_search"
            for entry in all_surface
        )
        if not matches:
            # 0 结果兜底：列出全部可见工具名，避免模型盲目重试同一 query 触发 doom-loop
            return text_result(
                call_id,
                _render_tool_fallback(query, all_surface, skill_search_visible=skill_search_visible),
            )
        return text_result(
            call_id,
            _render_tool_matches(
                query,
                matches,
                skill_search_visible=skill_search_visible,
            ),
            state_delta=(
                StateDeltaArtifact(
                    delta_type="tool_discoveries",
                    tool_discoveries=discovered,
                )
                if discovered
                else None
            ),
        )


class SkillSearchTool:
    name = "skill_search"
    description = (
        "按主题、工作流或领域检索可用技能。"
        "仅用于发现技能；如需检索工具，请使用 `tool_search`。"
    )
    tool_class = "read"
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "用自然语言描述你需要的技能、工作流或领域。",
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    side_effect = False
    effect_class = "pure_read"

    def __init__(self, *, capability_catalog: CapabilityCatalogService):
        self._runtime = SkillDiscoveryRuntime(
            capability_catalog=capability_catalog,
        )

    async def execute(self, args: dict[str, Any], context: ToolContext):
        call_id = args.get("_call_id", "")
        query = args.get("query", "")
        if not isinstance(query, str) or not query.strip():
            return error_result(call_id, "缺少必填参数 'query'")
        matches = self._runtime.search(query, workspace_path=context.workspace_path, limit=5)
        if not matches:
            # 0 结果兜底：列出全部技能，避免模型盲目重试同一 query 触发 doom-loop
            all_skills = self._runtime.list_all(workspace_path=context.workspace_path)
            return text_result(call_id, _render_skill_fallback(query, all_skills))
        return text_result(call_id, _render_skill_matches(query, matches))


class SubagentSearchTool:
    name = "subagent_search"
    description = (
        "按领域、职责或任务检索插件提供的子代理。"
        "仅用于发现；调用仍用 `sub_agent`。核心子代理见 `<subagents>`。"
    )
    tool_class = "read"
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "用自然语言描述你需要的子代理职责、领域或任务。",
            },
            "package": {
                "type": "string",
                "description": "可选：限定插件包名或子插件名。",
            },
            "limit": {
                "type": "integer",
                "description": "返回条数上限（默认 10，最大 20）。",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    side_effect = False
    effect_class = "pure_read"

    def __init__(self, *, capability_catalog: CapabilityCatalogService):
        self._runtime = SubagentDiscoveryRuntime(capability_catalog=capability_catalog)

    async def execute(self, args: dict[str, Any], context: ToolContext):
        call_id = args.get("_call_id", "")
        query = args.get("query", "")
        if not isinstance(query, str) or not query.strip():
            return error_result(call_id, "缺少必填参数 'query'")
        raw_package = args.get("package")
        package = raw_package.strip() if isinstance(raw_package, str) and raw_package.strip() else None
        raw_limit = args.get("limit", 10)
        limit = raw_limit if isinstance(raw_limit, int) and not isinstance(raw_limit, bool) else 10
        matches = self._runtime.search(query, package=package, limit=limit)
        if not matches:
            # 0 结果兜底：列出范围内插件子代理，避免模型盲目重试同一 query
            return text_result(
                call_id,
                _render_subagent_fallback(
                    query,
                    self._runtime.list_plugin_agents(limit=20, package=package),
                ),
            )
        return text_result(call_id, _render_subagent_matches(query, matches))
