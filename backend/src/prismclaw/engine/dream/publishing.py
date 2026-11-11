from __future__ import annotations

import hashlib
import time
from typing import Any

from prismclaw.engine.dream.candidates import DreamMemoryCandidate
from prismclaw.engine.dream.models import DreamPublishableArtifact, DreamPublishableMemory
from prismclaw.schema.memory.memory_types import L4ArtifactEntry, MemoryEntry


def build_publishable_memory(
    candidate: DreamMemoryCandidate,
    extracted: dict[str, Any],
    *,
    overrides: dict[str, Any] | None = None,
) -> DreamPublishableMemory:
    payload = dict(extracted)
    if overrides:
        payload.update(overrides)
    layer = str(payload.get("layer", "L2")).upper()
    memory_type = str(payload.get("type", "fact") or "fact")
    supersedes_raw = payload.get("supersedes")
    supersedes = [str(item) for item in supersedes_raw] if isinstance(supersedes_raw, list) else []
    return DreamPublishableMemory(
        layer=layer,  # type: ignore[arg-type]
        memory_kind=infer_memory_kind(payload.get("memory_kind"), layer=layer, type_=memory_type),  # type: ignore[arg-type]
        memory_type=memory_type,
        content=str(payload.get("content", "") or "").strip(),
        session_id=candidate.session_id,
        session_key=candidate.session_key,
        scope=preferred_str(payload.get("scope")) or None,
        entity_key=preferred_str(payload.get("entity_key")) or None,
        confidence=coerce_confidence(payload.get("confidence"), 0.75),
        evidence_count=max(
            1,
            coerce_non_negative_int(payload.get("evidence_count"), 0),
            len(candidate.source_entry_ids),
            coerce_non_negative_int(candidate.metadata.get("message_count"), 0),
        ),
        prerequisites=normalize_steps(payload.get("prerequisites")),
        steps=normalize_steps(payload.get("steps")),
        source_refs=build_source_refs(candidate),
        supersedes=supersedes,
        graph_group_id=preferred_str(payload.get("graph_group_id")) or None,
        version_chain_id=preferred_str(payload.get("version_chain_id")) or None,
        valid_from=_parse_optional_float(payload.get("valid_from")),
        valid_to=_parse_optional_float(payload.get("valid_to")),
        winner_flag=bool(payload.get("winner_flag", True)),
        publish_state=str(payload.get("publish_state", "reviewed") or "reviewed"),  # type: ignore[arg-type]
        origin_stage=str(payload.get("origin_stage", "candidate_merge")),
    )


def build_memory_entry_from_publishable(publishable: DreamPublishableMemory) -> MemoryEntry:
    now = time.time()
    content = build_structured_content(
        {
            "content": publishable.content,
            "memory_kind": publishable.memory_kind,
            "layer": publishable.layer,
            "type": publishable.memory_type,
            "prerequisites": publishable.prerequisites,
            "steps": publishable.steps,
        }
    )
    digest = hashlib.sha1(
        (
            f"{publishable.session_id}|{publishable.layer}|{publishable.memory_type}|"
            f"{publishable.memory_kind}|{content}"
        ).encode("utf-8")
    ).hexdigest()
    confidence = coerce_confidence(publishable.confidence, 0.75)
    return MemoryEntry(
        id=f"dream:{digest[:16]}",
        layer=publishable.layer,  # type: ignore[arg-type]
        type=publishable.memory_type,
        content=content,
        scope=publishable.scope,
        entity_key=publishable.entity_key,
        source_session_id=publishable.session_id,
        source_refs=list(publishable.source_refs),
        created_at=now,
        updated_at=now,
        status="active" if publishable.publish_state != "rejected" else "rejected",
        origin_stage=publishable.origin_stage,
        confidence=confidence,
        stability_score=max(confidence, 0.7),
        last_validated_at=now,
        graph_group_id=publishable.graph_group_id,
        version_chain_id=publishable.version_chain_id,
        publish_state=publishable.publish_state,
        valid_from=publishable.valid_from or now,
        valid_to=publishable.valid_to,
        winner_flag=bool(publishable.winner_flag),
        supersedes=list(publishable.supersedes),
    )


