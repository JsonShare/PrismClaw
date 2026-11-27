"""专家包清单与变量替换子包。"""

from prismclaw.engine.experts.manifest.manifest import (
    CommandMetadata,
    ExpertAuthor,
    ExpertManifest,
    parse_manifest,
)
from prismclaw.engine.experts.manifest.substitution import (
    PRISMCLAW_PLUGIN_DATA,
    PRISMCLAW_PLUGIN_ROOT,
    PRISMCLAW_USER_HOME,
    SKILL_DIR,
    build_expert_variables,
    substitute_variables,
)

__all__ = [
    "PRISMCLAW_PLUGIN_DATA",
    "PRISMCLAW_PLUGIN_ROOT",
    "PRISMCLAW_USER_HOME",
    "SKILL_DIR",
    "CommandMetadata",
    "ExpertAuthor",
    "ExpertManifest",
    "build_expert_variables",
    "parse_manifest",
    "substitute_variables",
]
