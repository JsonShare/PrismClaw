from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from prismclaw.schema.agent.capability import RuntimeCapabilities
from prismclaw.engine.plugins.registry.descriptors import PluginDescriptor


@dataclass(slots=True)
class DeclarativePluginRuntimeView:
    plugin_name: str
    mcp_servers: dict[str, Any]

    @classmethod
    def from_descriptor(cls, descriptor: PluginDescriptor) -> "DeclarativePluginRuntimeView":
        return cls(
            plugin_name=descriptor.plugin_name,
            mcp_servers=dict(descriptor.mcp_servers),
        )


@dataclass(slots=True)
class DeclarativePluginCapabilityView:
    plugin_name: str
    capability_graph: Any
    runtime_capabilities: RuntimeCapabilities | None = None

    @classmethod
    def from_descriptor(
        cls,
        descriptor: PluginDescriptor,
    ) -> "DeclarativePluginCapabilityView | None":
        if descriptor.capability_graph is None:
            return None
        return cls(
            plugin_name=descriptor.plugin_name,
            capability_graph=descriptor.capability_graph,
            runtime_capabilities=descriptor.runtime_capabilities,
        )


@dataclass(slots=True)
class HookExtensionView:
    plugin_name: str | None = None
    hooks: list[Any] = field(default_factory=list)
    hooks_config: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class HookBindingView:
    event_name: str
    handler: Any
    plugin_name: str | None = None
    source_kind: str | None = None
