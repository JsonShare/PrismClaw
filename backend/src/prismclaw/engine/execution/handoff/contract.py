from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


HANDOFF_REQUEST_BLOCK_NAME = "HANDOFF_REQUEST"


@dataclass
class HandoffRequestEntry:
    target: str
    task: str
    context_hint: str | None
    source_subagent: str
    source_sub_session_id: str
    source_chain_id: str
    source_hop_index: int
    source_effective_hop_index: int
    source_skill_origin: str | None = None
    source_preload_skills: list[str] = field(default_factory=list)
    source_is_fork: bool = False
    insertion_seq: int = 0
    insertion_turn: int = 0


def build_handoff_request_payload(
    *,
    target: str,
    task: str,
    context_hint: str | None,
    source_subagent: str,
    source_sub_session_id: str,
    source_chain_id: str,
    source_hop_index: int,
    source_effective_hop_index: int,
    source_skill_origin: str | None = None,
    source_preload_skills: list[str] | None = None,
    source_is_fork: bool = False,
) -> dict[str, Any]:
    return {
        "target": target,
        "task": task,
        "context_hint": context_hint,
        "source_subagent": source_subagent,
        "source_sub_session_id": source_sub_session_id,
        "source_chain_id": source_chain_id,
        "source_hop_index": source_hop_index,
        "source_effective_hop_index": source_effective_hop_index,
        "source_skill_origin": source_skill_origin,
        "source_preload_skills": _coerce_skill_names(source_preload_skills),
        "source_is_fork": bool(source_is_fork),
    }


def normalize_handoff_request_payload(
    payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    normalized: dict[str, Any] = {}
    for key in (
        "target",
        "task",
        "context_hint",
        "source_subagent",
        "source_sub_session_id",
        "source_chain_id",
        "source_hop_index",
        "source_effective_hop_index",
        "tool_call_id",
    ):
        if key in payload:
            normalized[key] = payload[key]

    if "source_skill_origin" in payload:
        normalized["source_skill_origin"] = payload["source_skill_origin"]

    if "source_preload_skills" in payload:
        normalized["source_preload_skills"] = _coerce_skill_names(
            payload["source_preload_skills"]
        )

    if "source_is_fork" in payload:
        normalized["source_is_fork"] = bool(payload["source_is_fork"])

    return normalized or None


def handoff_request_entry_from_payload(
    payload: dict[str, Any] | None,
) -> HandoffRequestEntry | None:
    normalized = normalize_handoff_request_payload(payload)
    if normalized is None:
        return None
    try:
        return HandoffRequestEntry(
            target=normalized["target"],
            task=normalized["task"],
            context_hint=normalized.get("context_hint"),
            source_subagent=normalized["source_subagent"],
            source_sub_session_id=normalized["source_sub_session_id"],
            source_chain_id=normalized["source_chain_id"],
            source_hop_index=normalized["source_hop_index"],
            source_effective_hop_index=normalized["source_effective_hop_index"],
            source_skill_origin=normalized.get("source_skill_origin"),
            source_preload_skills=_coerce_skill_names(
                normalized.get("source_preload_skills")
            ),
            source_is_fork=bool(normalized.get("source_is_fork", False)),
        )
    except KeyError:
        return None


def handoff_request_marker_contract_summary() -> str:
    return (
        "[HANDOFF_REQUEST] 块（target/task/context_hint，以及可选的 "
        "source_skill_origin/source_preload_skills/source_is_fork）"
    )


def render_handoff_request_handover(
    payload: dict[str, Any] | None,
) -> str:
    normalized = normalize_handoff_request_payload(payload)
    if normalized is None:
        return ""

    lines = [
        f"[{HANDOFF_REQUEST_BLOCK_NAME}]",
        f"target: {normalized.get('target', '')}",
        f"task: {normalized.get('task', '')}",
    ]
    context_hint = normalized.get("context_hint")
    if isinstance(context_hint, str) and context_hint:
        lines.append(f"context_hint: {context_hint}")
    source_skill_origin = normalized.get("source_skill_origin")
    if isinstance(source_skill_origin, str) and source_skill_origin:
        lines.append(f"source_skill_origin: {source_skill_origin}")
    source_preload_skills = normalized.get("source_preload_skills")
    if isinstance(source_preload_skills, list) and source_preload_skills:
        lines.append(
            "source_preload_skills: "
            + ", ".join(str(item) for item in source_preload_skills)
        )
    if normalized.get("source_is_fork") is True:
        lines.append("source_is_fork: true")
    lines.append(f"[/{HANDOFF_REQUEST_BLOCK_NAME}]")
    return "\n".join(lines)


def _coerce_skill_names(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for item in raw:
        if isinstance(item, str):
            name = item.strip()
            if name and name not in names:
                names.append(name)
    return names


__all__ = [
    "HANDOFF_REQUEST_BLOCK_NAME",
    "HandoffRequestEntry",
    "build_handoff_request_payload",
    "handoff_request_entry_from_payload",
    "handoff_request_marker_contract_summary",
    "normalize_handoff_request_payload",
    "render_handoff_request_handover",
]
