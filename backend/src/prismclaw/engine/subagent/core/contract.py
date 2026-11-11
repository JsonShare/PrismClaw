from __future__ import annotations
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from prismclaw.engine.toolkit.registry.tool_runtime import ToolContext

from prismclaw.schema.agent.session import SessionContextItem, SessionContextState
from prismclaw.schema.agent.task_runtime import ReviewRequirements
from prismclaw.schema.agent.task_types import TaskSpec
from prismclaw.schema.content.subagent import EvidenceRef, SubagentContextSnapshot, SubagentResultEnvelope


def context_snapshot_json_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "task_goal": {"type": "string"},
            "done_so_far": {
                "type": "array",
                "items": {"type": "string"},
            },
            "key_findings": {
                "type": "array",
                "items": {"type": "string"},
            },
            "constraints": {
                "type": "array",
                "items": {"type": "string"},
            },
            "open_questions": {
                "type": "array",
                "items": {"type": "string"},
            },
            "artifact_refs": {
                "type": "array",
                "items": {"type": "string"},
            },
            "expected_output": {"type": "string"},
            "system_prompt_section_names": {
                "type": "array",
                "items": {"type": "string"},
            },
            "system_prompt_cache_break_sections": {
                "type": "array",
                "items": {"type": "string"},
            },
            "active_skill_name": {"type": "string"},
            "invoked_skill_names": {
                "type": "array",
                "items": {"type": "string"},
            },
            "prior_evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source_type": {"type": "string"},
                        "source_path": {"type": "string"},
                        "source_location": {"type": "string"},
                        "quoted_text": {"type": "string"},
                        "confidence": {"type": "string"},
                        "domain_extensions": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                        },
                        "evidence_level": {"type": "string"},
                        "recommendation_grade": {"type": "string"},
                        "legal_source_type": {"type": "string"},
                        "legal_effective_date": {"type": "string"},
                        "legal_status": {"type": "string"},
                    },
                },
            },
        },
        "required": ["task_goal"],
        "additionalProperties": False,
    }


def coerce_context_snapshot(
    raw_snapshot: Any,
    *,
    fallback_task: str,
) -> SubagentContextSnapshot:
    if isinstance(raw_snapshot, SubagentContextSnapshot):
        return raw_snapshot
    if isinstance(raw_snapshot, dict):
        normalized = dict(raw_snapshot)
        if not normalized.get("task_goal"):
            normalized["task_goal"] = fallback_task
        return SubagentContextSnapshot.model_validate(normalized)
    raise ValueError("context snapshot must be a structured object")


def build_context_snapshot(
    *,
    task: str,
    parent_ctx: ToolContext,
    raw_snapshot: Any | None = None,
) -> SubagentContextSnapshot:
    if raw_snapshot not in (None, {}):
        return coerce_context_snapshot(raw_snapshot, fallback_task=task)

    task_spec = parent_ctx.task_spec
    review_requirements = parent_ctx.review_requirements
    session_state = _resolve_session_state(parent_ctx)

    task_goal = task.strip() or (task_spec.goal if task_spec is not None else "")
    if not task_goal and session_state is not None and session_state.primary_goal:
        task_goal = session_state.primary_goal
    if not task_goal:
        task_goal = "继续执行被委派的任务。"

    findings = _active_contents(getattr(session_state, "facts", []), limit=12 if len(getattr(session_state, "facts", [])) > 6 else 6)
    constraints = _dedupe(
        [
            *(_active_contents(getattr(session_state, "constraints", []), limit=6)),
            *(task_spec.constraints if task_spec is not None else []),
        ]
    )
    open_questions = _dedupe(
        _active_contents(getattr(session_state, "open_questions", []), limit=6)
    )
    done_so_far = _dedupe(
        [
            *(_task_profile_lines(task_spec)),
            *(_prefixed_lines("Decision", getattr(session_state, "decisions", []), limit=3)),
            *(_prefixed_lines("Todo", getattr(session_state, "todos", []), limit=3)),
            *(_recent_messages(session_state, limit=3)),
        ]
    )
    if not done_so_far and session_state is not None and session_state.primary_goal:
        done_so_far = [f"Parent goal: {session_state.primary_goal}"]

    artifact_refs = _dedupe(
        [
            *(_source_refs(getattr(session_state, "facts", []), limit=6)),
            *(_source_refs(getattr(session_state, "decisions", []), limit=4)),
            *(_source_refs(getattr(session_state, "open_questions", []), limit=4)),
            *(_runtime_refs(parent_ctx)),
        ]
    )
    system_prompt_section_names = _system_prompt_section_names(parent_ctx)
    system_prompt_cache_break_sections = _system_prompt_cache_break_sections(parent_ctx)
    active_skill_name = _active_skill_name(parent_ctx)
    invoked_skill_names = _invoked_skill_names(parent_ctx)
    prior_evidence = _collect_prior_evidence(session_state)

    return SubagentContextSnapshot(
        task_goal=task_goal,
        done_so_far=done_so_far,
        key_findings=findings,
        prior_evidence=prior_evidence,
        constraints=constraints,
        open_questions=open_questions,
        artifact_refs=artifact_refs,
        expected_output=_expected_output(task_spec, review_requirements),
        system_prompt_section_names=system_prompt_section_names,
        system_prompt_cache_break_sections=system_prompt_cache_break_sections,
        active_skill_name=active_skill_name,
        invoked_skill_names=invoked_skill_names,
    )


