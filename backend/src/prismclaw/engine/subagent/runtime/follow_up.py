from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from prismclaw.engine.subagent.runtime.parallel import (
    PARALLEL_TASK_RUNTIME_CUSTOM_TYPE,
    ParallelTaskRuntimeRecord,
    parallel_task_records_for_tree,
)
from prismclaw.schema.session.types import CustomEntry, HANDOFF_REQUEST_CUSTOM_TYPE, SessionTree


@dataclass(frozen=True)
class SessionFollowUpSnapshot:
    latest_handoff_request: dict[str, Any] | None = None
    parallel_task_summary: dict[str, Any] | None = None
    parallel_task_attention: dict[str, Any] | None = None
    follow_up_attention: dict[str, Any] | None = None

    def is_empty(self) -> bool:
        return (
            self.latest_handoff_request is None
            and self.parallel_task_summary is None
            and self.parallel_task_attention is None
            and self.follow_up_attention is None
        )


def session_follow_up_snapshot(session_tree: Any) -> SessionFollowUpSnapshot:
    summary = _build_parallel_task_summary(session_tree)
    attention = _build_parallel_task_attention(session_tree)
    return SessionFollowUpSnapshot(
        latest_handoff_request=_read_tree_dict(session_tree, "get_latest_handoff_request"),
        parallel_task_summary=summary,
        parallel_task_attention=attention,
        follow_up_attention=_build_follow_up_attention(
            session_tree,
            latest_handoff_request=_read_tree_dict(session_tree, "get_latest_handoff_request"),
            parallel_task_attention=attention,
        ),
    )


def format_parallel_task_summary(summary: dict[str, Any]) -> str:
    parts: list[str] = []
    task_count = summary.get("task_count")
    if isinstance(task_count, int):
        parts.append(f"tasks={task_count}")
    completed_count = summary.get("completed_count")
    if isinstance(completed_count, int):
        parts.append(f"completed={completed_count}")
    error_count = summary.get("error_count")
    if isinstance(error_count, int):
        parts.append(f"errors={error_count}")
    latest_task_id = summary.get("latest_task_id")
    if isinstance(latest_task_id, str) and latest_task_id:
        parts.append(f"latest_task_id={latest_task_id}")
    latest_status = summary.get("latest_status")
    if isinstance(latest_status, str) and latest_status:
        parts.append(f"latest_status={latest_status}")
    latest_agent_name = summary.get("latest_agent_name")
    if isinstance(latest_agent_name, str) and latest_agent_name:
        parts.append(f"latest_agent={latest_agent_name}")
    running_count = summary.get("running_count")
    if isinstance(running_count, int) and running_count > 0:
        parts.append(f"running={running_count}")
    cancelled_count = summary.get("cancelled_count")
    if isinstance(cancelled_count, int) and cancelled_count > 0:
        parts.append(f"cancelled={cancelled_count}")
    if not parts:
        return "并行任务运行摘要可用。"
    return "并行任务运行摘要：" + "、".join(parts) + "。"


def format_parallel_task_attention(attention: dict[str, Any]) -> str:
    recommended_task_id = attention.get("recommended_task_id")
    recommended_reason = attention.get("recommended_reason")
    attention_count = attention.get("attention_count")
    if isinstance(recommended_task_id, str) and recommended_task_id:
        reason_text = (
            recommended_reason
            if isinstance(recommended_reason, str) and recommended_reason
            else "follow_up"
        )
        prefix = (
            f"并行任务待关注：请先检查 `{recommended_task_id}`"
            f"（原因：{reason_text}）"
        )
        if isinstance(attention_count, int) and attention_count > 1:
            return prefix + f"；共有 {attention_count} 个任务需要关注。"
        return prefix + "。"
    if isinstance(attention_count, int) and attention_count > 0:
        return f"并行任务待关注：共有 {attention_count} 个任务需要关注。"
    return "并行任务待关注信息可用。"


def format_handoff_request(request: dict[str, Any]) -> str:
    parts: list[str] = []
    target = request.get("target")
    if isinstance(target, str) and target:
        parts.append(f"target={target}")
    source_subagent = request.get("source_subagent")
    if isinstance(source_subagent, str) and source_subagent:
        parts.append(f"source_subagent={source_subagent}")
    source_skill_origin = request.get("source_skill_origin")
    if isinstance(source_skill_origin, str) and source_skill_origin:
        parts.append(f"source_skill_origin={source_skill_origin}")
    task = request.get("task")
    if isinstance(task, str) and task:
        preview = task if len(task) <= 120 else task[:117] + "..."
        parts.append(f"task={preview}")
    if not parts:
        return "已记录最近一次移交请求。"
    return "最近一次移交请求：" + " | ".join(parts) + "。"


