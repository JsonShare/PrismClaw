from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from prismclaw.engine.subagent.definition.manifest import AgentManifest


class ExpertAgentLoader(Protocol):
    def scan_agents_dir(
        self,
        *,
        workspace_path: Path,
        agents_path: Path,
        source: str,
    ) -> list[AgentManifest]: ...

    def load_agent_file(
        self,
        *,
        agent_path: Path,
        source: str,
    ) -> AgentManifest: ...
