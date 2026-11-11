"""插件清单与变量替换子包。"""

from prismclaw.engine.plugins.manifest.manifest import (
    CommandMetadata,
    PluginAuthor,
    PluginManifest,
    UserConfigOption,
    parse_manifest,
)
from prismclaw.engine.plugins.manifest.substitution import (
    PRISMCLAW_MARKETPLACE_DATA,
    PRISMCLAW_PLUGIN_DATA,
    PRISMCLAW_PLUGIN_ROOT,
    PRISMCLAW_USER_HOME,
    SKILL_DIR,
    USER_CONFIG_PREFIX,
    build_plugin_variables,
    substitute_user_config_in_content,
    substitute_variables,
)

__all__ = [
    "PRISMCLAW_MARKETPLACE_DATA",
    "PRISMCLAW_PLUGIN_DATA",
    "PRISMCLAW_PLUGIN_ROOT",
    "PRISMCLAW_USER_HOME",
    "SKILL_DIR",
    "USER_CONFIG_PREFIX",
    "CommandMetadata",
    "PluginAuthor",
    "PluginManifest",
    "UserConfigOption",
    "build_plugin_variables",
    "parse_manifest",
    "substitute_user_config_in_content",
    "substitute_variables",
]