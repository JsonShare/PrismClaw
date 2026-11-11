from __future__ import annotations

import hashlib
import json
from typing import Any

from prismclaw.engine.execution.capabilities.catalog import (
    CapabilityCatalogService,
)
from prismclaw.engine.state import ToolDiscoveryLedger
from prismclaw.engine.utils.xml import xml_escape
from prismclaw.schema.content.context import ContextBlock


def build_capability_delta_blocks(
    *,
    capability_catalog: CapabilityCatalogService | None,
    discovered_tool_ledger: ToolDiscoveryLedger | None,
) -> list[ContextBlock]:
    """Build the per-turn capability delta blocks.

    The only genuinely per-turn capability delta is the tool listing: deferred
    tools become visible once unlocked by discovery. Skill and sub-agent
    discovery are already handled authoritatively in the frozen prompt by
    ``skills_section`` (marketplace-folded index) and ``subagents_section``
    (single source of truth), so they are no longer re-projected here.
    """
    if capability_catalog is None:
        return []

    blocks: list[ContextBlock] = []

    tool_block = _build_tool_listing_delta_block(
        capability_catalog=capability_catalog,
        discovered_tool_ledger=discovered_tool_ledger,
    )
    if tool_block is not None:
        blocks.append(tool_block)

    return blocks


def _build_tool_listing_delta_block(
    *,
    capability_catalog: CapabilityCatalogService,
    discovered_tool_ledger: ToolDiscoveryLedger | None,
) -> ContextBlock | None:
    if discovered_tool_ledger is None or not discovered_tool_ledger.records:
        return None

    discovered_by_name = {
        record.canonical_name: record
        for record in discovered_tool_ledger.records
    }
    unlocked_entries = [
        entry
        for entry in capability_catalog.list_tool_entries()
        if entry.deferred and entry.canonical_name in discovered_by_name
    ]
    if not unlocked_entries:
        return None

    payload_entries = [
        {
            "canonical_name": entry.canonical_name,
            "llm_name": entry.llm_name,
            "description": entry.description,
            "tool_class": entry.tool_class,
            "effect_class": entry.effect_class,
            "deferred": entry.deferred,
            "discovery": {
                "query": discovered_by_name[entry.canonical_name].query,
                "discovered_via": discovered_by_name[entry.canonical_name].discovered_via,
                "source_tool_name": discovered_by_name[entry.canonical_name].source_tool_name,
                "tool_call_id": discovered_by_name[entry.canonical_name].tool_call_id,
                "discovery_order": discovered_by_name[entry.canonical_name].discovery_order,
            },
        }
        for entry in unlocked_entries
    ]
    digest = _projection_digest(payload_entries)
    content_lines = [
        "<tool_listing_delta>",
        "发现机制解锁的延迟工具，用于补充冻结的基线工具清单。",
        "",
    ]
    for entry in unlocked_entries:
        content_lines.append("  <tool>")
        content_lines.append(f"    <name>{xml_escape(entry.llm_name)}</name>")
        if entry.description:
            content_lines.append(
                f"    <description>{xml_escape(entry.description)}</description>"
            )
        discovery = discovered_by_name[entry.canonical_name]
        if discovery.query:
            content_lines.append(f"    <query>{xml_escape(discovery.query)}</query>")
        content_lines.append(
            f"    <discovered_via>{xml_escape(discovery.discovered_via)}</discovered_via>"
        )
        content_lines.append(f"    <tool_class>{xml_escape(entry.tool_class)}</tool_class>")
        content_lines.append(
            f"    <effect_class>{xml_escape(entry.effect_class)}</effect_class>"
        )
        content_lines.append("  </tool>")
    content_lines.append("</tool_listing_delta>")

    return ContextBlock(
        id="tool_listing_delta",
        zone="dynamic",
        source="tool_listing_delta",
        content="\n".join(content_lines),
        stability="volatile",
        priority="high",
        cacheable=False,
        truncatable=True,
        replaceable=True,
        state_payload={
            "kind": "tool_listing_delta",
            "digest": digest,
            "entries": payload_entries,
        },
        metadata={
            "source_kind": "attachment",
            "attachment_kind": "tool_listing_delta",
            "projection_kind": "capability_delta",
            "digest": digest,
        },
    )


def _projection_digest(entries: list[dict[str, Any]]) -> str:
    payload = json.dumps(entries, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()