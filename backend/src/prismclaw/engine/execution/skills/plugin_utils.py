"""Plugin name resolution utilities.

Moved from contracts.services.skills to break the contracts→engine
dependency cycle. This is a runtime helper that operates on manifest
objects and belongs in the engine layer.
"""
from __future__ import annotations

from pathlib import Path as _Path
from typing import Any


def _resolve_plugin_name(manifest: Any) -> str | None:
    """从 source=='plugin' 的 SkillManifest 解析 plugin 名 (执业档案 key)。"""
    if getattr(manifest, "source", None) != "plugin":
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
