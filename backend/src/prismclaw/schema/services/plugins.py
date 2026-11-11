"""Plugin subsystem contracts.

Protocols for plugin trust checking and loading, plus shared plugin types.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class PluginTrustProtocol(Protocol):
    """Protocol for plugin trust decisions — duck-typing compatible with plugins.trust functions."""

    def is_source_trusted_for_load(
        self,
        source: str,
        *,
        workspace_trusted: bool,
        plugin_id: str | None = None,
        explicitly_trusted: set[str] | None = None,
    ) -> bool:
        """Check whether a plugin source is trusted for loading."""
        ...

    def trust_tier_for_source(self, source: str) -> str:
        """Return the trust tier for a given source."""
        ...


class PluginLoadProtocol(Protocol):
    """Protocol for plugin loading — duck-typing compatible with PluginLoadStrategy(ABC)."""

    def can_handle(self, install_path: Path) -> bool:
        """Check whether this strategy can handle the given install path."""
        ...

    async def load(
        self,
        plugin_id: str,
        install_path: Path,
        *,
        enabled: bool = True,
        plugin_configs: dict[str, dict[str, Any]] | None = None,
        data_base_dir: Path | None = None,
        porting_context: dict[str, Any] | None = None,
    ) -> tuple[Any | None, list[Any]]:
        """Load a plugin, returning (loaded_plugin | None, errors)."""
        ...


@dataclass(slots=True)
class PluginReloadSummary:
    """Summary of a plugin reload operation — zero prismclaw internal dependencies."""
    plugin_count: int
    command_count: int
    skill_count: int
    agent_count: int
    generation: int