def format_follow_up_attention(attention: dict[str, Any]) -> str:
    kind = attention.get("kind")
    if kind == "handoff":
        target = attention.get("recommended_target")
        reason = attention.get("recommended_reason")
        source_subagent = attention.get("source_subagent")
        handoff_parts: list[str] = []
        if isinstance(target, str) and target:
            handoff_parts.append(f"target={target}")
        if isinstance(source_subagent, str) and source_subagent:
            handoff_parts.append(f"source_subagent={source_subagent}")
        if isinstance(reason, str) and reason:
            handoff_parts.append(f"reason={reason}")
        if not handoff_parts:
            return "建议的会话后续动作：查看最近一次移交请求。"
        return "建议的会话后续动作：" + " | ".join(handoff_parts) + "。"
    if kind == "fork":
        task_id = attention.get("recommended_task_id")
        reason = attention.get("recommended_reason")
        fork_parts: list[str] = []
        if isinstance(task_id, str) and task_id:
            fork_parts.append(f"task_id={task_id}")
        if isinstance(reason, str) and reason:
            fork_parts.append(f"reason={reason}")
        if not fork_parts:
            return "建议的会话后续动作：查看最近的 fork 结果。"
        return "建议的会话后续动作：" + " | ".join(fork_parts) + "。"
    return "建议的会话后续动作可用。"


def render_follow_up_status(
    snapshot: SessionFollowUpSnapshot,
) -> tuple[str, dict[str, Any]] | None:
    if snapshot.is_empty():
        return None

    lines = ["会话后续状态："]
    metadata: dict[str, Any] = {}
    if snapshot.latest_handoff_request is not None:
        lines.append(format_handoff_request(snapshot.latest_handoff_request))
        metadata["latest_handoff_request"] = snapshot.latest_handoff_request
    if snapshot.parallel_task_summary is not None:
        lines.append(format_parallel_task_summary(snapshot.parallel_task_summary))
        metadata["parallel_task_summary"] = snapshot.parallel_task_summary
    if snapshot.parallel_task_attention is not None:
        lines.append(format_parallel_task_attention(snapshot.parallel_task_attention))
        metadata["parallel_task_attention"] = snapshot.parallel_task_attention
    if snapshot.follow_up_attention is not None:
        lines.append(format_follow_up_attention(snapshot.follow_up_attention))
        metadata["follow_up_attention"] = snapshot.follow_up_attention
    return "\n".join(lines), metadata


def build_session_follow_up_note(
    snapshot: SessionFollowUpSnapshot,
    *,
    available_tools: set[str],
) -> list[str]:
    attention = snapshot.follow_up_attention
    if attention is None:
        return []

    kind = attention.get("kind")
    if kind == "handoff":
        target = attention.get("recommended_target")
        source_subagent = attention.get("source_subagent")
        handoff_details: list[str] = ["kind=handoff"]
        if isinstance(target, str) and target:
            handoff_details.append(f"target={target}")
        if isinstance(source_subagent, str) and source_subagent:
            handoff_details.append(f"source_subagent={source_subagent}")
        if attention.get("source_is_fork") is True:
            handoff_details.append("source_is_fork=true")
        details_text = (
            f" ({', '.join(handoff_details)})" if handoff_details else ""
        )
        suffix = (
            "在再次委派或重复工作前，请先查看最近一次移交请求。"
        )
        return [f"会话后续动作可用{details_text}。{suffix}"]

    summary = snapshot.parallel_task_summary
    if summary is None:
        return []

    fork_details: list[str] = []
    task_count = summary.get("task_count")
    if isinstance(task_count, int) and task_count > 0:
        fork_details.append(f"tasks={task_count}")
    completed_count = summary.get("completed_count")
    if isinstance(completed_count, int) and completed_count > 0:
        fork_details.append(f"completed={completed_count}")
    error_count = summary.get("error_count")
    if isinstance(error_count, int) and error_count > 0:
        fork_details.append(f"errors={error_count}")
    latest_task_id = summary.get("latest_task_id")
    if isinstance(latest_task_id, str) and latest_task_id:
        fork_details.append(f"latest_task_id={latest_task_id}")
    latest_agent_name = summary.get("latest_agent_name")
    if isinstance(latest_agent_name, str) and latest_agent_name:
        fork_details.append(f"latest_agent={latest_agent_name}")
    latest_status = summary.get("latest_status")
    if isinstance(latest_status, str) and latest_status:
        fork_details.append(f"latest_status={latest_status}")
    attention_count = attention.get("attention_count")
    if isinstance(attention_count, int) and attention_count > 0:
        fork_details.append(f"needs_attention={attention_count}")
    recommended_task_id = attention.get("recommended_task_id")
    if isinstance(recommended_task_id, str) and recommended_task_id:
        fork_details.append(f"recommended_task_id={recommended_task_id}")
    recommended_reason = attention.get("recommended_reason")
    if isinstance(recommended_reason, str) and recommended_reason:
        fork_details.append(f"recommended_reason={recommended_reason}")

    suffix = (
        "在派生重复工作前，请先查看并行运行时状态。"
    )
    details_text = f"（{'，'.join(fork_details)}）" if fork_details else ""
    return [f"本会话存在并行 fork 状态{details_text}。{suffix}"]


