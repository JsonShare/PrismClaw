from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from prismclaw.engine.plugins.loading.agent_loader import PluginAgentLoader
from prismclaw.engine.subagent.definition.parser import parse_agent_md
from prismclaw.engine.subagent.definition.registry import SubAgentRegistry
from prismclaw.schema.config.config import SubAgentConfig

if TYPE_CHECKING:
    from prismclaw.engine.subagent.definition.manifest import AgentManifest


class DefaultPluginAgentLoader(PluginAgentLoader):
    def __init__(self, *, settings: SubAgentConfig | None = None) -> None:
        self._settings = settings or SubAgentConfig()

    def scan_agents_dir(
        self,
        *,
        workspace_path: Path,
        agents_path: Path,
        source: str,
    ) -> list[AgentManifest]:
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
    ) -> AgentManifest:
        agent = parse_agent_md(agent_path).model_copy(update={"source": source})
        return agent
