"""Skill management protocol — for resolving, listing, and reloading skills."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SkillManagementProtocol(Protocol):
    """Protocol for the skill management facade."""
    async def resolve(self, name: str) -> Any | None: ...
    async def reload(self) -> list[str]: ...
    async def list_skills(self) -> list[Any]: ...
