from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class PluginAgentLoader(Protocol):
    def scan_agents_dir(
        self,
        *,
        workspace_path: Path,
        agents_path: Path,
        source: str,
    ) -> list[Any]: ...

    def load_agent_file(
        self,
        *,
        agent_path: Path,
        source: str,
    ) -> Any: ...

