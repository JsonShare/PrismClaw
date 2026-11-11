"""Plugin dialect detection for ingest.

Probes a plugin directory to determine which ecosystem dialect it uses, so the
ingest pipeline can decide whether to skip it (already canonical) or convert it.
Detection is purely filesystem-based and side-effect free.

Priority (per SDD P2-T1):
    1. ``.plugin/plugin.json`` or root ``plugin.json``  → CANONICAL (skip)
    2. ``.trae-plugin/plugin.json``                            → TRAE (convert)
    3. bare ``skills/`` directory                              → BARE_SKILLS (wrap)
    4. otherwise                                               → UNKNOWN
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

__all__ = [
    "PluginDialect",
    "DialectDetection",
    "detect_dialect",
]


class PluginDialect(str, Enum):
    """Recognized plugin source dialects."""

    CANONICAL = "canonical"
    TRAE = "trae"
    BARE_SKILLS = "bare_skills"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DialectDetection:
    """Result of probing a plugin directory."""

    dialect: PluginDialect
    manifest_path: Path | None = None
    skills_dir: Path | None = None


def detect_dialect(plugin_dir: Path) -> DialectDetection:
    """Detect the dialect of ``plugin_dir``.

    Returns a :class:`DialectDetection` with the resolved dialect, the manifest
    path (for canonical/trae), and the skills directory (when one exists).
    """
    plugin_dir = Path(plugin_dir)

    canonical = plugin_dir / ".plugin" / "plugin.json"
    if canonical.is_file():
        return DialectDetection(PluginDialect.CANONICAL, manifest_path=canonical)
    root_manifest = plugin_dir / "plugin.json"
    if root_manifest.is_file():
        return DialectDetection(PluginDialect.CANONICAL, manifest_path=root_manifest)

    trae = plugin_dir / ".trae-plugin" / "plugin.json"
    if trae.is_file():
        return DialectDetection(
            PluginDialect.TRAE,
            manifest_path=trae,
            skills_dir=_skills_dir(plugin_dir),
        )

    skills = _skills_dir(plugin_dir)
    if skills is not None:
        return DialectDetection(PluginDialect.BARE_SKILLS, skills_dir=skills)

    return DialectDetection(PluginDialect.UNKNOWN)


def _skills_dir(plugin_dir: Path) -> Path | None:
    skills = plugin_dir / "skills"
    return skills if skills.is_dir() else None
