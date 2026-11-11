from __future__ import annotations

from pathlib import Path
from typing import Any

from prismclaw.engine.subagent.definition.parser import parse_agent_md
from prismclaw.engine.subagent.definition.registry import SubAgentRegistry
from prismclaw.engine.plugins.loading.agent_loader import PluginAgentLoader
from prismclaw.infra.settings import SubAgentSettings


class DefaultPluginAgentLoader(PluginAgentLoader):
    def __init__(self, *, settings: SubAgentSettings | None = None) -> None:
        self._settings = settings or SubAgentSettings()

    def scan_agents_dir(
        self,
        *,
        workspace_path: Path,
        agents_path: Path,
        source: str,
    ) -> list[Any]:
        registry = SubAgentRegistry(
            settings=self._settings,
            workspace_path=workspace_path,
        )
        return list(registry._scan_layer(agents_path, source=source))

    def load_agent_file(
        self,
        *,
        agent_path: Path,
        source: str,
    ) -> Any:
        agent = parse_agent_md(agent_path).model_copy(update={"source": source})
        return agent

