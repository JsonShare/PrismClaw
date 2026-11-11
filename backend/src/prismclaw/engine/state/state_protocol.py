from __future__ import annotations

import hashlib
import json
from typing import Any

from prismclaw.schema.agent.session import SessionContextItem, SessionContextState, SessionStateUpdate

# 状态载荷最近消息条数：结构性展示参数（非预算旋钮），单一来源，
# state_store 从此导入。
_RECENT_MESSAGE_LIMIT = 6
_STATE_PAYLOAD_RENDER_KEYS = (
    "primary_goal",
    "constraints",
    "decisions",
    "todos",
    "facts",
    "assumptions",
    "open_questions",
    "recent_messages",
)
_STATE_PAYLOAD_LIST_KEYS = (
    "constraints",
    "decisions",
    "todos",
    "facts",
    "assumptions",
    "open_questions",
)
_SUPERSEDED_PREFIXES = (
    "resolved ",
    "resolved:",
    "done ",
    "done:",
    "completed ",
    "completed:",
    "answered ",
    "answered:",
    "closed ",
    "closed:",
    "obsolete ",
    "obsolete:",
    "superseded ",
    "superseded:",
    "replaced ",
    "replaced:",
    "cancelled ",
    "cancelled:",
    "已解决",
    "已完成",
    "已替换",
    "已废弃",
)
_UNCERTAIN_PREFIXES = (
    "maybe ",
    "maybe:",
    "uncertain ",
    "uncertain:",
    "possibly ",
    "possibly:",
    "probably ",
    "probably:",
    "tentative ",
    "tentative:",
    "可能",
    "不确定",
    "待确认",
)


def extract_message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text)
        return "\n".join(parts)
    return ""


def extract_state_update(messages: list[dict[str, Any]]) -> SessionStateUpdate:
    update = SessionStateUpdate()
    for message in messages:
        role = message.get("role")
        text = extract_message_text(message)
        entry_id = message.get("__entry_id__")
        source_ref = str(entry_id) if isinstance(entry_id, str) and entry_id else None
        if not text.strip():
            continue
        if role == "user" and update.primary_goal is None:
            update.primary_goal = _snippet(text, limit=320)
        for value in _extract_lines(("constraint", "constraints"), text):
            update.constraints.append(_item(value, kind="constraint", source_ref=source_ref))
        for value in _extract_lines(("decision", "decisions"), text):
            update.decisions.append(_item(value, kind="decision", source_ref=source_ref))
        for value in _extract_lines(("todo", "next", "action"), text):
            update.todos.append(_item(value, kind="todo", source_ref=source_ref))
        for value in _extract_lines(("assumption", "assumptions"), text):
            update.assumptions.append(_item(value, kind="assumption", source_ref=source_ref))
        for value in _extract_lines(("question", "questions", "open_question"), text):
            update.open_questions.append(_item(value, kind="question", source_ref=source_ref))
        if role == "assistant":
            for value in _extract_lines(("fact", "facts", "finding", "findings"), text):
                update.facts.append(_item(value, kind="fact", source_ref=source_ref))
        if role == "tool":
            _merge_subagent_result_update(
                update,
                _extract_tool_result_sections(text),
                source_ref=source_ref,
            )
    return update


