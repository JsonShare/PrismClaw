"""Expert ingest adapters.

Home of the path-remap adapter (``path_remap``), which rewrites foreign-dialect
expert content into PrismClaw's canonical form while producing rewritten copies
(originals are never modified).
"""

from __future__ import annotations

from .path_remap import PathRemapRule, apply_path_remap, parse_remap_rules

__all__ = [
    "PathRemapRule",
    "apply_path_remap",
    "parse_remap_rules",
]
