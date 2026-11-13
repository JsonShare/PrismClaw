"""Pure helper functions shared by the loop assembly (deps / tree_writer /
entry_runtime) and the run preparer.

All functions in this module are *pure* (or close to it): they do not depend on
``AgentRunnerDeps`` or any class instance state.  They operate on plain data
objects (SessionTree, TaskSpec, TaskRuntimeState, etc.) and return new values.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any, cast

from prismclaw.schema.agent.session import MessageEntry
from prismclaw.schema.agent.task_runtime import (
    EvidenceRecord,
    TaskRuntimeState,
)
from prismclaw.schema.agent.task_types import TaskSpec
from prismclaw.schema.session.types import SessionTree


# ---------------------------------------------------------------------------
# Session-tree helpers
# ---------------------------------------------------------------------------

def _user_message_count(tree: SessionTree) -> int:
    """1-based user-message counter over tree.order (turn 语义).

    跳过 ``metadata.kind == "clarification_answer"`` 的 user entry——
    HITL 澄清答案是人机交互回合的延续，不是新的用户问题；Chat 页面
    (``_build_display_messages``) 已据此抑制为独立气泡，trajectory
    的 turn 划分必须与之对齐，否则会把"跳过提问 → agent 续执行"
    渲染成新 Turn 里的独立咨询内容。
    """
    count = 0
    for eid in tree.order:
        e = tree.entries.get(eid)
        if isinstance(e, MessageEntry) and e.role == "user":
            if isinstance(e.metadata, dict) and e.metadata.get("kind") == "clarification_answer":
                continue
            count += 1
    return count


def _node_seq(tree: SessionTree, entry_id: str) -> int:
    """1-based position of entry_id in tree.order (canonical truth)."""
    return tree.order.index(entry_id) + 1 if entry_id in tree.order else len(tree.order)


def _tool_args_for_call(tree: SessionTree, tool_call_id: str | None) -> str | None:
    """Reverse-lookup a tool call's raw arguments from the issuing assistant entry.

    tool MessageEntry 只携带 tool_call_id（结果在 content 上，参数在发起
    调用的 assistant entry 的 tool_calls 里）。从 tree.order 末尾向前找第
    一个含该 call_id 的 assistant entry —— 同迭代内发起者就在近邻，扫描
    有界。返回 arguments 原文（str 直传 / dict JSON 序列化）。
    """
    if not tool_call_id:
        return None
    for eid in reversed(tree.order):
        entry = tree.entries.get(eid)
        if not isinstance(entry, MessageEntry) or entry.role != "assistant":
            continue
        for tc in entry.tool_calls or []:
            try:
                tc_dict = tc if isinstance(tc, dict) else tc.model_dump()
                if tc_dict.get("id") != tool_call_id:
                    continue
                args = (
                    tc_dict.get("arguments")
                    or (tc_dict.get("function") or {}).get("arguments")
                )
                if args is None:
                    return None
                if isinstance(args, str):
                    return args or None
                import json as _json
                return _json.dumps(args, ensure_ascii=False, default=str)
            except Exception:
                continue
    return None


# ---------------------------------------------------------------------------
# Text / anchor helpers
# ---------------------------------------------------------------------------

def _normalize_anchor_text(text: str, *, max_length: int = 240) -> str:
    normalized = " ".join(text.strip().split())
    normalized = normalized.strip(" \t\r\n;；,，。")
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 1].rstrip() + "…"


def _decision_scope_for_task_spec(task_spec: TaskSpec) -> str:
    constraints = [item.strip() for item in task_spec.constraints if item.strip()]
    scope_parts = [
        f"goal={_normalize_anchor_text(task_spec.goal, max_length=240)}",
        f"risk={task_spec.risk_level}",
        "constraints=" + "|".join(dict.fromkeys(constraints)),
        f"attachments_present={'true' if task_spec.attachments_present else 'false'}",
    ]
    digest = hashlib.sha256("\n".join(scope_parts).encode("utf-8")).hexdigest()[:24]
    return f"scope:{digest}"


# ---------------------------------------------------------------------------
# Task-runtime construction
# ---------------------------------------------------------------------------

def _build_task_runtime_from_spec(
    *,
    task_spec: TaskSpec,
    workspace_id: str | None,
    prior_runtime: TaskRuntimeState | None = None,
    entry_resolution_status: str = "ready",
    entry_failure_code: str | None = None,
    state_override: str | None = None,
) -> TaskRuntimeState:
    created_at_epoch_ms = (
        prior_runtime.created_at_epoch_ms if prior_runtime is not None else int(time.time() * 1000)
    )
    updated_at_epoch_ms = int(time.time() * 1000)
    requires_risk_confirmation = entry_resolution_status == "ready" and state_override is None and (
        task_spec.risk_level in {"high", "critical"} or bool(
        task_spec.preflight_probe is not None and task_spec.preflight_probe.baseline_risk == "high"
        )
    )
    if entry_resolution_status == "pending":
        runtime_state = "intaking"
    elif entry_resolution_status == "failed":
        runtime_state = "blocked"
    elif state_override is not None:
        runtime_state = state_override
    elif requires_risk_confirmation:
        runtime_state = "awaiting_risk_confirmation"
    else:
        runtime_state = "executing"
    confirmation_required = requires_risk_confirmation
    pending_confirmation_reason = (
        "当前任务包含高风险执行，确认后才会继续进入执行主链。"
        if requires_risk_confirmation
        else None
    )
    return TaskRuntimeState(
        task_id=task_spec.task_id,
        session_id=task_spec.session_id,
        workspace_id=workspace_id,
        state=cast(Any, runtime_state),
        entry_resolution_status=cast(Any, entry_resolution_status),
        entry_failure_code=entry_failure_code,
        state_version=prior_runtime.state_version if prior_runtime is not None else 1,
        latest_review_decision_id=(
            prior_runtime.latest_review_decision_id if prior_runtime is not None else None
        ),
        risk_level=task_spec.risk_level,
        confirmation_required=confirmation_required,
        pending_confirmation_reason=pending_confirmation_reason,
        checkpoint_cursor=prior_runtime.checkpoint_cursor if prior_runtime is not None else None,
        last_mutation_id=prior_runtime.last_mutation_id if prior_runtime is not None else None,
        created_at_epoch_ms=created_at_epoch_ms,
        updated_at_epoch_ms=updated_at_epoch_ms,
    )


# ---------------------------------------------------------------------------
# Evidence builders (pure — only data transformation, no I/O)
# ---------------------------------------------------------------------------

def _build_recovery_probe_evidence(
    *,
    task_runtime: TaskRuntimeState,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=f"{task_runtime.task_id}:ev:{uuid.uuid4().hex[:12]}",
        task_id=task_runtime.task_id,
        tool_name="recovery_probe",
        status="ok",
        origin="system",
    )


def _build_workspace_state_evidence(
    *,
    task_runtime: TaskRuntimeState,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=f"{task_runtime.task_id}:ev:{uuid.uuid4().hex[:12]}",
        task_id=task_runtime.task_id,
        tool_name="workspace_state",
        status="ok",
        origin="system",
    )


def _build_preflight_probe_evidence(
    *,
    task_runtime: TaskRuntimeState,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=f"{task_runtime.task_id}:ev:{uuid.uuid4().hex[:12]}",
        task_id=task_runtime.task_id,
        tool_name="preflight_probe",
        status="ok",
        origin="system",
    )


def _build_manual_confirmation_evidence(
    *,
    task_runtime: TaskRuntimeState,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=f"{task_runtime.task_id}:ev:{uuid.uuid4().hex[:12]}",
        task_id=task_runtime.task_id,
        tool_name="manual_confirmation",
        status="ok",
        origin="system",
    )
