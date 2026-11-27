"""configs/mcp.json 的唯一连接器注册表视图。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from prismclaw.schema.services.mcp_settings import McpServerConfig, McpSettings

DEFAULT_MCP_CONFIG_PATH = Path(__file__).resolve().parents[4] / "configs" / "mcp.json"


class ConnectorRegistry:
    """configs/mcp.json 的只读视图；唯一启用判定来源。"""

    def __init__(self, *, path: Path | None = None) -> None:
        self._path = Path(path) if path is not None else DEFAULT_MCP_CONFIG_PATH
        self._servers = self._load()

    def _load(self) -> dict[str, McpServerConfig]:
        path = self._path
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError(f"MCP connector registry not found: {path}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid MCP connector registry {path}: {exc}") from exc

        if not isinstance(raw, dict):
            raise ValueError(f"MCP connector registry {path} must be a JSON object")
        unknown = set(raw) - {"connectors"}
        if unknown:
            raise ValueError(
                f"MCP connector registry {path} has unsupported top-level keys: "
                f"{sorted(unknown)}"
            )
        connectors = raw.get("connectors", {})
        if not isinstance(connectors, dict):
            raise ValueError(f"MCP connector registry {path} 'connectors' must be an object")

        servers: dict[str, McpServerConfig] = {}
        for name, value in connectors.items():
            if not isinstance(value, dict):
                raise ValueError(
                    f"MCP connector registry {path} entry {name!r} must be an object"
                )
            try:
                servers[str(name)] = McpServerConfig.model_validate({**value})
            except ValueError as exc:
                raise ValueError(
                    f"invalid MCP connector registry {path} entry {name!r}: {exc}"
                ) from exc
        try:
            McpSettings(enabled=bool(servers), servers=servers)
        except ValueError as exc:
            raise ValueError(f"invalid MCP connector registry {path}: {exc}") from exc
        return servers

    @property
    def servers(self) -> dict[str, McpServerConfig]:
        return {name: cfg for name, cfg in self._servers.items() if cfg.enabled}

    @property
    def has_enabled(self) -> bool:
        return bool(self.servers)

    def settings(self) -> McpSettings:
        return McpSettings(enabled=self.has_enabled, servers=self.servers)


__all__ = ["DEFAULT_MCP_CONFIG_PATH", "ConnectorRegistry"]
