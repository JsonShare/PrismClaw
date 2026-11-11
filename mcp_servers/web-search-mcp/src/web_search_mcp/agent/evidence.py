"""search_evidence metadata builder.

Field names intentionally mirror the PrismClaw SearchEvidence contract so the
engine's MCP adapter and the existing frontend evidence card stay compatible.
"""

from __future__ import annotations

from typing import Any


def build_search_evidence(
    *,
    query: str,
    results: list[dict[str, Any]],
    diagnostics: dict[str, str],
) -> dict[str, Any]:
    usable = sum(1 for item in results if item.get("content_status") != "snippet_only")
    extractable = sum(1 for item in results if item.get("url"))
    return {
        "query": query,
        "search_hit_count": len(results),
        "usable_source_count": usable,
        "extractable_source_count": extractable,
        "provider_statuses": dict(diagnostics),
        "has_public_evidence": bool(results),
    }
