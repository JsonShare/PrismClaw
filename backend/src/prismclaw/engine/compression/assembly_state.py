from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from prismclaw.engine.state.state_protocol import merge_state_payloads
from prismclaw.schema.agent.task_types import TaskSpec
from prismclaw.schema.content.context import ContextBlock

# 状态组装条数上限：结构性展示参数（非预算旋钮），不外置。
_MAX_SECTION_ITEMS = 4
_MAX_RECENT_MESSAGES = 3


@dataclass(slots=True)
class AssemblyStateBundle:
    anchor_blocks: list[ContextBlock]
    merged_state_payload: dict[str, Any]
    retrieval_query: str | None
    anchor_sources: list[str]


def build_assembly_state_bundle(
    *,
    prompt: str | None,
    messages: list[dict[str, Any]] | list[Any],
    anchor_blocks: list[ContextBlock],
    task_spec: TaskSpec | None = None,
) -> AssemblyStateBundle:
    merged_state_payload = merge_state_payloads(
        [
            block.state_payload
            for block in anchor_blocks
            if isinstance(block.state_payload, dict) and block.state_payload
        ]
    )
    retrieval_query = build_state_driven_retrieval_query(
        prompt=prompt,
        messages=messages,
        state_payload=merged_state_payload,
        task_spec=task_spec,
    )
    return AssemblyStateBundle(
        anchor_blocks=list(anchor_blocks),
        merged_state_payload=merged_state_payload,
        retrieval_query=retrieval_query,
        anchor_sources=[block.source for block in anchor_blocks if block.source],
    )


def build_state_driven_retrieval_query(
    *,
    prompt: str | None,
    messages: list[dict[str, Any]] | list[Any],
    state_payload: dict[str, Any],
    task_spec: TaskSpec | None = None,
) -> str | None:
    lines: list[str] = []
    prompt_text = (prompt or "").strip()
    if task_spec is not None:
        lines.append(f"task_goal: {task_spec.goal.strip()}")
        lines.append(f"task_risk: {task_spec.risk_level}")
        if task_spec.constraints:
            lines.append("task_constraints:")
            lines.extend(f"- {value}" for value in task_spec.constraints[:_MAX_SECTION_ITEMS])
    if prompt_text:
        lines.append(f"user_request: {prompt_text}")

    primary_goal = state_payload.get("primary_goal")
    if isinstance(primary_goal, str) and primary_goal.strip():
        lines.append(f"primary_goal: {primary_goal.strip()}")

    for label, key in (
        ("active_decisions", "decisions"),
        ("active_todos", "todos"),
        ("active_constraints", "constraints"),
        ("open_questions", "open_questions"),
        ("known_facts", "facts"),
    ):
        values = _payload_contents(state_payload.get(key))
        if not values:
            continue
        lines.append(f"{label}:")
        lines.extend(f"- {value}" for value in values[:_MAX_SECTION_ITEMS])

    recent_messages = [
        item.strip()
        for item in state_payload.get("recent_messages", [])
        if isinstance(item, str) and item.strip()
    ]
    if recent_messages:
        lines.append("recent_messages:")
        lines.extend(f"- {value}" for value in recent_messages[-_MAX_RECENT_MESSAGES:])
    elif not prompt_text:
        fallback = _recent_user_message(messages)
        if fallback:
            lines.append(f"user_request: {fallback}")

    query = "\n".join(lines).strip()
    return query or None


def _payload_contents(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        item["content"]
        for item in value
        if isinstance(item, dict) and isinstance(item.get("content"), str) and item["content"].strip()
    ]


def _recent_user_message(messages: list[dict[str, Any]] | list[Any]) -> str | None:
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return None
