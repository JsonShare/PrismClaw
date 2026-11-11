from __future__ import annotations

from typing import Any

from prismclaw.schema.content.context import ContextBlock
from prismclaw.schema.content.evidence import ContextGraph, EvidencePacket

# NOTE (SDD context-governance S3a/S3b, 2026-08-21): 注入区块加"事实引用
# 指令行"的方案已被消融实验否决 — 真实系统 A/B 各 3 轮, Kafka 陈述
# 0/6, 指令无任何增益。根因不是转述纪律, 而是同主题冲突条目(W4): 库中
# 同时存在"含 Kafka"与"不含 Kafka"两版技术栈事实, 模型面对冲突对差异
# 项弃权(极简对照: 单版本注入 3/3 正确陈述 Kafka)。修复走 W4 提取纪律
# + 冲突条目收敛, 不走注入侧 prompt。


class MemoryFormatter:
    @staticmethod
    def format_sections(
        l2l3_results: list[Any],
        archive_results: list[Any],
    ) -> list[tuple[str, str]]:
        l2_entries = [r for r in l2l3_results if getattr(r, "layer", "") == "L2"]
        l3_entries = [r for r in l2l3_results if getattr(r, "layer", "") == "L3"]
        if not l2_entries and not l3_entries and not archive_results:
            return []
        sections: list[tuple[str, str]] = []
        if l2_entries:
            lines = ["<memory_facts>", "<facts>"]
            for entry in l2_entries:
                lines.append(
                    f"- [{getattr(entry, 'type', 'general')}] {getattr(entry, 'content', '')}"
                )
            lines.extend(["</facts>", "</memory_facts>"])
            sections.append(("memory_facts", "\n".join(lines)))
        if l3_entries:
            lines = ["<memory_procedures>", "<procedures>"]
            for entry in l3_entries:
                entry_id = getattr(entry, "id", "")[:8]
                lines.append(
                    f"- [{getattr(entry, 'type', 'general')}|{entry_id}] "
                    f"{getattr(entry, 'content', '')}"
                )
            lines.extend(["</procedures>", "</memory_procedures>"])
            sections.append(("memory_procedures", "\n".join(lines)))
        if archive_results:
            lines = ["<memory_archives>", "<archives>"]
            for entry in archive_results:
                archive_kind = _archive_kind_label(entry)
                sid_short = getattr(entry, "session_id", "").split(":")[-1][:12]
                similarity = getattr(entry, "similarity", None)
                score = getattr(entry, "score", None)
                sim_str = (
                    f"{similarity:.2f}"
                    if isinstance(similarity, (int, float)) and not isinstance(similarity, bool)
                    else "?"
                )
                score_str = (
                    f"{score:.2f}"
                    if isinstance(score, (int, float)) and not isinstance(score, bool)
                    else "?"
                )
                summary = getattr(entry, "summary", "")
                graph_group_id = getattr(entry, "graph_group_id", None)
                payload = getattr(entry, "payload", {}) if isinstance(getattr(entry, "payload", {}), dict) else {}
                payload_hint = _artifact_payload_hint(payload)
                if archive_kind:
                    extra = f"|group={str(graph_group_id)[:10]}" if graph_group_id else ""
                    hint = f" {payload_hint}" if payload_hint else ""
                    lines.append(
                        f"- [kind={archive_kind}|session={sid_short}|sim={sim_str}|score={score_str}{extra}] {summary}{hint}"
                    )
                else:
                    lines.append(f"- [session={sid_short}|sim={sim_str}] {summary}")
            lines.extend(["</archives>", "</memory_archives>"])
            sections.append(("memory_archives", "\n".join(lines)))
        return sections

    @staticmethod
    def format_packet_sections(
        packets: list[EvidencePacket],
    ) -> list[tuple[str, str]]:
        l2_entries = [
            packet
            for packet in packets
            if packet.kind == "memory" and str(packet.layer or "").upper() == "L2"
        ]
        l3_entries = [
            packet
            for packet in packets
            if packet.kind == "memory" and str(packet.layer or "").upper() == "L3"
        ]
        archive_entries = [
            packet for packet in packets if packet.kind in {"archive", "artifact"}
        ]
        if not l2_entries and not l3_entries and not archive_entries:
            return []
        sections: list[tuple[str, str]] = []
        if l2_entries:
            lines = ["<memory_facts>", "<facts>"]
            for packet in l2_entries:
                entry_type = packet.provenance.get("entry_type", "general")
                lines.append(f"- [{entry_type}] {packet.content}")
            lines.extend(["</facts>", "</memory_facts>"])
            sections.append(("memory_facts", "\n".join(lines)))
        if l3_entries:
            lines = ["<memory_procedures>", "<procedures>"]
            for packet in l3_entries:
                entry_id = str(packet.source_ref or packet.packet_id).split(":")[-1][:8]
                entry_type = packet.provenance.get("entry_type", "general")
                lines.append(f"- [{entry_type}|{entry_id}] {packet.content}")
            lines.extend(["</procedures>", "</memory_procedures>"])
            sections.append(("memory_procedures", "\n".join(lines)))
        if archive_entries:
            lines = ["<memory_archives>", "<archives>"]
            for packet in archive_entries:
                archive_kind = None
                if packet.kind == "artifact":
                    archive_kind = _normalize_archive_kind_label(
                        packet.metadata.get("artifact_kind")
                        or packet.provenance.get("artifact_kind")
                        or packet.title
                    )
                session_id = str(packet.provenance.get("session_id", "") or "")
                sid_short = session_id.split(":")[-1][:12]
                similarity = packet.metadata.get("similarity")
                score = packet.metadata.get("score")
                sim_str = (
                    f"{similarity:.2f}"
                    if isinstance(similarity, (int, float)) and not isinstance(similarity, bool)
                    else "?"
                )
                score_str = (
                    f"{score:.2f}"
                    if isinstance(score, (int, float)) and not isinstance(score, bool)
                    else "?"
                )
                graph_group_id = packet.metadata.get("graph_group_id")
                if archive_kind:
                    extra = f"|group={str(graph_group_id)[:10]}" if graph_group_id else ""
                    payload = packet.metadata.get("payload", {})
                    payload_hint = _artifact_payload_hint(payload) if isinstance(payload, dict) else ""
                    hint = f" {payload_hint}" if payload_hint else ""
                    lines.append(
                        f"- [kind={archive_kind}|session={sid_short}|sim={sim_str}|score={score_str}{extra}] {packet.content}{hint}"
                    )
                else:
                    lines.append(f"- [session={sid_short}|sim={sim_str}] {packet.content}")
            lines.extend(["</archives>", "</memory_archives>"])
            sections.append(("memory_archives", "\n".join(lines)))
        return sections

    @classmethod
    def format_blocks(
        cls,
        l2l3_results: list[Any],
        archive_results: list[Any],
    ) -> list[ContextBlock]:
        sections = cls.format_sections(l2l3_results, archive_results)
        blocks: list[ContextBlock] = []
        for name, text in sections:
            priority = "medium"
            if name == "memory_facts":
                priority = "high"
            elif name == "memory_archives":
                priority = "low"
            blocks.append(
                ContextBlock(
                    id=name,
                    zone="dynamic",
                    source=name,
                    content=text,
                    stability="volatile",
                    priority=priority,  # type: ignore[arg-type]
                    cacheable=False,
                    truncatable=True,
                    metadata={"wrapper": "memory_context"},
                )
            )
        return blocks

    @classmethod
    def format_blocks_from_packets(
        cls,
        packets: list[EvidencePacket],
        *,
        graph: ContextGraph | None = None,
    ) -> list[ContextBlock]:
        sections = cls.format_packet_sections(packets)
        blocks: list[ContextBlock] = []
        graph_meta = (
            {
                "graph_id": graph.graph_id,
                "packet_count": len(graph.packets),
                "edge_count": len(graph.edges),
            }
            if graph is not None
            else None
        )
        for name, text in sections:
            priority = "medium"
            if name == "memory_facts":
                priority = "high"
            elif name == "memory_archives":
                priority = "low"
            packet_ids = [
                packet.packet_id
                for packet in packets
                if (name == "memory_facts" and packet.kind == "memory" and str(packet.layer or "").upper() == "L2")
                or (name == "memory_procedures" and packet.kind == "memory" and str(packet.layer or "").upper() == "L3")
                or (name == "memory_archives" and packet.kind in {"archive", "artifact"})
            ]
            blocks.append(
                ContextBlock(
                    id=name,
                    zone="dynamic",
                    source=name,
                    content=text,
                    stability="volatile",
                    priority=priority,  # type: ignore[arg-type]
                    cacheable=False,
                    truncatable=True,
                    metadata={
                        "wrapper": "memory_context",
                        "evidence_packet_ids": packet_ids,
                        "context_graph": graph_meta,
                    },
                )
            )
        return blocks

    @staticmethod
    def build_memory_context_from_sections(
        sections: list[tuple[str, str]],
    ) -> str | None:
        if not sections:
            return None
        lines = ["<memory_context>"]
        for _name, text in sections:
            lines.append(text)
        lines.append("</memory_context>")
        return "\n".join(lines)

    @classmethod
    def format_context(
        cls,
        l2l3_results: list[Any],
        archive_results: list[Any],
    ) -> str | None:
        sections = cls.format_sections(l2l3_results, archive_results)
        return cls.build_memory_context_from_sections(sections)


def _archive_kind_label(entry: Any) -> str | None:
    artifact_kind = str(getattr(entry, "artifact_kind", "") or "").strip().lower()
    if artifact_kind:
        return _normalize_archive_kind_label(artifact_kind)
    session_id = str(getattr(entry, "session_id", "") or "")
    marker = ":dream:rem:"
    if marker not in session_id:
        return None
    suffix = session_id.split(marker, 1)[1]
    kind = suffix.split(":", 1)[0].strip().lower()
    normalized_kind = _normalize_archive_kind_label(kind)
    return normalized_kind or "rem"


def _normalize_archive_kind_label(value: Any) -> str | None:
    artifact_kind = str(value or "").strip().lower()
    if not artifact_kind:
        return None
    if artifact_kind in {"archive", "pattern", "graph", "relation"}:
        return f"rem-{artifact_kind}"
    return artifact_kind


def _artifact_payload_hint(payload: dict[str, Any]) -> str:
    keys = []
    for candidate in ("entities", "patterns", "relations", "topics", "open_questions"):
        value = payload.get(candidate)
        if isinstance(value, list) and value:
            keys.append(f"{candidate}={len(value)}")
    return f"({' '.join(keys)})" if keys else ""