def _runtime_parallel_records(session_tree: Any) -> list[ParallelTaskRuntimeRecord]:
    return parallel_task_records_for_tree(
        session_tree,
        include_terminal=True,
        kinds=None,
    )


def _build_parallel_task_summary(session_tree: Any) -> dict[str, Any] | None:
    records = _runtime_parallel_records(session_tree)
    if not records:
        return None
    latest = records[-1]
    summary: dict[str, Any] = {
        "task_count": len(records),
        "completed_count": sum(1 for record in records if record.status == "completed"),
        "error_count": sum(1 for record in records if record.status == "failed"),
        "cancelled_count": sum(1 for record in records if record.status == "cancelled"),
        "running_count": sum(
            1 for record in records if record.status in {"scheduled", "running"}
        ),
        "latest_task_id": latest.task_id,
        "latest_status": latest.status,
    }
    if isinstance(latest.agent_name, str) and latest.agent_name:
        summary["latest_agent_name"] = latest.agent_name
    return summary


def _build_parallel_task_attention(session_tree: Any) -> dict[str, Any] | None:
    records = _runtime_parallel_records(session_tree)
    if not records:
        return None
    attention_records = [
        record for record in records if record.status in {"failed", "cancelled"}
    ]
    if attention_records:
        ordered_task_ids = [record.task_id for record in reversed(attention_records)]
        latest = attention_records[-1]
        return {
            "attention_task_ids": ordered_task_ids,
            "attention_count": len(ordered_task_ids),
            "recommended_task_id": latest.task_id,
            "recommended_reason": latest.status,
        }
    latest = records[-1]
    return {
        "recommended_task_id": latest.task_id,
        "recommended_reason": (
            "latest_runtime" if latest.status in {"scheduled", "running"} else "latest_result"
        ),
    }


def _latest_custom_entry_index(
    session_tree: Any,
    *,
    custom_type: str | None = None,
    custom_types: frozenset[str] | None = None,
    predicate: Any = None,
) -> int | None:
    if not isinstance(session_tree, SessionTree):
        return None
    if custom_type is None and custom_types is None:
        return None
    latest_index: int | None = None
    for index, entry in enumerate(session_tree.get_branch()):
        if not isinstance(entry, CustomEntry):
            continue
        if custom_type is not None and entry.custom_type != custom_type:
            continue
        if custom_types is not None and entry.custom_type not in custom_types:
            continue
        if predicate is not None and not predicate(entry):
            continue
        latest_index = index
    return latest_index


def _build_follow_up_attention(
    session_tree: Any,
    *,
    latest_handoff_request: dict[str, Any] | None,
    parallel_task_attention: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if latest_handoff_request is None and parallel_task_attention is None:
        return None

    latest_handoff_index = _latest_custom_entry_index(
        session_tree,
        custom_type=HANDOFF_REQUEST_CUSTOM_TYPE,
    )
    latest_fork_index = _latest_custom_entry_index(
        session_tree,
        custom_type=PARALLEL_TASK_RUNTIME_CUSTOM_TYPE,
    )

    if latest_handoff_request is not None and (
        parallel_task_attention is None
        or latest_handoff_index is None
        or latest_fork_index is None
        or latest_handoff_index >= latest_fork_index
    ):
        attention = {"kind": "handoff", **latest_handoff_request}
        target = latest_handoff_request.get("target")
        if isinstance(target, str) and target:
            attention["recommended_target"] = target
        source_subagent = latest_handoff_request.get("source_subagent")
        if isinstance(source_subagent, str) and source_subagent:
            attention["source_subagent"] = source_subagent
        if latest_handoff_request.get("source_is_fork") is True:
            attention["source_is_fork"] = True
        attention["recommended_reason"] = "latest_handoff"
        return attention

    if parallel_task_attention is None:
        return None
    return {"kind": "fork", **parallel_task_attention}


def _read_tree_dict(tree: Any, method_name: str) -> dict[str, Any] | None:
    if tree is None:
        return None
    getter = getattr(tree, method_name, None)
    if not callable(getter):
        return None
    value = getter()
    return dict(value) if isinstance(value, dict) else None


__all__ = [
    "SessionFollowUpSnapshot",
    "build_session_follow_up_note",
    "format_parallel_task_attention",
    "format_parallel_task_summary",
    "format_follow_up_attention",
    "format_handoff_request",
    "render_follow_up_status",
    "session_follow_up_snapshot",
]
