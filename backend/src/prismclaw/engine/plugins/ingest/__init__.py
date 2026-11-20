"""Plugin ingest adapters.

Transforms foreign-dialect plugin content into PrismClaw's canonical form
during *ingest* (producing rewritten copies; originals are never modified).
Home of the path-remap adapter (``path_remap``) and the dialect detection /
ingest pipeline.
"""
from __future__ import annotations

from .detect import DialectDetection, PluginDialect, detect_dialect
from .path_remap import PathRemapRule, apply_path_remap, parse_remap_rules
from .pipeline import IngestError, IngestResult, ingest_plugin

__all__ = [
    # detect
    "DialectDetection",
    "PluginDialect",
    "detect_dialect",
    # path_remap
    "PathRemapRule",
    "apply_path_remap",
    "parse_remap_rules",
    # pipeline
    "IngestError",
    "IngestResult",
    "ingest_plugin",
]
