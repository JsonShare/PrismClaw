"""Expert subsystem contracts.

Protocols for expert loading, plus shared expert types.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class ExpertLoadProtocol(Protocol):
    """Protocol for expert loading — duck-typing compatible with LoadStrategy(ABC)."""

    def can_handle(self, install_path: Path) -> bool:
        """Check whether this strategy can handle the given install path."""
        ...

    async def load(
        self,
        expert_id: str,
        install_path: Path,
        *,
        enabled: bool = True,
        data_base_dir: Path | None = None,
    ) -> tuple[Any | None, list[Any]]:
        """Load a expert, returning (loaded_expert | None, errors)."""
        ...


@dataclass(slots=True)
class ExpertReloadSummary:
    """Summary of a expert reload operation — zero prismclaw internal dependencies."""

    expert_count: int
    command_count: int
    skill_count: int
    agent_count: int
    generation: int