def render_context_snapshot(snapshot: SubagentContextSnapshot) -> str:
    lines = ["<context_handshake>"]
    lines.extend(_render_section("task_goal", [snapshot.task_goal]))
    lines.extend(_render_section("done_so_far", snapshot.done_so_far))
    lines.extend(_render_section("key_findings", snapshot.key_findings))
    lines.extend(_render_section("constraints", snapshot.constraints))
    lines.extend(_render_section("open_questions", snapshot.open_questions))
    lines.extend(_render_section("artifact_refs", snapshot.artifact_refs))
    if snapshot.prior_evidence:
        lines.append("<prior_evidence>")
        for ev in snapshot.prior_evidence:
            lines.append(f"- [{ev.confidence}] {ev.source_type}: {ev.source_path}")
            if ev.source_location:
                lines.append(f"  location: {ev.source_location}")
            if ev.quoted_text:
                quoted_preview = ev.quoted_text[:200] + ("..." if len(ev.quoted_text) > 200 else "")
                lines.append(f"  quote: {quoted_preview}")
            # 领域扩展（泛化渲染）
            for key, value in ev.domain_extensions.items():
                lines.append(f"  {key}: {value}")
            # deprecated: 旧域字段兜底（domain_extensions 为空时保留旧格式）
            if not ev.domain_extensions:
                if ev.evidence_level:
                    lines.append(f"  evidence_level: {ev.evidence_level}")
                if ev.recommendation_grade:
                    lines.append(f"  recommendation_grade: {ev.recommendation_grade}")
                if ev.legal_source_type:
                    lines.append(f"  legal_source_type: {ev.legal_source_type}")
                if ev.legal_effective_date:
                    lines.append(f"  legal_effective_date: {ev.legal_effective_date}")
                if ev.legal_status:
                    lines.append(f"  legal_status: {ev.legal_status}")
        lines.append("</prior_evidence>")
    if snapshot.expected_output:
        lines.extend(_render_section("expected_output", [snapshot.expected_output]))
    lines.extend(
        _render_section("system_prompt_section_names", snapshot.system_prompt_section_names)
    )
    lines.extend(
        _render_section(
            "system_prompt_cache_break_sections",
            snapshot.system_prompt_cache_break_sections,
        )
    )
    if snapshot.active_skill_name:
        lines.extend(_render_section("active_skill_name", [snapshot.active_skill_name]))
    lines.extend(_render_section("invoked_skill_names", snapshot.invoked_skill_names))
    lines.append("</context_handshake>")
    return "\n".join(lines)


def render_task_envelope(task: str, snapshot: SubagentContextSnapshot) -> str:
    return (
        f"<task_goal>\n{task}\n</task_goal>\n\n"
        f"{render_context_snapshot(snapshot)}\n\n"
        "请利用结构化上下文握手继续执行任务，不要重新发现已经确定的状态。\n\n"
        "请以 JSON 对象返回最终答案，字段包括："
        "summary、findings、decisions、open_questions、artifact_refs、"
        "recommended_next_actions、confidence_assessment。"
    )


def normalize_result_envelope_text(result: dict[str, Any] | str) -> str:
    """Normalize sub-agent success output into the stable JSON envelope contract."""
    if isinstance(result, str):
        text = result.strip()
        if not text:
            text = "子代理已结束，但未返回结论。"
        envelope = parse_subagent_result_envelope(text)
        if envelope is None:
            envelope = _wrap_plaintext_result(text)
        return envelope.model_dump_json()

    if "summary" in result:
        try:
            envelope = SubagentResultEnvelope.model_validate(result)
        except Exception:
            envelope = None
        else:
            return envelope.model_dump_json()

    summary = str(result.get("conclusion") or "").strip()
    if not summary:
        summary = "子代理已结束，但未返回结论。"
    trace_logs = result.get("trace", [])
    findings = [str(item).strip() for item in trace_logs[-5:] if str(item).strip()]
    envelope = SubagentResultEnvelope(summary=summary, findings=findings)
    return envelope.model_dump_json()


