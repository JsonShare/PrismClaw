from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

import redis.asyncio as aioredis

from prismclaw.engine.experts.manifest.expert_schema import (
    ExpertPackageManifest,
    validate_expert_package,
)

if TYPE_CHECKING:
    from prismclaw.engine.experts.loading.load_result import ExpertPackage
    from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext


class TeamErrorCode(StrEnum):
    TEAM_NOT_CREATED = "TEAM_NOT_CREATED"
    TEAM_MEMBER_NOT_IN_PACKAGE = "TEAM_MEMBER_NOT_IN_PACKAGE"
    TEAM_SPAWN_BY_MEMBER = "TEAM_SPAWN_BY_MEMBER"
    TEAM_LEAD_SELF_SPAWN = "TEAM_LEAD_SELF_SPAWN"
    TEAM_MEMBER_UNKNOWN = "TEAM_MEMBER_UNKNOWN"
    TEAM_NOT_ACTIVE = "TEAM_NOT_ACTIVE"
    TEAM_NAME_MISMATCH = "TEAM_NAME_MISMATCH"
    TEAM_PACKAGE_INVALID = "TEAM_PACKAGE_INVALID"


class TeamRegistryUnavailableError(RuntimeError):
    def __init__(self, operation: str, team_name: str) -> None:
        self.operation = operation
        self.team_name = team_name
        super().__init__(f"team registry Redis {operation} failed for {team_name!r}")


@dataclass(frozen=True, slots=True)
class TeamRegistration:
    team_name: str
    lead: str
    members: tuple[str, ...]
    expert_package: str

    def payload(self) -> dict[str, Any]:
        return {
            "team_name": self.team_name,
            "lead": self.lead,
            "members": list(self.members),
            "expert_package": self.expert_package,
        }


@dataclass(frozen=True, slots=True)
class TeamRegistry:
    """Redis-backed registry scoped to one session run."""

    redis_client: aioredis.Redis | None
    session_id: str
    run_id: str
    ttl_seconds: int = 604_800
    key_prefix: str = "prismclaw:"

    @property
    def key(self) -> str:
        return f"{self.key_prefix}session:{{{self.session_id}}}:run:{self.run_id}:teams"

    def _client(self, operation: str, team_name: str) -> aioredis.Redis:
        if self.redis_client is None:
            raise TeamRegistryUnavailableError(operation, team_name)
        return self.redis_client

    @staticmethod
    def _serialize(registration: TeamRegistration) -> str:
        return json.dumps(
            {"version": 1, "registration": registration.payload()},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _deserialize(raw: str | bytes, team_name: str) -> TeamRegistration:
        document = json.loads(raw)
        if not isinstance(document, dict) or document.get("version") != 1:
            raise ValueError("unsupported team registration document")
        payload = document.get("registration")
        if not isinstance(payload, dict):
            raise ValueError("team registration payload is missing")
        members = payload.get("members")
        if (
            payload.get("team_name") != team_name
            or not isinstance(payload.get("lead"), str)
            or not isinstance(members, list)
            or not all(isinstance(member, str) for member in members)
            or not isinstance(payload.get("expert_package"), str)
        ):
            raise ValueError("team registration payload is invalid")
        return TeamRegistration(
            team_name=team_name,
            lead=payload["lead"],
            members=tuple(members),
            expert_package=payload["expert_package"],
        )

    async def register(
        self, registration: TeamRegistration
    ) -> tuple[TeamRegistration, bool]:
        team_name = registration.team_name
        client = self._client("register", team_name)
        try:
            async with client.pipeline(transaction=True) as pipe:
                pipe.hsetnx(self.key, team_name, self._serialize(registration))
                pipe.expire(self.key, self.ttl_seconds)
                pipe.hget(self.key, team_name)
                created, _expires, raw = await pipe.execute()
            return self._deserialize(raw, team_name), bool(created)
        except Exception as exc:
            raise TeamRegistryUnavailableError("register", team_name) from exc

    async def get(self, team_name: str) -> TeamRegistration | None:
        client = self._client("get", team_name)
        try:
            raw = await client.hget(self.key, team_name)
            if raw is None:
                return None
            return self._deserialize(raw, team_name)
        except Exception as exc:
            raise TeamRegistryUnavailableError("get", team_name) from exc

    async def delete(self, team_name: str) -> TeamRegistration | None:
        client = self._client("delete", team_name)
        try:
            async with client.pipeline(transaction=True) as pipe:
                pipe.hget(self.key, team_name)
                pipe.hdel(self.key, team_name)
                raw, deleted = await pipe.execute()
            if not deleted:
                return None
            if raw is None:
                raise ValueError("deleted team registration payload is missing")
            return self._deserialize(raw, team_name)
        except Exception as exc:
            raise TeamRegistryUnavailableError("delete", team_name) from exc


def get_team_registry(context: ToolContext, *, create: bool = False) -> TeamRegistry | None:
    registry = context.team_registry
    if (
        isinstance(registry, TeamRegistry)
        and registry.session_id == context.session_id
        and registry.run_id == context.request_id
    ):
        return registry
    if not create:
        return None
    store = context.session_store
    registry = TeamRegistry(
        redis_client=getattr(store, "redis_client", None),
        session_id=context.session_id,
        run_id=context.request_id,
        ttl_seconds=getattr(store, "ttl_seconds", 604_800),
        key_prefix=getattr(store, "key_prefix", "prismclaw:"),
    )
    context.team_registry = registry
    return registry


def load_team_manifest(expert: ExpertPackage) -> ExpertPackageManifest:
    root = Path(expert.root_path or expert.path)
    raw = json.loads((root / ".plugin" / "plugin.json").read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("team expert manifest must be a JSON object")
    manifest = validate_expert_package(root, raw)
    if manifest.expert_type != "team" or manifest.team_info is None:
        raise ValueError(f"expert package {expert.name!r} is not a team")
    return manifest


__all__ = [
    "TeamErrorCode",
    "TeamRegistration",
    "TeamRegistry",
    "TeamRegistryUnavailableError",
    "get_team_registry",
    "load_team_manifest",
]
