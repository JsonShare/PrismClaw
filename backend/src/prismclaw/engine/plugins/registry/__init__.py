"""插件注册表子包。"""

from prismclaw.engine.plugins.registry.identity import (
    BUILTIN_SOURCE,
    LOCAL_SOURCE,
    MARKETPLACE_SOURCE,
    PLUGIN_ID_PATTERN,
    SESSION_SOURCE,
    VALID_SOURCES,
    PluginID,
    is_valid_plugin_id,
    is_valid_plugin_name,
    make_plugin_id,
    parse_plugin_identifier,
)
from prismclaw.engine.plugins.registry.descriptors import (
    AgentDescriptor,
    BaseDescriptor,
    CommandDescriptor,
    DescriptorKind,
    HookDescriptor,
    PluginDescriptor,
    SkillDescriptor,
)
from prismclaw.engine.plugins.registry.registry import DescriptorRegistry
from prismclaw.engine.plugins.registry.runtime_views import (
    DeclarativePluginCapabilityView,
    DeclarativePluginRuntimeView,
    HookBindingView,
    HookExtensionView,
)
from prismclaw.engine.plugins.registry.context import (
    AgentDraft,
    CommandDraft,
    PluginContext,
    SkillDraft,
)

__all__ = [
    "BUILTIN_SOURCE",
    "LOCAL_SOURCE",
    "MARKETPLACE_SOURCE",
    "PLUGIN_ID_PATTERN",
    "SESSION_SOURCE",
    "VALID_SOURCES",
    "AgentDescriptor",
    "AgentDraft",
    "BaseDescriptor",
    "CommandDescriptor",
    "CommandDraft",
    "DeclarativePluginCapabilityView",
    "DeclarativePluginRuntimeView",
    "DescriptorKind",
    "DescriptorRegistry",
    "HookBindingView",
    "HookDescriptor",
    "HookExtensionView",
    "PluginContext",
    "PluginDescriptor",
    "PluginID",
    "SkillDescriptor",
    "SkillDraft",
    "is_valid_plugin_id",
    "is_valid_plugin_name",
    "make_plugin_id",
    "parse_plugin_identifier",
]