def parse_subagent_result_envelope(raw_text: str) -> SubagentResultEnvelope | None:
    text = raw_text.strip()
    if not text:
        return None
    try:
        return SubagentResultEnvelope.model_validate_json(text)
    except ValueError:
        return None


def render_result_envelope_handover(envelope: SubagentResultEnvelope) -> str:
    lines = ["[subagent_result]"]
    lines.append("summary:")
    lines.append(envelope.summary.strip() or "(empty)")
    for section_name, values in (
        ("findings", envelope.findings),
        ("decisions", envelope.decisions),
        ("open_questions", envelope.open_questions),
        ("artifact_refs", envelope.artifact_refs),
        ("recommended_next_actions", envelope.recommended_next_actions),
    ):
        if not values:
            continue
        lines.append(f"{section_name}:")
        for value in values:
            lines.append(f"- {value}")
    if envelope.evidence:
        lines.append("evidence:")
        for ev in envelope.evidence:
            ev_line = f"- [{ev.confidence}] {ev.source_type}: {ev.source_path}"
            if ev.source_location:
                ev_line += f" ({ev.source_location})"
            # 领域扩展（泛化渲染，保留域名前缀避免歧义）
            for key, value in ev.domain_extensions.items():
                ev_line += f" [{key}={value}]"
            # deprecated: 旧域字段兜底
            if not ev.domain_extensions:
                if ev.evidence_level:
                    ev_line += f" [evidence:{ev.evidence_level}]"
                if ev.recommendation_grade:
                    ev_line += f" [grade:{ev.recommendation_grade}]"
                if ev.legal_source_type:
                    ev_line += f" [legal:{ev.legal_source_type}]"
                if ev.legal_effective_date:
                    ev_line += f" effective:{ev.legal_effective_date}"
                if ev.legal_status:
                    ev_line += f" ({ev.legal_status})"
            lines.append(ev_line)
    if envelope.confidence_assessment:
        lines.append(f"confidence: {envelope.confidence_assessment}")
    lines.append("[/subagent_result]")
    return "\n".join(lines)


def _render_section(name: str, values: list[str]) -> list[str]:
    if not values:
        return []
    lines = [f"<{name}>"]
    for value in values:
        lines.append(f"- {value}")
    lines.append(f"</{name}>")
    return lines


def _coerce_model(raw: Any, model_type: Any) -> Any | None:
    if isinstance(raw, model_type):
        return raw
    if isinstance(raw, dict):
        try:
            return model_type.model_validate(raw)
        except Exception:
            return None
    return None


def _resolve_session_state(parent_ctx: ToolContext) -> SessionContextState | None:
    state = _coerce_model(parent_ctx.session_state, SessionContextState)
    if state is not None:
        return state
    engine = parent_ctx.context_engine
    getter = getattr(engine, "get_session_state", None)
    if not callable(getter):
        return None
    try:
        resolved = getter(parent_ctx.session_id)
    except Exception:
        return None
    return _coerce_model(resolved, SessionContextState)


def _active_contents(items: list[SessionContextItem], *, limit: int) -> list[str]:
    values: list[str] = []
    for item in items:
        if getattr(item, "status", "active") != "active":
            continue
        content = str(getattr(item, "content", "") or "").strip()
        if not content:
            continue
        values.append(content)
        if len(values) >= limit:
            break
    return values


def _prefixed_lines(prefix: str, items: list[SessionContextItem], *, limit: int) -> list[str]:
    return [f"{prefix}: {value}" for value in _active_contents(items, limit=limit)]


def _recent_messages(state: SessionContextState | None, *, limit: int) -> list[str]:
    if state is None:
        return []
    values = [str(item).strip() for item in state.recent_messages if str(item).strip()]
    return values[-limit:]


def _source_refs(items: list[SessionContextItem], *, limit: int) -> list[str]:
    refs: list[str] = []
    for item in items:
        if getattr(item, "status", "active") != "active":
            continue
        for ref in getattr(item, "source_refs", []):
            if isinstance(ref, str) and ref.strip():
                refs.append(ref.strip())
                if len(refs) >= limit:
                    return refs
    return refs


