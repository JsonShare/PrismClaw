from __future__ import annotations

import re
from typing import Any

from prismclaw.engine.subagent.definition.manifest import AgentManifest
from prismclaw.schema.services.skills import SkillManifest

_INVALID_AGENT_NAME_CHARS = re.compile(r"[^a-z0-9-]+")
_DUPLICATE_HYPHENS = re.compile(r"-{2,}")


def derive_skill_agent_name(skill_name: str, explicit_name: str | None = None) -> str:
    base = explicit_name or skill_name
    normalized = base.lower().replace(".", "-").replace("_", "-")
    normalized = _INVALID_AGENT_NAME_CHARS.sub("-", normalized)
    normalized = _DUPLICATE_HYPHENS.sub("-", normalized).strip("-")
    if not normalized:
        normalized = "skill-agent"
    if not normalized[0].isalpha():
        normalized = f"s-{normalized}"
    return normalized[:64].rstrip("-")


def build_agent_manifest_from_skill(skill: SkillManifest) -> AgentManifest:
    agent = skill.agent_config
    agent_name = derive_skill_agent_name(skill.name, agent.name if agent else None)
    description = (
        agent.description
        if agent and agent.description.strip()
        else skill.description or f"为技能 '{skill.name}' 自动生成的子代理。"
    )
    allowed_tools = (
        list(agent.allowed_tools)
        if agent and agent.allowed_tools is not None
        else list(skill.allowed_tools)
        if skill.allowed_tools is not None
        else None
    )
    can_handoff_to = list(agent.can_handoff_to) if agent and agent.can_handoff_to else None
    if can_handoff_to and allowed_tools is not None and "request_handoff" not in allowed_tools:
        allowed_tools = [*allowed_tools, "request_handoff"]
    execution_mode = "parallel" if skill.execution_mode == "fork" else "sync"
    return AgentManifest(
        name=agent_name,
        description=description,
        model=agent.model if agent else None,
        execution_mode=execution_mode,
        allowed_tools=allowed_tools,
        max_turns=agent.max_turns if agent else None,
        timeout_seconds=agent.timeout_seconds if agent else None,
        can_handoff_to=can_handoff_to,
        system_prompt=skill.body or "",
        preload_skills=list(skill.declared_invoke_sequence or ()),
    )


def agent_manifest_to_skill_payload(manifest: AgentManifest) -> dict[str, Any]:
    return manifest.model_dump(mode="json", exclude_none=True)