def append_unique_ref(refs: list[str], ref: str) -> list[str]:
    if not ref:
        return list(refs)
    merged = list(refs)
    if ref not in merged:
        merged.append(ref)
    return merged


def coerce_confidence(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = float(default)
    return min(1.0, max(0.0, parsed))


def preferred_str(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def coerce_non_negative_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    return max(0, parsed)


def _parse_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def normalize_steps(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def infer_memory_kind(raw: Any, *, layer: str, type_: Any) -> str:
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()
    type_text = str(type_ or "").lower()
    if layer == "L3" or type_text in {"procedure", "workflow", "playbook"}:
        return "procedure"
    if type_text in {"preference", "constraint"}:
        return type_text
    return "fact"


def build_structured_content(payload: dict[str, Any]) -> str:
    content = str(payload.get("content", "") or "").strip()
    memory_kind = infer_memory_kind(
        payload.get("memory_kind"),
        layer=str(payload.get("layer", "L2")),
        type_=payload.get("type"),
    )
    if memory_kind != "procedure":
        return content
    steps = normalize_steps(payload.get("steps"))
    prerequisites = normalize_steps(payload.get("prerequisites"))
    lines = [f"goal: {content}"]
    if prerequisites:
        lines.append("prerequisites:")
        lines.extend(f"- {item}" for item in prerequisites)
    if steps:
        lines.append("steps:")
        lines.extend(f"{index + 1}. {item}" for index, item in enumerate(steps))
    return "\n".join(lines).strip()


def build_source_refs(candidate: DreamMemoryCandidate) -> list[str]:
    refs = [candidate.candidate_id]
    refs.extend(str(ref) for ref in candidate.source_entry_ids if str(ref))
    deduped: list[str] = []
    for ref in refs:
        if ref not in deduped:
            deduped.append(ref)
    return deduped


def build_rem_artifacts(
    entries: list[MemoryEntry],
    *,
    archive_summary: str,
    graph_group_id: str,
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    synthesis = format_rem_archive_summary(
        archive_summary=archive_summary,
        graph_group_id=graph_group_id,
        entries=entries,
    )
    if synthesis:
        artifacts.append(
            {
                "artifact_kind": "archive",
                "summary": synthesis,
                "payload": build_archive_payload(entries, graph_group_id=graph_group_id),
            }
        )
    pattern_artifact = build_pattern_artifact(entries, graph_group_id=graph_group_id)
    if pattern_artifact:
        artifacts.append(pattern_artifact)
    graph_artifact = build_graph_artifact(entries, graph_group_id=graph_group_id)
    if graph_artifact:
        artifacts.append(graph_artifact)
    relation_artifact = build_relation_artifact(entries, graph_group_id=graph_group_id)
    if relation_artifact:
        artifacts.append(relation_artifact)
    return artifacts


def format_rem_archive_summary(
    *,
    archive_summary: str,
    graph_group_id: str,
    entries: list[MemoryEntry],
) -> str:
    summary = archive_summary.strip()
    if not summary:
        return ""
    lines = [
        "REM archive synthesis",
        f"group={graph_group_id}",
        summary,
    ]
    source_clusters = sorted({entry.cluster_id for entry in entries if entry.cluster_id})
    if source_clusters:
        lines.append(f"source_clusters: {', '.join(source_clusters[:6])}")
    return "\n".join(lines)


def build_archive_payload(entries: list[MemoryEntry], *, graph_group_id: str) -> dict[str, Any]:
    return {
        "graph_group_id": graph_group_id,
        "entry_count": len(entries),
        "source_memory_ids": [entry.id for entry in entries[:8]],
        "entities": _collect_entities(entries),
        "topics": _collect_topics(entries),
        "clusters": sorted({entry.cluster_id for entry in entries if entry.cluster_id})[:6],
    }


def build_pattern_artifact(entries: list[MemoryEntry], *, graph_group_id: str) -> dict[str, Any] | None:
    facts = [entry for entry in entries if entry.layer == "L2"][:3]
    procedures = [entry for entry in entries if entry.layer == "L3"][:3]
    if not facts and not procedures:
        return None
    payload = {
        "patterns": [
            *[_pattern_item(entry, category="stable_fact") for entry in facts],
            *[_pattern_item(entry, category="workflow_pattern") for entry in procedures],
        ],
        "entities": _collect_entities([*facts, *procedures]),
        "topics": _collect_topics([*facts, *procedures]),
    }
    lines = [
        "REM pattern summary",
        f"group={graph_group_id}",
    ]
    if facts:
        lines.append("stable_facts:")
        lines.extend(f"- {_entry_preview(entry)}" for entry in facts)
    if procedures:
        lines.append("workflow_patterns:")
        lines.extend(f"- {_entry_preview(entry)}" for entry in procedures)
    return {
        "artifact_kind": "pattern",
        "summary": "\n".join(lines),
        "payload": payload,
    }


def build_graph_artifact(entries: list[MemoryEntry], *, graph_group_id: str) -> dict[str, Any] | None:
    if len(entries) < 2:
        return None
    relation_payload = build_relation_payload(entries)
    nodes = [_node_item(entry) for entry in entries[:6]]
    payload = {
        "nodes": nodes,
        "relations": relation_payload["relations"],
        "entities": _collect_entities(entries),
        "topics": _collect_topics(entries),
        "clusters": sorted({entry.cluster_id for entry in entries if entry.cluster_id})[:6],
    }
    lines = [
        "REM graph summary",
        f"group={graph_group_id}",
        "nodes:",
    ]
    for node in nodes:
        lines.append(f"- [{node['layer']}:{node['type']}] {node['summary']}")
    if payload["clusters"]:
        lines.append(f"clusters: {', '.join(payload['clusters'])}")
    if payload["relations"]:
        lines.extend(
            [
                "relations:",
                *[
                    f"- [{relation['type']}] {relation['source_label']} -> {relation['target_label']}"
                    for relation in payload["relations"][:4]
                ],
            ]
        )
    return {
        "artifact_kind": "graph",
        "summary": "\n".join(lines),
        "payload": payload,
    }


def build_relation_artifact(entries: list[MemoryEntry], *, graph_group_id: str) -> dict[str, Any] | None:
    payload = build_relation_payload(entries)
    relations = payload["relations"]
    if not relations:
        return None
    lines = [
        "REM relation summary",
        f"group={graph_group_id}",
        "relations:",
        *[
            f"- [{relation['type']}] {relation['source_label']} -> {relation['target_label']}"
            for relation in relations[:6]
        ],
    ]
    return {
        "artifact_kind": "relation",
        "summary": "\n".join(lines),
        "payload": payload,
    }


def build_relation_payload(entries: list[MemoryEntry]) -> dict[str, Any]:
    relations: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    facts = [entry for entry in entries if entry.layer == "L2"][:4]
    procedures = [entry for entry in entries if entry.layer == "L3"][:4]
    for procedure in procedures:
        for fact in facts:
            relation_type = "guided_by" if fact.type in {"preference", "constraint"} else "depends_on"
            _append_relation(relations, seen, procedure, fact, relation_type)
    by_entity: dict[str, list[MemoryEntry]] = {}
    for entry in entries:
        if entry.entity_key:
            by_entity.setdefault(entry.entity_key, []).append(entry)
    for entity_entries in by_entity.values():
        anchor = entity_entries[0]
        for current in entity_entries[1:4]:
            _append_relation(relations, seen, anchor, current, "same_entity")
    for entry in entries:
        for superseded_id in entry.supersedes[:4]:
            key = (entry.id, superseded_id, "supersedes")
            if key in seen:
                continue
            seen.add(key)
            relations.append(
                {
                    "source": entry.id,
                    "target": superseded_id,
                    "type": "supersedes",
                    "source_label": _entry_label(entry),
                    "target_label": superseded_id,
                }
            )
    return {
        "relations": relations[:12],
        "entities": _collect_entities(entries),
        "relation_types": sorted({relation["type"] for relation in relations}),
    }


def _append_relation(
    relations: list[dict[str, Any]],
    seen: set[tuple[str, str, str]],
    source: MemoryEntry,
    target: MemoryEntry,
    relation_type: str,
) -> None:
    key = (source.id, target.id, relation_type)
    if key in seen:
        return
    seen.add(key)
    relations.append(
        {
            "source": source.id,
            "target": target.id,
            "type": relation_type,
            "source_label": _entry_label(source),
            "target_label": _entry_label(target),
        }
    )


def _pattern_item(entry: MemoryEntry, *, category: str) -> dict[str, Any]:
    return {
        "memory_id": entry.id,
        "category": category,
        "layer": entry.layer,
        "type": entry.type,
        "entity_key": entry.entity_key,
        "summary": _entry_preview(entry),
    }


def _node_item(entry: MemoryEntry) -> dict[str, Any]:
    return {
        "memory_id": entry.id,
        "layer": entry.layer,
        "type": entry.type,
        "entity_key": entry.entity_key,
        "summary": _entry_preview(entry),
    }


def _entry_label(entry: MemoryEntry) -> str:
    entity = preferred_str(entry.entity_key)
    return entity or f"{entry.layer}:{entry.type}:{entry.id[:8]}"


def _entry_preview(entry: MemoryEntry) -> str:
    text = " ".join(str(entry.content or "").split())
    return text[:160]


def _collect_entities(entries: list[MemoryEntry]) -> list[str]:
    entities: list[str] = []
    for entry in entries:
        entity = preferred_str(entry.entity_key)
        if entity and entity not in entities:
            entities.append(entity)
    return entities[:8]


def _collect_topics(entries: list[MemoryEntry]) -> list[str]:
    topics: list[str] = []
    for entry in entries:
        topic = f"{entry.layer}:{entry.type}"
        if topic not in topics:
            topics.append(topic)
    return topics[:8]


def build_publishable_rem_artifacts(
    session_key: str,
    entries: list[MemoryEntry],
    *,
    artifacts: list[dict[str, Any]],
    graph_group_id: str,
) -> list[DreamPublishableArtifact]:
    source_refs = build_rem_source_refs(entries)
    publishable: list[DreamPublishableArtifact] = []
    for artifact in artifacts:
        normalized_kind = str(artifact.get("artifact_kind", "")).strip().lower()
        summary = str(artifact.get("summary", "") or "").strip()
        payload = artifact.get("payload", {})
        if normalized_kind not in {"archive", "pattern", "graph", "relation"} or not summary:
            continue
        publishable.append(
            DreamPublishableArtifact(
                artifact_kind=normalized_kind,  # type: ignore[arg-type]
                session_id=build_rem_artifact_session_id(
                    session_key,
                    artifact_kind=normalized_kind,
                    graph_group_id=graph_group_id,
                    summary=summary,
                ),
                session_key=session_key,
                summary=summary,
                graph_group_id=graph_group_id,
                source_refs=list(source_refs),
                payload=dict(payload) if isinstance(payload, dict) else {},
                origin_stage="rem",
            )
        )
    return publishable


def build_l4_artifact_entry_from_publishable(
    publishable: DreamPublishableArtifact,
) -> L4ArtifactEntry:
    payload = {
        "artifact_kind": publishable.artifact_kind,
        "graph_group_id": publishable.graph_group_id,
        "source_refs": list(publishable.source_refs),
    }
    if publishable.payload:
        payload.update(publishable.payload)
    return L4ArtifactEntry(
        id=publishable.session_id,
        artifact_kind=publishable.artifact_kind,  # type: ignore[arg-type]
        session_id=publishable.session_id,
        summary=publishable.summary,
        created_at=time.time(),
        updated_at=time.time(),
        graph_group_id=publishable.graph_group_id,
        source_refs=list(publishable.source_refs),
        origin_stage=publishable.origin_stage,
        payload=payload,
    )


def build_rem_artifact_session_id(
    session_key: str,
    *,
    artifact_kind: str,
    graph_group_id: str,
    summary: str,
) -> str:
    digest = hashlib.sha1(
        f"{session_key}|{artifact_kind}|{graph_group_id}|{summary}".encode("utf-8")
    ).hexdigest()
    return f"{session_key}:dream:rem:{artifact_kind}:{digest[:12]}"


def build_rem_source_refs(entries: list[MemoryEntry]) -> list[str]:
    refs: list[str] = []
    for entry in entries:
        if entry.id not in refs:
            refs.append(entry.id)
        for ref in entry.source_refs:
            if ref and ref not in refs:
                refs.append(ref)
    return refs


def serialize_publishable_memory(publishable: DreamPublishableMemory) -> dict[str, Any]:
    return {
        "layer": publishable.layer,
        "memory_kind": publishable.memory_kind,
        "memory_type": publishable.memory_type,
        "content": publishable.content,
        "session_id": publishable.session_id,
        "session_key": publishable.session_key,
        "scope": publishable.scope,
        "entity_key": publishable.entity_key,
        "confidence": publishable.confidence,
        "evidence_count": publishable.evidence_count,
        "prerequisites": list(publishable.prerequisites),
        "steps": list(publishable.steps),
        "source_refs": list(publishable.source_refs),
        "supersedes": list(publishable.supersedes),
        "graph_group_id": publishable.graph_group_id,
        "version_chain_id": publishable.version_chain_id,
        "valid_from": publishable.valid_from,
        "valid_to": publishable.valid_to,
        "winner_flag": publishable.winner_flag,
        "publish_state": publishable.publish_state,
        "origin_stage": publishable.origin_stage,
    }


def deserialize_publishable_memory(raw: Any) -> DreamPublishableMemory | None:
    if not isinstance(raw, dict):
        return None
    try:
        return DreamPublishableMemory(
            layer=str(raw.get("layer", "L2")),  # type: ignore[arg-type]
            memory_kind=str(raw.get("memory_kind", "fact")),  # type: ignore[arg-type]
            memory_type=str(raw.get("memory_type", "fact")),
            content=str(raw.get("content", "")).strip(),
            session_id=str(raw.get("session_id", "")),
            session_key=str(raw.get("session_key", "")),
            scope=preferred_str(raw.get("scope")) or None,
            entity_key=preferred_str(raw.get("entity_key")) or None,
            confidence=coerce_confidence(raw.get("confidence"), 0.75),
            evidence_count=coerce_non_negative_int(raw.get("evidence_count"), 1),
            prerequisites=normalize_steps(raw.get("prerequisites")),
            steps=normalize_steps(raw.get("steps")),
            source_refs=[str(item) for item in raw.get("source_refs", []) if str(item)],
            supersedes=[str(item) for item in raw.get("supersedes", []) if str(item)],
            graph_group_id=preferred_str(raw.get("graph_group_id")) or None,
            version_chain_id=preferred_str(raw.get("version_chain_id")) or None,
            valid_from=_parse_optional_float(raw.get("valid_from")),
            valid_to=_parse_optional_float(raw.get("valid_to")),
            winner_flag=bool(raw.get("winner_flag", True)),
            publish_state=str(raw.get("publish_state", "reviewed")),  # type: ignore[arg-type]
            origin_stage=str(raw.get("origin_stage", "candidate_merge")),
        )
    except Exception:
        return None


def serialize_publishable_artifact(publishable: DreamPublishableArtifact) -> dict[str, Any]:
    return {
        "artifact_kind": publishable.artifact_kind,
        "session_id": publishable.session_id,
        "session_key": publishable.session_key,
        "summary": publishable.summary,
        "publish_state": publishable.publish_state,
        "graph_group_id": publishable.graph_group_id,
        "source_refs": list(publishable.source_refs),
        "payload": dict(publishable.payload),
        "origin_stage": publishable.origin_stage,
    }


def deserialize_publishable_artifact(raw: Any) -> DreamPublishableArtifact | None:
    if not isinstance(raw, dict):
        return None
    try:
        return DreamPublishableArtifact(
            artifact_kind=str(raw.get("artifact_kind", "archive")),  # type: ignore[arg-type]
            session_id=str(raw.get("session_id", "")),
            session_key=str(raw.get("session_key", "")),
            summary=str(raw.get("summary", "")).strip(),
            publish_state=str(raw.get("publish_state", "published")),  # type: ignore[arg-type]
            graph_group_id=preferred_str(raw.get("graph_group_id")) or None,
            source_refs=[str(item) for item in raw.get("source_refs", []) if str(item)],
            payload=dict(raw.get("payload", {})) if isinstance(raw.get("payload", {}), dict) else {},
            origin_stage=str(raw.get("origin_stage", "rem")),
        )
    except Exception:
        return None