def _runtime_refs(ctx: ToolContext) -> list[str]:
    refs: list[str] = []
    task_id = ctx.task_id
    request_id = ctx.request_id
    if isinstance(task_id, str) and task_id:
        refs.append(f"task:{task_id}")
    if isinstance(request_id, str) and request_id:
        refs.append(f"request:{request_id}")
    for raw in ctx.decision_ledger_tail:
        if isinstance(raw, str) and raw:
            refs.append(f"ledger:{raw}")
    return refs


def _system_prompt_section_names(ctx: ToolContext) -> list[str]:
    raw = ctx.system_prompt_sections
    if not isinstance(raw, dict):
        return []
    names: list[str] = []
    for key in ("static_sections", "dynamic_sections"):
        values = raw.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
    return _dedupe(names)


def _system_prompt_cache_break_sections(ctx: ToolContext) -> list[str]:
    raw = ctx.system_prompt_sections
    if not isinstance(raw, dict):
        return []
    sections = raw.get("cache_break_sections")
    if not isinstance(sections, list):
        return []
    return _dedupe([item for item in sections if isinstance(item, str)])


def _active_skill_name(ctx: ToolContext) -> str | None:
    raw_invoked_state = ctx.invoked_skill_state
    if isinstance(raw_invoked_state, dict):
        active_runtime = raw_invoked_state.get("active_runtime")
        if isinstance(active_runtime, dict):
            name = active_runtime.get("skill_name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return None


def _invoked_skill_names(ctx: ToolContext) -> list[str]:
    names: list[str] = []
    raw_invoked_state = ctx.invoked_skill_state
    if isinstance(raw_invoked_state, dict):
        skills = raw_invoked_state.get("skills")
        if isinstance(skills, dict):
            for skill_name in skills.keys():
                if isinstance(skill_name, str) and skill_name.strip():
                    names.append(skill_name.strip())
    active_skill_name = _active_skill_name(ctx)
    if active_skill_name:
        names.append(active_skill_name)
    return _dedupe(names)


def _task_profile_lines(task_spec: TaskSpec | None) -> list[str]:
    if task_spec is None:
        return []
    return [f"父任务概要：risk={task_spec.risk_level}"]


def _expected_output(
    task_spec: TaskSpec | None,
    review_requirements: ReviewRequirements | None,
) -> str | None:
    if task_spec is None:
        return None
    if task_spec.risk_level == "high":
        return "风险任务：产物须含明确证据。"
    return None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        value = str(raw).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _wrap_plaintext_result(text: str) -> SubagentResultEnvelope:
    sections = {
        "findings": [],
        "decisions": [],
        "open_questions": [],
        "artifact_refs": [],
        "recommended_next_actions": [],
    }
    heading_map = {
        "findings": "findings",
        "finding": "findings",
        "decisions": "decisions",
        "decision": "decisions",
        "open_questions": "open_questions",
        "open_question": "open_questions",
        "questions": "open_questions",
        "question": "open_questions",
        "artifact_refs": "artifact_refs",
        "artifacts": "artifact_refs",
        "artifact": "artifact_refs",
        "recommended_next_actions": "recommended_next_actions",
        "next_actions": "recommended_next_actions",
        "next action": "recommended_next_actions",
        "next actions": "recommended_next_actions",
    }
    summary_lines: list[str] = []
    active_section: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if active_section is None:
                summary_lines.append("")
            continue
        normalized = line.rstrip(":").lower().replace(" ", "_")
        if normalized in heading_map:
            active_section = heading_map[normalized]
            continue
        if active_section is not None and line.startswith(("- ", "* ")):
            value = line[2:].strip()
            if value:
                sections[active_section].append(value)
            continue
        if active_section is None:
            summary_lines.append(line)
            continue
        sections[active_section].append(line)
    summary = "\n".join(part for part in summary_lines if part != "").strip()
    if not summary:
        summary = text
    return SubagentResultEnvelope(summary=summary, **sections)


def _collect_prior_evidence(session_state: Any) -> list[EvidenceRef]:
    """从 SessionContextState 中提取前轮证据"""
    raw_evidence = getattr(session_state, "evidence", [])
    if not raw_evidence:
        return []
    items: list[EvidenceRef] = []
    for e in raw_evidence[:20]:
        if isinstance(e, EvidenceRef):
            items.append(e)
        elif isinstance(e, dict):
            try:
                items.append(EvidenceRef.model_validate(e))
            except Exception:
                pass
    return items