def derive_state_payload(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    update = extract_state_update(messages)
    if not has_state_update_payload(update):
        return None
    return normalize_state_payload(update)


def has_state_update_payload(update: SessionStateUpdate | dict[str, Any] | None) -> bool:
    if update is None:
        return False
    normalized = normalize_state_payload(update)
    return any(value for value in normalized.values())


def state_payload_from_state(state: SessionContextState) -> dict[str, Any]:
    return normalize_state_payload(state)


def session_memory_payload_from_state(state: SessionContextState) -> dict[str, Any]:
    payload = normalize_state_payload(state)
    payload["recent_messages"] = []
    return payload


def normalize_state_payload(
    value: SessionStateUpdate | SessionContextState | dict[str, Any] | None,
) -> dict[str, Any]:
    if value is None:
        payload: dict[str, Any] = {}
    elif isinstance(value, SessionContextState):
        payload = {
            "primary_goal": value.primary_goal,
            "constraints": [
                item.model_dump(mode="json")
                for item in value.constraints
                if item.status == "active"
            ],
            "decisions": [
                item.model_dump(mode="json")
                for item in value.decisions
                if item.status == "active"
            ],
            "todos": [
                item.model_dump(mode="json")
                for item in value.todos
                if item.status == "active"
            ],
            "facts": [
                item.model_dump(mode="json")
                for item in value.facts
                if item.status == "active"
            ],
            "assumptions": [
                item.model_dump(mode="json")
                for item in value.assumptions
                if item.status == "active"
            ],
            "open_questions": [
                item.model_dump(mode="json")
                for item in value.open_questions
                if item.status == "active"
            ],
            "recent_messages": list(value.recent_messages[-_RECENT_MESSAGE_LIMIT:]),
            "evidence": list(value.evidence) if value.evidence else [],
        }
    elif isinstance(value, SessionStateUpdate):
        payload = value.model_dump(mode="json")
    else:
        payload = dict(value)

    normalized: dict[str, Any] = {"primary_goal": None, "recent_messages": []}
    primary_goal = payload.get("primary_goal")
    if isinstance(primary_goal, str) and primary_goal.strip():
        normalized["primary_goal"] = primary_goal.strip()
    for key in _STATE_PAYLOAD_LIST_KEYS:
        normalized[key] = _normalize_state_items(payload.get(key))
    normalized["recent_messages"] = _normalize_recent_messages(payload.get("recent_messages"))
    # evidence 透传（list[dict]，不做 normalize）
    raw_evidence = payload.get("evidence")
    if isinstance(raw_evidence, list):
        normalized["evidence"] = raw_evidence
    return normalized


def merge_state_payloads(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    merged = normalize_state_payload(None)
    seen_items: dict[str, set[str]] = {
        key: set() for key in (*_STATE_PAYLOAD_LIST_KEYS, "recent_messages")
    }
    seen_evidence: set[str] = set()
    for payload in payloads:
        normalized = normalize_state_payload(payload)
        primary_goal = normalized.get("primary_goal")
        if merged["primary_goal"] is None and isinstance(primary_goal, str) and primary_goal.strip():
            merged["primary_goal"] = primary_goal.strip()
        for key in _STATE_PAYLOAD_LIST_KEYS:
            for item in normalized[key]:
                content = item["content"]
                if content in seen_items[key]:
                    continue
                seen_items[key].add(content)
                merged[key].append(item)
        for value in normalized["recent_messages"]:
            if value in seen_items["recent_messages"]:
                continue
            seen_items["recent_messages"].add(value)
            merged["recent_messages"].append(value)
        # 合并 evidence（按 source_path + quoted_text 去重）
        for ev in normalized.get("evidence", []):
            if isinstance(ev, dict):
                ev_key = f"{ev.get('source_path', '')}:{ev.get('quoted_text', '')}"
                if ev_key not in seen_evidence:
                    seen_evidence.add(ev_key)
                    merged.setdefault("evidence", []).append(ev)
    return merged


def diff_state_payload(
    current: dict[str, Any] | SessionStateUpdate | SessionContextState | None,
    baseline: dict[str, Any] | SessionStateUpdate | SessionContextState | None,
) -> dict[str, Any]:
    current_payload = normalize_state_payload(current)
    baseline_payload = normalize_state_payload(baseline)
    delta = normalize_state_payload(None)
    current_goal = current_payload.get("primary_goal")
    baseline_goal = baseline_payload.get("primary_goal")
    if isinstance(current_goal, str) and current_goal.strip() and current_goal != baseline_goal:
        delta["primary_goal"] = current_goal
    for key in _STATE_PAYLOAD_LIST_KEYS:
        baseline_contents = {
            item.get("content")
            for item in baseline_payload.get(key, [])
            if isinstance(item, dict) and isinstance(item.get("content"), str)
        }
        delta[key] = [
            item
            for item in current_payload.get(key, [])
            if isinstance(item, dict)
            and isinstance(item.get("content"), str)
            and item.get("content") not in baseline_contents
        ]
    baseline_recent = set(baseline_payload.get("recent_messages", []))
    delta["recent_messages"] = [
        item for item in current_payload.get("recent_messages", []) if item not in baseline_recent
    ]
    return delta


def render_state_payload_block(
    *,
    wrapper: str,
    state_payload: dict[str, Any] | SessionStateUpdate | SessionContextState | None,
    summary_text: str | None = None,
) -> str | None:
    normalized = normalize_state_payload(state_payload)
    if not any(value for value in normalized.values()) and not (
        isinstance(summary_text, str) and summary_text.strip()
    ):
        return None
    lines = [f"<{wrapper}>"]
    if isinstance(summary_text, str) and summary_text.strip():
        lines.append("<summary>")
        lines.append(summary_text.strip())
        lines.append("</summary>")
    for key in _STATE_PAYLOAD_RENDER_KEYS:
        value = normalized.get(key)
        if not value:
            continue
        lines.append(f"<{key}>")
        if isinstance(value, str):
            lines.append(value)
        elif key == "recent_messages":
            for item in value:
                lines.append(f"- {item}")
        else:
            for item in value:
                content = item.get("content") if isinstance(item, dict) else None
                if isinstance(content, str) and content.strip():
                    lines.append(f"- {content.strip()}")
        lines.append(f"</{key}>")
    lines.append(f"</{wrapper}>")
    return "\n".join(lines)


def _snippet(value: str, *, limit: int = 240) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _stable_item_id(kind: str, content: str) -> str:
    digest = hashlib.sha1(f"{kind}:{content}".encode("utf-8")).hexdigest()
    return f"{kind[:3]}_{digest[:12]}"


def _extract_lines(prefixes: tuple[str, ...], text: str) -> list[str]:
    results: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        for prefix in prefixes:
            if lowered.startswith(prefix):
                value = line[len(prefix) :].strip(" :-")
                if value:
                    results.append(value)
                break
    return results


def _item(content: str, *, kind: str, source_ref: str | None = None) -> SessionContextItem:
    status, normalized_content, confidence = _classify_item_disposition(content)
    refs = [source_ref] if source_ref else []
    return SessionContextItem(
        id=_stable_item_id(kind, normalized_content),
        kind=kind,
        content=normalized_content,
        status=status,
        confidence=confidence,
        source_refs=refs,
    )


def _normalize_state_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, SessionContextItem):
            candidate = item.model_dump(mode="json")
        elif isinstance(item, dict):
            candidate = dict(item)
        elif isinstance(item, str) and item.strip():
            candidate = {"content": item.strip()}
        else:
            continue
        content = candidate.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        candidate["content"] = content.strip()
        normalized.append(candidate)
    return normalized


def _classify_item_disposition(content: str) -> tuple[str, str, float]:
    text = content.strip()
    lowered = text.lower()
    for prefix in _SUPERSEDED_PREFIXES:
        prefix_lower = prefix.lower()
        if lowered.startswith(prefix_lower):
            normalized = text[len(prefix) :].strip(" :-")
            return "superseded", normalized or text, 1.0
    for prefix in _UNCERTAIN_PREFIXES:
        prefix_lower = prefix.lower()
        if lowered.startswith(prefix_lower):
            normalized = text[len(prefix) :].strip(" :-")
            return "uncertain", normalized or text, 0.5
    return "active", text, 1.0


def _normalize_recent_messages(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        item.strip() for item in value if isinstance(item, str) and item.strip()
    ][-_RECENT_MESSAGE_LIMIT:]


def _extract_tool_result_sections(text: str) -> dict[str, list[str]]:
    parsed_sections = _extract_structured_tool_result_sections(text)
    if parsed_sections is not None:
        return parsed_sections
    return _extract_subagent_result_sections(text)


def _extract_structured_tool_result_sections(text: str) -> dict[str, list[str]] | None:
    try:
        payload = json.loads(text)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    result = {
        "findings": _string_list(payload.get("findings")),
        "decisions": _string_list(payload.get("decisions")),
        "open_questions": _string_list(payload.get("open_questions")),
        "recommended_next_actions": _string_list(payload.get("recommended_next_actions")),
    }

    # 提取 evidence，存入 session_state.evidence
    raw_evidence = payload.get("evidence")
    if isinstance(raw_evidence, list):
        result["evidence"] = raw_evidence

    return result


def _extract_subagent_result_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {
        "findings": [],
        "decisions": [],
        "open_questions": [],
        "recommended_next_actions": [],
    }
    active_section: str | None = None
    heading_map = {
        "findings": "findings",
        "decisions": "decisions",
        "open_questions": "open_questions",
        "recommended_next_actions": "recommended_next_actions",
    }
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        normalized = line.rstrip(":").lower().replace(" ", "_")
        if normalized in heading_map:
            active_section = heading_map[normalized]
            continue
        if active_section is None:
            continue
        if line.startswith("- "):
            value = line[2:].strip()
            if value:
                sections[active_section].append(value)
            continue
        active_section = None
    return sections


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _merge_subagent_result_update(
    update: SessionStateUpdate,
    sections: dict[str, list[str]],
    *,
    source_ref: str | None,
) -> None:
    for value in sections["findings"]:
        update.facts.append(_item(value, kind="fact", source_ref=source_ref))
    for value in sections["decisions"]:
        update.decisions.append(_item(value, kind="decision", source_ref=source_ref))
    for value in sections["open_questions"]:
        update.open_questions.append(_item(value, kind="question", source_ref=source_ref))
    for value in sections["recommended_next_actions"]:
        update.todos.append(_item(value, kind="todo", source_ref=source_ref))
    # 写入 evidence（原始 dict 列表，由 _collect_prior_evidence 在下游解析）
    if "evidence" in sections:
        update.evidence.extend(sections["evidence"])
