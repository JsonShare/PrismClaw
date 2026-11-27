"""专家包清单与 Agent MD 的加载期校验。"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Literal, NoReturn

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

from prismclaw.engine.experts.codes import ExpertError, ExpertErrorCode
from prismclaw.engine.experts.registry.identity import (
    is_valid_expert_name,
)
from prismclaw.schema.shared import _extract_frontmatter
from prismclaw.schema.shared.utils import estimate_tokens

logger = logging.getLogger(__name__)

EXPERT_AGENT_TARGET_TOKENS = 2000
EXPERT_AGENT_MAX_TOKENS = 4000

_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_HOOK_MCP_KEYS = frozenset({"hooks", "mcp", "mcpServers", "mcp_servers"})
_COMMAND_LSP_KEYS = frozenset({"commands", ".lsp.json", "lsp", "lspServers", "lsp_servers"})
_AGENT_TOOLS_KEYS = frozenset({"tools", "allowed_tools", "allowedTools"})
_AGENT_FIELD_ALIASES = {
    "preloadSkills": "preload_skills",
}


class ExpertLocalizedText(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    en: str = Field(min_length=1)
    zh: str = Field(min_length=1)


class ExpertTeamInfo(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    lead_agent: str = Field(alias="leadAgent", min_length=1)
    member_agents: list[str] = Field(alias="memberAgents")


class ExpertMember(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True, str_strip_whitespace=True)

    id: str = Field(min_length=1)
    # WorkBuddy 的规范表使用 name、模板使用 displayName；两种输入都接受，统一输出 displayName。
    display_name: ExpertLocalizedText = Field(
        validation_alias=AliasChoices("displayName", "name"),
        serialization_alias="displayName",
    )
    profession: ExpertLocalizedText
    avatar: str = Field(min_length=1)
    role: Literal["lead", "member"]


class ExpertPackageManifest(BaseModel):
    """02 §3.1 的专家清单；原始禁项在模型解析前单独检查。"""

    model_config = ConfigDict(extra="ignore", populate_by_name=True, str_strip_whitespace=True)

    name: str
    version: str
    description: str = Field(min_length=1)
    expert_type: Literal["agent", "team"] = Field(alias="expertType")
    agent_name: str = Field(alias="agentName", min_length=1)
    agents: list[str] | None = None
    skills: list[str] | None = None
    team_info: ExpertTeamInfo | None = Field(default=None, alias="teamInfo")
    members: list[ExpertMember] | None = None
    display_name: ExpertLocalizedText = Field(alias="displayName")
    profession: ExpertLocalizedText
    # 中文目标 40–50 字（仅告警）；中文字符串与 WorkBuddy 的 en/zh 映射两种表示均接受。
    display_description: str | ExpertLocalizedText = Field(alias="displayDescription")
    avatar: str = Field(min_length=1)
    category_id: str = Field(alias="categoryId", min_length=1)
    default_init_prompt: ExpertLocalizedText = Field(alias="defaultInitPrompt")
    tags: list[str | ExpertLocalizedText] | None = None
    quick_prompts: list[ExpertLocalizedText] | None = Field(default=None, alias="quickPrompts")
    expert: str | None = None


def _raise_invalid(field: str, message: str, **detail: object) -> NoReturn:
    raise ExpertError(
        ExpertErrorCode.MANIFEST_INVALID,
        message,
        {"field": field, **detail},
    )


def _validation_details(exc: ValidationError, *, file_path: Path) -> dict[str, Any]:
    return {
        "file_path": str(file_path),
        "errors": [
            {
                "field": ".".join(str(part) for part in error["loc"]),
                "message": error["msg"],
                "kind": error["type"],
            }
            for error in exc.errors()
        ],
    }


def _validate_forbidden_declarations(install_path: Path, raw: dict[str, Any]) -> None:
    hook_mcp_fields = sorted(_HOOK_MCP_KEYS & raw.keys())
    hook_mcp_paths = [
        path
        for path in (
            install_path / "hooks",
            install_path / "mcp.json",
            install_path / ".mcp.json",
            install_path / ".plugin" / "mcp.json",
        )
        if path.exists()
    ]
    if hook_mcp_fields or hook_mcp_paths:
        raise ExpertError(
            ExpertErrorCode.MANIFEST_EXPERT_HAS_HOOKS_OR_MCP,
            "Expert packages must not declare hooks or MCP",
            {
                "fields": hook_mcp_fields,
                "paths": [str(path) for path in hook_mcp_paths],
            },
        )

    command_lsp_fields = sorted(_COMMAND_LSP_KEYS & raw.keys())
    command_lsp_paths = [
        path
        for path in (
            install_path / "commands",
            install_path / ".lsp.json",
            install_path / ".plugin" / ".lsp.json",
        )
        if path.exists()
    ]
    if command_lsp_fields or command_lsp_paths:
        _raise_invalid(
            "commands/.lsp.json",
            "Expert packages must not declare commands or LSP servers",
            fields=command_lsp_fields,
            paths=[str(path) for path in command_lsp_paths],
        )


def _validate_display_and_team(manifest: ExpertPackageManifest) -> None:
    if not is_valid_expert_name(manifest.name):
        _raise_invalid("name", f"Expert name must be kebab-case: {manifest.name!r}")
    if not _SEMVER_RE.fullmatch(manifest.version):
        _raise_invalid("version", f"Expert version must be semantic: {manifest.version!r}")
    if not is_valid_expert_name(manifest.agent_name):
        _raise_invalid(
            "agentName",
            f"agentName must be lowercase letters/digits/hyphens: {manifest.agent_name!r}",
        )
    if manifest.agent_name == "team-lead":
        _raise_invalid(
            "agentName",
            "agentName must be business-specific, not the generic 'team-lead'",
        )
    if manifest.expert is not None and manifest.expert != manifest.name:
        _raise_invalid(
            "expert",
            "expert must equal name when present",
            expert=manifest.expert,
            name=manifest.name,
        )

    description = manifest.display_description
    zh_description = description if isinstance(description, str) else description.zh
    if not 40 <= len(zh_description) <= 50:
        logger.warning(
            "Expert %s displayDescription.zh is %s chars (recommended 40–50)",
            manifest.name,
            len(zh_description),
        )

    avatar = Path(manifest.avatar)
    if avatar.is_absolute() or ".." in avatar.parts:
        _raise_invalid("avatar", "avatar must be a package-relative path", avatar=manifest.avatar)

    for field_name, values in (
        ("tags", manifest.tags),
        ("quickPrompts", manifest.quick_prompts),
    ):
        if values is None or len(values) != 3:
            _raise_invalid(field_name, f"{field_name} must contain exactly 3 items")
    if manifest.quick_prompts is None or manifest.default_init_prompt != manifest.quick_prompts[0]:
        _raise_invalid(
            "defaultInitPrompt",
            "defaultInitPrompt must equal the first quickPrompts item",
        )

    if manifest.expert_type != "team":
        return
    if manifest.team_info is None:
        _raise_invalid("teamInfo", "Team expert must declare teamInfo")
    if manifest.members is None:
        _raise_invalid("members", "Team expert must declare members")

    team_info = manifest.team_info
    members = manifest.members
    lead = team_info.lead_agent
    if lead != manifest.agent_name:
        _raise_invalid(
            "teamInfo.leadAgent",
            "teamInfo.leadAgent must equal agentName",
            lead_agent=lead,
            agent_name=manifest.agent_name,
        )
    if manifest.profession != manifest.display_name:
        _raise_invalid(
            "profession",
            "Team profession must equal displayName",
            profession=manifest.profession.model_dump(),
            display_name=manifest.display_name.model_dump(),
        )
    member_ids = [member.id for member in members]
    expected_agent_ids = [lead, *team_info.member_agents]
    if len(expected_agent_ids) != len(set(expected_agent_ids)):
        _raise_invalid(
            "teamInfo.memberAgents",
            "Team member IDs must be unique and exclude the lead",
            lead_agent=lead,
        )
    if len(member_ids) != len(set(member_ids)):
        _raise_invalid("members", "Team member IDs must be unique", member_ids=member_ids)

    missing_members = sorted(set(expected_agent_ids) - set(member_ids))
    unexpected_members = sorted(set(member_ids) - set(expected_agent_ids))
    if missing_members or unexpected_members:
        _raise_invalid(
            "members",
            "Team members must exactly match teamInfo leadAgent/memberAgents",
            missing_members=missing_members,
            unexpected_members=unexpected_members,
        )
    for member in members:
        expected_role = "lead" if member.id == lead else "member"
        if member.role != expected_role:
            _raise_invalid(
                "members",
                "Team member roles must match teamInfo.leadAgent",
                member_id=member.id,
                expected_role=expected_role,
                actual_role=member.role,
            )


def _validate_agent_file(path: Path) -> None:
    # Keep the subagent package out of the expert-loader import graph.
    from prismclaw.engine.subagent.definition.manifest import AgentManifest

    try:
        content = path.read_text(encoding="utf-8")
        frontmatter_text, body = _extract_frontmatter(content)
        loaded = yaml.safe_load(frontmatter_text)
    except Exception as exc:
        _raise_invalid(
            "agents",
            f"Invalid Agent MD {path}: {exc}",
            file_path=str(path),
        )

    if not isinstance(loaded, dict):
        _raise_invalid(
            "agents",
            f"Agent MD frontmatter must be a mapping: {path}",
            file_path=str(path),
        )
    frontmatter: dict[str, Any] = loaded
    tools_fields = sorted(_AGENT_TOOLS_KEYS & frontmatter.keys())
    if tools_fields:
        raise ExpertError(
            ExpertErrorCode.MANIFEST_AGENT_TOOLS_FORBIDDEN,
            f"Expert Agent MD must not declare tools: {path}",
            {"file_path": str(path), "fields": tools_fields},
        )
    for alias, canonical in _AGENT_FIELD_ALIASES.items():
        if alias in frontmatter:
            _raise_invalid(
                f"agents.{alias}",
                f"Agent MD field {alias!r} is not supported; use {canonical!r}",
                file_path=str(path),
                field_name=alias,
                canonical_field=canonical,
            )
    if frontmatter.get("name") != path.stem:
        _raise_invalid(
            "agents.name",
            "Agent MD frontmatter name must equal its filename",
            file_path=str(path),
            expected_name=path.stem,
            actual_name=frontmatter.get("name"),
        )
    if not body.strip():
        _raise_invalid(
            "agents",
            "Agent MD persona body must not be empty",
            file_path=str(path),
        )
    try:
        AgentManifest.model_validate({**frontmatter, "system_prompt": body})
    except ValidationError as exc:
        raise ExpertError(
            ExpertErrorCode.MANIFEST_INVALID,
            f"Invalid Agent MD manifest: {path}",
            _validation_details(exc, file_path=path),
        ) from exc

    token_count = estimate_tokens(body)
    if token_count > EXPERT_AGENT_MAX_TOKENS:
        raise ExpertError(
            ExpertErrorCode.MANIFEST_AGENT_TOO_LONG,
            f"Expert Agent MD exceeds {EXPERT_AGENT_MAX_TOKENS} tokens: {path}",
            {
                "file_path": str(path),
                "estimated_tokens": token_count,
                "maximum_tokens": EXPERT_AGENT_MAX_TOKENS,
            },
        )
    if token_count > EXPERT_AGENT_TARGET_TOKENS:
        logger.warning(
            "Expert Agent MD is %s tokens (target <= %s): %s",
            token_count,
            EXPERT_AGENT_TARGET_TOKENS,
            path,
        )


def validate_expert_package(install_path: Path, raw: dict[str, Any]) -> ExpertPackageManifest:
    """校验一个完整专家包；任一规则失败即抛出结构化 ``ExpertError``。"""
    _validate_forbidden_declarations(install_path, raw)
    manifest_path = install_path / ".plugin" / "plugin.json"
    try:
        manifest = ExpertPackageManifest.model_validate(raw)
    except ValidationError as exc:
        raise ExpertError(
            ExpertErrorCode.MANIFEST_INVALID,
            f"Invalid expert manifest: {manifest_path}",
            _validation_details(exc, file_path=manifest_path),
        ) from exc

    _validate_display_and_team(manifest)

    expected_agents = [manifest.agent_name]
    if manifest.team_info is not None:
        expected_agents.extend(manifest.team_info.member_agents)
    for agent_name in expected_agents:
        agent_path = install_path / "agents" / f"{agent_name}.md"
        if not agent_path.is_file():
            _raise_invalid(
                "agentName" if agent_name == manifest.agent_name else "teamInfo.memberAgents",
                f"Required expert Agent MD is missing: {agent_path}",
                agent_name=agent_name,
                file_path=str(agent_path),
                reason="agent_missing",
            )

    agents_dir = install_path / "agents"
    for path in sorted(agents_dir.glob("*.md"), key=lambda item: item.name):
        _validate_agent_file(path)
    return manifest


__all__ = [
    "EXPERT_AGENT_MAX_TOKENS",
    "EXPERT_AGENT_TARGET_TOKENS",
    "ExpertLocalizedText",
    "ExpertPackageManifest",
    "ExpertMember",
    "ExpertTeamInfo",
    "validate_expert_package",
]
