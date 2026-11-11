from __future__ import annotations

import hashlib
from typing import Any

from prismclaw.schema.content.context import ContextBlock
from prismclaw.schema.content.evidence import ContextGraph, ContextGraphEdge, EvidencePacket, EvidenceStrategy


def build_context_graph(
    *,
    evidence_strategy: EvidenceStrategy,
    anchor_blocks: list[ContextBlock],
    retrieval_packets: list[EvidencePacket],
) -> ContextGraph:
    anchor_packets = [_packet_from_anchor_block(block) for block in anchor_blocks]
    packets = [*anchor_packets, *retrieval_packets]
    edges: list[ContextGraphEdge] = []

    anchor_ids = [packet.packet_id for packet in anchor_packets]
    for packet in retrieval_packets:
        for anchor_id in anchor_ids:
            edges.append(
                ContextGraphEdge(
                    source_id=anchor_id,
                    target_id=packet.packet_id,
                    relation="anchors",
                    reason="retrieval_result_selected_from_current_context",
                )
            )
        for required in evidence_strategy.evidence_types:
            if _packet_supports_requirement(packet, required):
                edges.append(
                    ContextGraphEdge(
                        source_id=packet.packet_id,
                        target_id=anchor_ids[0] if anchor_ids else packet.packet_id,
                        relation="supports",
                        reason=f"supports_requirement:{required}",
                    )
                )

    group_index: dict[str, list[str]] = {}
    packet_ids = {packet.packet_id for packet in packets}
    for packet in retrieval_packets:
        group_key = str(packet.metadata.get("graph_group_id") or "").strip()
        if group_key:
            group_index.setdefault(group_key, []).append(packet.packet_id)
        for superseded in packet.metadata.get("supersedes", []):
            if isinstance(superseded, str) and superseded.strip():
                target_packet_id = f"memory:{superseded.strip()}"
                if target_packet_id in packet_ids:
                    edges.append(
                        ContextGraphEdge(
                            source_id=packet.packet_id,
                            target_id=target_packet_id,
                            relation="supersedes",
                            reason="memory_version_chain",
                        )
                    )
        for ref in packet.source_refs:
            if ref in packet_ids:
                edges.append(
                    ContextGraphEdge(
                        source_id=packet.packet_id,
                        target_id=ref,
                        relation="derived_from",
                        reason="source_ref_link",
                    )
                )

    for ids in group_index.values():
        for idx, source_id in enumerate(ids):
            for target_id in ids[idx + 1 :]:
                edges.append(
                    ContextGraphEdge(
                        source_id=source_id,
                        target_id=target_id,
                        relation="related_to",
                        reason="shared_graph_group",
                    )
                )

    return ContextGraph(
        graph_id=f"{evidence_strategy.strategy_id}:graph",
        strategy_id=evidence_strategy.strategy_id,
        packets=packets,
        edges=edges,
        metadata={
            "packet_count": len(packets),
            "edge_count": len(edges),
            "anchor_count": len(anchor_packets),
            "retrieval_count": len(retrieval_packets),
            "strategy": evidence_strategy.strategy,
        },
    )


def _packet_from_anchor_block(block: ContextBlock) -> EvidencePacket:
    packet_id = f"anchor:{block.id}"
    return EvidencePacket(
        packet_id=packet_id,
        kind="anchor",
        source=block.source,
        title=block.source.replace("_", " "),
        content=block.content,
        layer=None,
        source_ref=block.id,
        confidence=1.0,
        freshness="current",
        provenance={
            "origin": "context_block",
            "zone": block.zone,
            "stability": block.stability,
        },
        metadata=dict(block.metadata or {}),
    )


def context_graph_metadata(graph: ContextGraph) -> dict[str, Any]:
    packet_digest = hashlib.sha1(
        "|".join(packet.packet_id for packet in graph.packets).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "graph_id": graph.graph_id,
        "strategy_id": graph.strategy_id,
        "packet_count": len(graph.packets),
        "edge_count": len(graph.edges),
        "packet_digest": packet_digest,
    }


def _packet_supports_requirement(packet: EvidencePacket, requirement: str) -> bool:
    normalized = str(requirement or "").strip().lower()
    if not normalized:
        return False
    source = packet.source.lower()
    title = packet.title.lower()
    content = packet.content.lower()
    if normalized in source or normalized in title:
        return True
    if normalized == "tool_verification":
        return packet.kind == "memory" and str(packet.layer or "").upper() == "L3"
    if normalized == "grounding_evidence":
        return packet.kind in {"archive", "artifact"}
    if normalized == "code_and_design_context":
        return "fact" in title or "relation" in title or "graph" in title
    return normalized.replace("_", " ") in content
