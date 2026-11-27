from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prismclaw.engine.experts.codes import ExpertError, ExpertErrorCode
from prismclaw.engine.subagent.team_registry import (
    TeamErrorCode,
    TeamRegistration,
    TeamRegistryUnavailableError,
    get_team_registry,
    load_team_manifest,
)
from prismclaw.engine.toolkit.registry.tool_runtime import (
    ToolContext,
    error_result,
    text_result,
)
from prismclaw.schema.content.content_types import ToolResult

if TYPE_CHECKING:
    from prismclaw.engine.execution.capabilities.catalog import RuntimeCatalog
    from prismclaw.engine.experts.registry.expert_registry import ExpertRegistry


_PARAMETERS = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "当前团队专家包的精确名称。",
        }
    },
    "required": ["name"],
    "additionalProperties": False,
}


def _identity(context: ToolContext) -> tuple[str, str, str]:
    scope = context.session_scope
    team_name = (scope.team_name if scope is not None else None) or ""
    lead = (scope.lead_agent if scope is not None else None) or ""
    caller = context.subagent_name or lead or "unknown"
    return team_name, lead, caller


def _error(
    call_id: str,
    code: ExpertErrorCode | TeamErrorCode,
    *,
    team_name: str,
    agent_name: str,
    caller: str,
    reason: str,
) -> ToolResult:
    metadata = {
        "code": code.value,
        "team_name": team_name,
        "agent_name": agent_name,
        "caller": caller,
        "reason": reason,
    }
    return error_result(call_id, f"{code.value}: {reason}", metadata=metadata)


def _check_context(
    call_id: str,
    context: ToolContext,
    name: str,
) -> ToolResult | None:
    team_name, lead, caller = _identity(context)
    if not team_name or not lead:
        return _error(
            call_id,
            TeamErrorCode.TEAM_NOT_ACTIVE,
            team_name=name,
            agent_name="",
            caller=caller,
            reason="no team expert is active in the current run",
        )
    if (context.session_scope is not None and context.session_scope.member) or caller != lead:
        return _error(
            call_id,
            TeamErrorCode.TEAM_SPAWN_BY_MEMBER,
            team_name=team_name,
            agent_name="",
            caller=caller,
            reason="only the current team lead may manage the team registry",
        )
    if name != team_name:
        return _error(
            call_id,
            TeamErrorCode.TEAM_NAME_MISMATCH,
            team_name=team_name,
            agent_name="",
            caller=caller,
            reason="team name must exactly match the active expert package",
        )
    return None


