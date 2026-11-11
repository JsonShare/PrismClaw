from __future__ import annotations

from dataclasses import dataclass

from prismclaw.schema.services.skills import SkillManifest


@dataclass
class PreloadedSkillContext:
    manifests: list[SkillManifest]
    catalog_block: str
    instructions_block: str
    allowed_tools_limit: set[str] | None
