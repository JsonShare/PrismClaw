"""Session projection engines — read models derived from SessionTree.

Each projector encapsulates a specific read-path concern:
- MessageProjector: build LLM-visible messages from entries
- TaskProjection: task runtime/spec/review queries
- ForkTaskProjector: fork/parallel task queries
- HandoffProjector: handoff request queries
- CacheProjector: cache strategy snapshot queries
- SearchProjector: search/extract evidence queries
- ReplacementProjector: replacement decision queries
- HistoryCompactionProjector: compaction artifact queries
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from prismclaw.schema.content.replacement_state import ReplacementDecisionState

logger = logging.getLogger(__name__)

# P3-FUS-01: L3 压缩摘要渲染前脱敏
from prismclaw.schema.shared.sanitize_injection import sanitize_before_context_injection

from prismclaw.schema.content import (
    CompactionArtifact,
    parse_compaction_artifact,
)
from prismclaw.schema.agent.session import (
    AgentMessageDict,
    SessionContextState,
    SystemReminderProjection,
)
from prismclaw.schema.agent.task_types import TaskSpec
from prismclaw.schema.session import (
    CompactionEntry,
    CustomEntry,
    EVIDENCE_LEDGER_CUSTOM_TYPE,
    EXTRACT_EVIDENCE_CUSTOM_TYPE,
    FORK_TASK_PROJECTION_CUSTOM_TYPE,
    HANDOFF_REQUEST_CUSTOM_TYPE,
    MessageEntry,
    REVIEW_DECISION_CUSTOM_TYPE,
    REVIEW_REQUIREMENTS_CUSTOM_TYPE,
    SEARCH_EVIDENCE_CUSTOM_TYPE,
    SESSION_MEMORY_CUSTOM_TYPE,
    SESSION_STATE_CUSTOM_TYPE,
    SessionEntry,
    SessionTree,
    TASK_MUTATION_CUSTOM_TYPE,
    TASK_RUNTIME_CUSTOM_TYPE,
    TASK_SPEC_CUSTOM_TYPE,
    _collect_custom_models,
    _collect_turn_runtime_metrics_snapshots,
    _effective_fork_task_projection_entries,
    _find_latest_custom_model,
    _history_compaction_reduction,
    _latest_custom_entry_index,
    _message_entry_to_dict,
)
from prismclaw.schema.agent.task_runtime import (
    EvidenceRecord,
    ReviewDecisionRecord,
    ReviewRequirements,
    TaskMutation,
    TaskRuntimeState,
)


class MessageProjector:
    def __init__(self, tree: SessionTree) -> None:
        self._tree = tree

    def build_runtime_messages(self) -> list[AgentMessageDict]:
        branch = self._tree.get_branch()
        latest_compaction_idx: int | None = None
        for i, entry in enumerate(branch):
            if isinstance(entry, CompactionEntry):
                latest_compaction_idx = i
        messages: list[AgentMessageDict] = []
        if latest_compaction_idx is not None:
            compaction = branch[latest_compaction_idx]
            assert isinstance(compaction, CompactionEntry)
            first_kept_idx: int | None = None
            for i, entry in enumerate(branch):
                if entry.id == compaction.first_kept_entry_id:
                    first_kept_idx = i
                    break
            start_idx = first_kept_idx if first_kept_idx is not None else latest_compaction_idx + 1
            tail = branch[start_idx:]
            for entry in tail:
                if isinstance(entry, MessageEntry) and entry.id != compaction.id:
                    messages.append(_message_entry_to_dict(entry))
        else:
            for entry in branch:
                if isinstance(entry, MessageEntry):
                    messages.append(_message_entry_to_dict(entry))
        return messages

    def build_runtime_meta_messages(self) -> list[SystemReminderProjection]:
        branch = self._tree.get_branch()
        for entry in reversed(branch):
            if not isinstance(entry, CompactionEntry):
                continue
            return [SystemReminderProjection.history_compaction_summary(
                summary_text=self._render_summary_with_source_ref(entry),
                entry_id=entry.id)]
        return []

    @staticmethod
    def _render_summary_with_source_ref(entry: CompactionEntry) -> str:
        """Append a source-reference line to the summary at render time.

        The reference is *derived* from the CompactionEntry's structured
        boundary metadata — never stored in the summary text itself, so
        it survives any number of chained compactions without loss.
        """
        boundary = (entry.details or {}).get("boundary") or {}
        head = boundary.get("source_head_entry_id")
        tail = boundary.get("source_tail_entry_id")
        if not head or not tail:
            # boundary 缺失 → 摘要无法附带回读范围，LLM 将无法 read_compaction_source 回读原始数据。
            logger.warning(
                "compaction summary rendered without read-back boundary: entry_id=%s head=%s tail=%s",
                entry.id, bool(head), bool(tail),
            )
            # P3-FUS-01: 压缩摘要渲染前 Safe Harbor 脱敏
            sanitized, _ = sanitize_before_context_injection(
                entry.summary or "", layer="L3", source="compaction_summary"
            )
            return sanitized
        # P3-FUS-01: 压缩摘要渲染前 Safe Harbor 脱敏
        summary, _ = sanitize_before_context_injection(
            entry.summary or "", layer="L3", source="compaction_summary"
        )
        return (
            f"{summary}\n\n"
            f"[SYSTEM NOTE: 上述为压缩摘要。原始对话已归档，范围 entry_id={head}..{tail}。"
            f"当摘要缺少你需要的细节（数值、日期、原话）时，禁止凭空补全。]"
        )


class TaskProjection:
    def __init__(self, tree: SessionTree) -> None:
        self._tree = tree

    def get_latest_task_runtime(self) -> TaskRuntimeState | None:
        return _find_latest_custom_model(self._tree.get_branch(), TASK_RUNTIME_CUSTOM_TYPE, TaskRuntimeState)

    def get_latest_task_spec(self) -> TaskSpec | None:
        return _find_latest_custom_model(self._tree.get_branch(), TASK_SPEC_CUSTOM_TYPE, TaskSpec)

    def get_latest_review_requirements(self) -> ReviewRequirements | None:
        return _find_latest_custom_model(self._tree.get_branch(), REVIEW_REQUIREMENTS_CUSTOM_TYPE, ReviewRequirements)

    def get_evidence_ledger(self) -> list[EvidenceRecord]:
        return _collect_custom_models(self._tree.get_branch(), EVIDENCE_LEDGER_CUSTOM_TYPE, EvidenceRecord)

    def get_latest_review_decision(self) -> ReviewDecisionRecord | None:
        return _find_latest_custom_model(self._tree.get_branch(), REVIEW_DECISION_CUSTOM_TYPE, ReviewDecisionRecord)

    def get_task_mutations(self) -> list[TaskMutation]:
        return _collect_custom_models(self._tree.get_branch(), TASK_MUTATION_CUSTOM_TYPE, TaskMutation)


class ForkTaskProjector:
    def __init__(self, tree: SessionTree) -> None:
        self._tree = tree

    def get_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for entry in self._tree.get_branch():
            if not isinstance(entry, CustomEntry):
                continue
            if entry.custom_type != FORK_TASK_PROJECTION_CUSTOM_TYPE:
                continue
            if isinstance(entry.data, dict):
                entries.append(dict(entry.data))
        return entries

    def get_latest(self, *, task_id: str | None = None) -> dict[str, Any] | None:
        entries = self.get_entries()
        if task_id is None:
            return entries[-1] if entries else None
        for entry in reversed(entries):
            if entry.get("task_id") == task_id:
                return entry
        return None

    def get_summary(self) -> dict[str, Any] | None:
        entries = self.get_entries()
        if not entries:
            return None
        effective = _effective_fork_task_projection_entries(entries)
        return _build_fork_task_projection_summary(entries=entries, effective_entries=effective)

    def get_attention(self) -> dict[str, Any] | None:
        entries = self.get_entries()
        if not entries:
            return None
        effective = _effective_fork_task_projection_entries(entries)
        return _build_fork_task_attention(entries=entries, effective_entries=effective)


class HandoffProjector:
    def __init__(self, tree: SessionTree) -> None:
        self._tree = tree

    def get_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for entry in self._tree.get_branch():
            if not isinstance(entry, CustomEntry):
                continue
            if entry.custom_type != HANDOFF_REQUEST_CUSTOM_TYPE:
                continue
            if isinstance(entry.data, dict):
                entries.append(dict(entry.data))
        return entries

    def get_latest(self) -> dict[str, Any] | None:
        entries = self.get_entries()
        return entries[-1] if entries else None

    def get_follow_up_attention(self) -> dict[str, Any] | None:
        latest_handoff = self.get_latest()
        fork_attention = ForkTaskProjector(self._tree).get_attention()
        if latest_handoff is None and fork_attention is None:
            return None
        latest_handoff_index = _latest_custom_entry_index(self._tree, custom_type=HANDOFF_REQUEST_CUSTOM_TYPE)
        latest_fork_index = _latest_custom_entry_index(self._tree, custom_type=FORK_TASK_PROJECTION_CUSTOM_TYPE)
        if latest_handoff is not None and (fork_attention is None or latest_handoff_index is None or latest_fork_index is None or latest_handoff_index >= latest_fork_index):
            attention: dict[str, Any] = {"kind": "handoff", "recommended_reason": "handoff_request"}
            target = latest_handoff.get("target")
            if isinstance(target, str) and target:
                attention["recommended_target"] = target
            task = latest_handoff.get("task")
            if isinstance(task, str) and task:
                attention["recommended_task"] = task
            source_subagent = latest_handoff.get("source_subagent")
            if isinstance(source_subagent, str) and source_subagent:
                attention["source_subagent"] = source_subagent
            source_skill_origin = latest_handoff.get("source_skill_origin")
            if isinstance(source_skill_origin, str) and source_skill_origin:
                attention["source_skill_origin"] = source_skill_origin
            if latest_handoff.get("source_is_fork") is True:
                attention["source_is_fork"] = True
            return attention
        if fork_attention is None:
            return None
        return {"kind": "fork", **dict(fork_attention)}


class CacheProjector:
    def __init__(self, tree: SessionTree) -> None:
        self._tree = tree

    def get_turn_metrics_snapshots(self) -> list[Any]:
        return _collect_turn_runtime_metrics_snapshots(self._tree.get_branch())

class SearchProjector:
    def __init__(self, tree: SessionTree) -> None:
        self._tree = tree

    def get_search_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for entry in self._tree.get_branch():
            if not isinstance(entry, CustomEntry) or entry.custom_type != SEARCH_EVIDENCE_CUSTOM_TYPE:
                continue
            if isinstance(entry.data, dict):
                entries.append(dict(entry.data))
        return entries

    def get_latest_search(self) -> dict[str, Any] | None:
        entries = self.get_search_entries()
        return entries[-1] if entries else None

    def get_current_branch(self) -> list[SessionEntry]:
        branch = self._tree.get_branch()
        if not branch:
            return []
        start_idx = 0
        for idx in range(len(branch) - 1, -1, -1):
            entry = branch[idx]
            if isinstance(entry, MessageEntry) and entry.role == "user":
                start_idx = idx
                break
        return branch[start_idx:]

    def get_current_search_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for entry in self.get_current_branch():
            if not isinstance(entry, CustomEntry) or entry.custom_type != SEARCH_EVIDENCE_CUSTOM_TYPE:
                continue
            if isinstance(entry.data, dict):
                entries.append(dict(entry.data))
        return entries

    def get_latest_current_search(self) -> dict[str, Any] | None:
        entries = self.get_current_search_entries()
        return entries[-1] if entries else None

    def get_extract_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for entry in self._tree.get_branch():
            if not isinstance(entry, CustomEntry) or entry.custom_type != EXTRACT_EVIDENCE_CUSTOM_TYPE:
                continue
            if isinstance(entry.data, dict):
                entries.append(dict(entry.data))
        return entries

    def get_latest_extract(self) -> dict[str, Any] | None:
        entries = self.get_extract_entries()
        return entries[-1] if entries else None

    def get_current_extract_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for entry in self.get_current_branch():
            if not isinstance(entry, CustomEntry) or entry.custom_type != EXTRACT_EVIDENCE_CUSTOM_TYPE:
                continue
            if isinstance(entry.data, dict):
                entries.append(dict(entry.data))
        return entries

    def get_latest_current_extract(self) -> dict[str, Any] | None:
        entries = self.get_current_extract_entries()
        return entries[-1] if entries else None


class ReplacementProjector:
    def __init__(self, tree: SessionTree) -> None:
        self._tree = tree

    def get_state(self) -> ReplacementDecisionState:
        from prismclaw.schema.content import collect_replacement_decision_state
        state = collect_replacement_decision_state(self._tree.get_branch())
        logger.debug(
            "ReplacementProjector.get_state: tree_id=%s decisions=%d digest=%s",
            getattr(self._tree, "session_id", "?"), state.decision_count(), state.state_digest(),
        )
        return state

    def get_persisted_artifacts(self) -> dict[str, CompactionArtifact]:
        return self.get_state().to_artifact_map()


class HistoryCompactionProjector:
    def __init__(self, tree: SessionTree) -> None:
        self._tree = tree

    def get_count(self) -> int:
        return sum(1 for entry in self._tree.get_branch() if isinstance(entry, CompactionEntry))

    def get_latest_artifact(self) -> CompactionArtifact | None:
        for entry in reversed(self._tree.get_branch()):
            if not isinstance(entry, CompactionEntry):
                continue
            artifact = parse_compaction_artifact(entry.details)
            if artifact is None or artifact.artifact_type != "history_compaction_summary":
                continue
            return artifact
        return None

    def get_latest_reduction(self) -> int:
        for entry in reversed(self._tree.get_branch()):
            if not isinstance(entry, CompactionEntry):
                continue
            return _history_compaction_reduction(entry)
        return 0


class SessionProjectionEngine:
    def __init__(self, tree: SessionTree) -> None:
        self.tree = tree
        self.messages = MessageProjector(tree)
        self.task = TaskProjection(tree)
        self.fork = ForkTaskProjector(tree)
        self.handoff = HandoffProjector(tree)
        self.cache = CacheProjector(tree)
        self.search = SearchProjector(tree)
        self.replacement = ReplacementProjector(tree)
        self.history_compaction = HistoryCompactionProjector(tree)

    def get_latest_session_memory_state(self) -> SessionContextState | None:
        for entry in reversed(self.tree.get_branch()):
            if not isinstance(entry, CustomEntry):
                continue
            if entry.custom_type not in {SESSION_MEMORY_CUSTOM_TYPE, SESSION_STATE_CUSTOM_TYPE}:
                continue
            if not isinstance(entry.data, dict):
                continue
            try:
                return SessionContextState.model_validate(entry.data)
            except Exception:
                continue
        return None


def _build_fork_task_projection_summary(
    *,
    entries: list[dict[str, Any]],
    effective_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "projection_count": len(entries),
        "task_count": len(effective_entries),
        "completed_count": sum(
            1 for entry in effective_entries if entry.get("fork_status") == "completed"
        ),
        "error_count": sum(
            1 for entry in effective_entries if entry.get("fork_status") == "error"
        ),
    }
    latest = entries[-1]
    task_id = latest.get("task_id")
    if isinstance(task_id, str) and task_id:
        summary["latest_task_id"] = task_id
    agent_name = latest.get("agent_name")
    if isinstance(agent_name, str) and agent_name:
        summary["latest_agent_name"] = agent_name
    fork_status = latest.get("fork_status")
    if isinstance(fork_status, str) and fork_status:
        summary["latest_fork_status"] = fork_status
    batch_id = latest.get("batch_id")
    if isinstance(batch_id, str) and batch_id:
        summary["latest_batch_id"] = batch_id
    return summary


def _build_fork_task_attention(
    *,
    entries: list[dict[str, Any]],
    effective_entries: list[dict[str, Any]],
) -> dict[str, Any] | None:
    attention_task_ids = [
        task_id
        for entry in reversed(effective_entries)
        for task_id in [entry.get("task_id")]
        if isinstance(task_id, str) and task_id and entry.get("fork_status") == "error"
    ]
    if attention_task_ids:
        return {
            "attention_task_ids": attention_task_ids,
            "attention_count": len(attention_task_ids),
            "recommended_task_id": attention_task_ids[0],
            "recommended_reason": "error",
        }

    latest = entries[-1]
    latest_task_id = latest.get("task_id")
    if isinstance(latest_task_id, str) and latest_task_id:
        return {
            "recommended_task_id": latest_task_id,
            "recommended_reason": "latest_result",
        }
    return None