from __future__ import annotations

from typing import TYPE_CHECKING

from prismclaw.engine.experts.codes import ExpertErrorCode
from prismclaw.engine.subagent.team_registry import (
    TeamErrorCode,
    TeamRegistration,
    TeamRegistry,
    TeamRegistryUnavailableError,
)

if TYPE_CHECKING:
    from prismclaw.engine.execution.capabilities.catalog import AgentCatalogEntry


class TeamGuardError(ValueError):
    def __init__(
        self,
        code: ExpertErrorCode | TeamErrorCode,
        *,
        team_name: str,
        agent_name: str,
        caller: str,
        reason: str,
    ) -> None:
        self.code = code
        self.team_name = team_name
        self.agent_name = agent_name
        self.caller = caller or "unknown"
        self.reason = reason
        super().__init__(f"{code.value}: {reason}")

    @property
    def metadata(self) -> dict[str, str]:
        return {
            "code": self.code.value,
            "team_name": self.team_name,
            "agent_name": self.agent_name,
            "caller": self.caller,
            "reason": self.reason,
        }


async def guard_dispatch(
    caller_agent: str,
    agent_name: str,
    team_name: str,
    *,
    registry: TeamRegistry | None,
    lead_agent: str,
    entry: AgentCatalogEntry | None,
    is_member: bool = False,
) -> TeamRegistration:
    caller = caller_agent or "unknown"
    if is_member or caller != lead_agent:
        raise TeamGuardError(
            TeamErrorCode.TEAM_SPAWN_BY_MEMBER,
            team_name=team_name,
            agent_name=agent_name,
            caller=caller,
            reason="only the current team lead may dispatch members",
        )

    try:
        registration = await registry.get(team_name) if registry is not None else None
    except TeamRegistryUnavailableError as exc:
        raise TeamGuardError(
            ExpertErrorCode.TEAM_REGISTRY_UNAVAILABLE,
            team_name=team_name,
            agent_name=agent_name,
            caller=caller,
            reason="team registry is unavailable",
        ) from exc
    if registration is None or registration.lead != lead_agent:
        raise TeamGuardError(
            TeamErrorCode.TEAM_NOT_CREATED,
            team_name=team_name,
            agent_name=agent_name,
            caller=caller,
            reason="team is not registered in the current run",
        )
    if agent_name == lead_agent:
        raise TeamGuardError(
            TeamErrorCode.TEAM_LEAD_SELF_SPAWN,
            team_name=team_name,
            agent_name=agent_name,
            caller=caller,
            reason="the team lead cannot spawn itself",
        )
    if entry is None or entry.name != agent_name:
        raise TeamGuardError(
            TeamErrorCode.TEAM_MEMBER_UNKNOWN,
            team_name=team_name,
            agent_name=agent_name,
            caller=caller,
            reason="agent_name must exactly match a team member ID",
        )
    if (
        entry.expert_name != registration.expert_package
        or agent_name not in registration.members
    ):
        raise TeamGuardError(
            TeamErrorCode.TEAM_MEMBER_NOT_IN_PACKAGE,
            team_name=team_name,
            agent_name=agent_name,
            caller=caller,
            reason="agent is not a member of the current expert package",
        )
    return registration


__all__ = ["TeamGuardError", "guard_dispatch"]
