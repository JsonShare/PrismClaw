"""Ingest pipeline: materialize a canonical plugin copy from any dialect.

Orchestrates :mod:`detect` + :mod:`trae_convert` to produce a plugin directory
that PrismClaw's declarative loader can consume directly (``.plugin/plugin.json`` + conventional ``skills/``). The source directory is never
modified; a copy is materialized under the chosen ``output_root``.

Dialect handling (per SDD P2):
    CANONICAL   → copied through unchanged (already loadable);
    TRAE        → copied, then a canonical manifest is generated from
                  ``.trae-plugin/plugin.json``;
    BARE_SKILLS → copied, then a minimal canonical manifest is synthesized;
    UNKNOWN     → rejected with ``IngestError``.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from prismclaw.engine.plugins.ingest.detect import (
    PluginDialect,
    detect_dialect,
)
from prismclaw.engine.plugins.ingest.trae_convert import (
    write_canonical_manifest,
)

__all__ = ["IngestError", "IngestResult", "ingest_plugin"]


class IngestError(Exception):
    """Raised when a directory cannot be ingested."""


@dataclass(frozen=True)
class IngestResult:
    """Outcome of a successful ingest."""

    source: Path
    output_dir: Path
    manifest_path: Path
    dialect: PluginDialect


def ingest_plugin(source_dir: Path, output_root: Path) -> IngestResult:
    """Materialize a canonical copy of ``source_dir`` under ``output_root``.

    The copy is named after the source directory's basename. Returns an
    :class:`IngestResult` describing where the canonical plugin was written.
    """
    source_dir = Path(source_dir)
    if not source_dir.is_dir():
        raise IngestError(f"Source is not a directory: {source_dir}")

    detection = detect_dialect(source_dir)
    if detection.dialect is PluginDialect.UNKNOWN:
        raise IngestError(
            f"无法识别插件方言（在以下位置未找到 plugin.json / .trae-plugin / "
            f"skills/）：{source_dir}"
        )

    output_root = Path(output_root)
    output_dir = output_root / source_dir.name
    if output_dir.resolve() == source_dir.resolve():
        raise IngestError("output_root must differ from the source directory")

    # Materialize the copy (source untouched). dirs_exist_ok allows re-ingest.
    if output_dir.exists():
        shutil.rmtree(output_dir)
    shutil.copytree(source_dir, output_dir)

    if detection.dialect is PluginDialect.CANONICAL:
        manifest_path = output_dir / (
            detection.manifest_path.relative_to(source_dir)
            if detection.manifest_path is not None
            else Path(".plugin/plugin.json")
        )
    elif detection.dialect is PluginDialect.TRAE:
        trae_manifest = json.loads(
            (output_dir / ".trae-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        manifest_path = write_canonical_manifest(trae_manifest, output_dir)
    else:  # BARE_SKILLS
        manifest_path = _write_bare_skills_manifest(output_dir)

    return IngestResult(
        source=source_dir,
        output_dir=output_dir,
        manifest_path=manifest_path,
        dialect=detection.dialect,
    )


def _write_bare_skills_manifest(plugin_dir: Path) -> Path:
    """Synthesize a minimal canonical manifest for a bare ``skills/`` plugin."""
    manifest = {"name": plugin_dir.name, "skills": "skills"}
    plugin_dir_plugin = plugin_dir / ".plugin"
    plugin_dir_plugin.mkdir(parents=True, exist_ok=True)
    out_path = plugin_dir_plugin / "plugin.json"
    out_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out_path