class TeamCreateTool:
    name = "team_create"
    description = "在单次运行中注册当前团队及其清单成员；必须先注册，再通过 sub_agent 派发。"
    parameters = _PARAMETERS
    side_effect = True
    tool_class = "write"
    effect_class = "local_mutation"
    visibility_scope = "main_only"
    deferred = True

    def __init__(
        self,
        *,
        expert_registry: ExpertRegistry,
        capability_catalog: RuntimeCatalog,
    ) -> None:
        self._expert_registry = expert_registry
        self._capability_catalog = capability_catalog

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = str(args.get("_call_id") or "")
        raw_name = args.get("name")
        name = raw_name if isinstance(raw_name, str) else ""
        context_error = _check_context(call_id, context, name)
        if context_error is not None:
            return context_error
        team_name, lead, caller = _identity(context)
        return await self.register(
            call_id,
            context,
            team_name=team_name,
            lead=lead,
            caller=caller,
        )

    async def register(
        self,
        call_id: str,
        context: ToolContext,
        *,
        team_name: str,
        lead: str,
        caller: str,
    ) -> ToolResult:
        registry = get_team_registry(context, create=True)
        if registry is None:
            return _error(
                call_id,
                ExpertErrorCode.TEAM_REGISTRY_UNAVAILABLE,
                team_name=team_name,
                agent_name="",
                caller=caller,
                reason="team registry is unavailable",
            )
        try:
            existing = await registry.get(team_name)
        except TeamRegistryUnavailableError:
            return _error(
                call_id,
                ExpertErrorCode.TEAM_REGISTRY_UNAVAILABLE,
                team_name=team_name,
                agent_name="",
                caller=caller,
                reason="team registry is unavailable",
            )
        if existing is not None:
            return self._success(call_id, existing, created=False)

        expert = next(
            (
                candidate
                for candidate in self._expert_registry.list_experts()
                if candidate.name == team_name
                and candidate.manifest is not None
                and candidate.manifest.expert_type == "team"
            ),
            None,
        )
        if expert is None:
            return _error(
                call_id,
                TeamErrorCode.TEAM_PACKAGE_INVALID,
                team_name=team_name,
                agent_name="",
                caller=caller,
                reason="active team package is not deployed",
            )
        try:
            manifest = load_team_manifest(expert)
        except (ExpertError, OSError, ValueError) as exc:
            return _error(
                call_id,
                TeamErrorCode.TEAM_PACKAGE_INVALID,
                team_name=team_name,
                agent_name="",
                caller=caller,
                reason=str(exc),
            )

        team_info = manifest.team_info
        assert team_info is not None
        if manifest.name != team_name or team_info.lead_agent != lead:
            return _error(
                call_id,
                TeamErrorCode.TEAM_PACKAGE_INVALID,
                team_name=team_name,
                agent_name=lead,
                caller=caller,
                reason="team identity does not match teamInfo.leadAgent",
            )

        root = Path(expert.root_path or expert.path)
        for member in team_info.member_agents:
            if not (root / "agents" / f"{member}.md").is_file():
                return _error(
                    call_id,
                    TeamErrorCode.TEAM_MEMBER_NOT_IN_PACKAGE,
                    team_name=team_name,
                    agent_name=member,
                    caller=caller,
                    reason="declared team member has no Agent MD in the current package",
                )

        for agent_name in (lead, *team_info.member_agents):
            entry = self._capability_catalog.get_agent_entry(agent_name)
            if entry is None or entry.expert_name != team_name:
                return _error(
                    call_id,
                    TeamErrorCode.TEAM_MEMBER_NOT_IN_PACKAGE,
                    team_name=team_name,
                    agent_name=agent_name,
                    caller=caller,
                    reason="agent catalog entry does not belong to the current team package",
                )
        try:
            registration, created = await registry.register(
                TeamRegistration(
                    team_name=team_name,
                    lead=lead,
                    members=tuple(team_info.member_agents),
                    expert_package=team_name,
                )
            )
        except TeamRegistryUnavailableError:
            return _error(
                call_id,
                ExpertErrorCode.TEAM_REGISTRY_UNAVAILABLE,
                team_name=team_name,
                agent_name="",
                caller=caller,
                reason="team registry is unavailable",
            )
        return self._success(call_id, registration, created=created)

    @staticmethod
    def _success(
        call_id: str,
        registration: TeamRegistration,
        *,
        created: bool,
    ) -> ToolResult:
        payload = {
            "status": "created" if created else "existing",
            "created": created,
            **registration.payload(),
        }
        return text_result(
            call_id,
            json.dumps(payload, ensure_ascii=False),
            metadata=payload,
        )


class TeamDeleteTool:
    name = "team_delete"
    description = "只注销当前运行中的团队注册；不会终止成员，成员清理由 subagent_control 负责。"
    parameters = _PARAMETERS
    side_effect = True
    tool_class = "write"
    effect_class = "local_mutation"
    visibility_scope = "main_only"
    deferred = True

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = str(args.get("_call_id") or "")
        raw_name = args.get("name")
        name = raw_name if isinstance(raw_name, str) else ""
        context_error = _check_context(call_id, context, name)
        if context_error is not None:
            return context_error
        team_name, _lead, caller = _identity(context)
        registry = get_team_registry(context)
        try:
            registration = await registry.delete(team_name) if registry is not None else None
        except TeamRegistryUnavailableError:
            return _error(
                call_id,
                ExpertErrorCode.TEAM_REGISTRY_UNAVAILABLE,
                team_name=team_name,
                agent_name="",
                caller=caller,
                reason="team registry is unavailable",
            )
        if registration is None:
            return _error(
                call_id,
                TeamErrorCode.TEAM_NOT_CREATED,
                team_name=team_name,
                agent_name="",
                caller=caller,
                reason="team is not registered in the current run",
            )
        payload = {"status": "deleted", **registration.payload()}
        return text_result(
            call_id,
            json.dumps(payload, ensure_ascii=False),
            metadata=payload,
        )


__all__ = ["TeamCreateTool", "TeamDeleteTool"]
