"""Expert name resolution utilities.

Moved from contracts.services.skills to break the contracts→engine
dependency cycle. This is a runtime helper that operates on manifest
objects and belongs in the engine layer.
"""

from __future__ import annotations

from pathlib import Path as _Path
from typing import TYPE_CHECKING

from prismclaw.schema.agent.domains import SkillSource

if TYPE_CHECKING:
    from prismclaw.schema.services.skills import SkillManifest


def _resolve_expert_name(manifest: SkillManifest) -> str | None:
    """从专家包来源（SkillSource.EXPERT）的 SkillManifest 解析 expert 名。"""
    if SkillSource.coerce(getattr(manifest, "source", None)) is not SkillSource.EXPERT:
        return None
    raw = getattr(manifest, "loaded_from", None)
    if not raw or not isinstance(raw, str):
        return None
    p = _Path(raw)
    candidates: list[str] = []
    if p.parent.name and p.parent.name != ".plugin":
        candidates.append(p.parent.name)
    if p.parent.parent and p.parent.parent.name:
        candidates.append(p.parent.parent.name)
    if p.name:
        candidates.append(p.name)
    for c in candidates:
        if c and c not in {"plugin.json", ".plugin"}:
            return c
    return None
