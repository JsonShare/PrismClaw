"""Session projection engines — read models derived from SessionTree.

Each projector encapsulates a specific read-path concern:
- MessageProjector: build LLM-visible messages from entries
- TaskProjection: task runtime/spec/review queries
- ForkTaskProjector: fork/parallel task queries
- CacheProjector: cache strategy snapshot queries
- SearchProjector: search/extract evidence queries
- ReplacementProjector: replacement decision queries
- HistoryCompactionProjector: compaction artifact queries
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from prismclaw.schema.agent.data_types import ContentBlock
from prismclaw.schema.agent.session import (
    AgentMessageDict,
    SessionContextState,
    SystemReminderProjection,
    TurnRuntimeMetricsSnapshot,
)
from prismclaw.schema.agent.task_runtime import (
    EvidenceRecord,
    ReviewDecisionRecord,
    TaskMutation,
    TaskRuntimeState,
)
from prismclaw.schema.agent.task_types import TaskSpec
from prismclaw.schema.content.compaction import (
    CompactionArtifact,
    parse_compaction_artifact,
)
from prismclaw.schema.content.replacement_state import ReplacementDecisionState
from prismclaw.schema.session.types import (
    EVIDENCE_LEDGER_CUSTOM_TYPE,
    EXTRACT_EVIDENCE_CUSTOM_TYPE,
    FORK_TASK_PROJECTION_CUSTOM_TYPE,
    REVIEW_DECISION_CUSTOM_TYPE,
    SEARCH_EVIDENCE_CUSTOM_TYPE,
    SESSION_MEMORY_CUSTOM_TYPE,
    SESSION_STATE_CUSTOM_TYPE,
    TASK_MUTATION_CUSTOM_TYPE,
    TASK_RUNTIME_CUSTOM_TYPE,
    TASK_SPEC_CUSTOM_TYPE,
    TURN_RUNTIME_METRICS_CUSTOM_TYPE,
    CompactionEntry,
    CustomEntry,
    MessageEntry,
    SessionEntry,
    SessionTree,
)
from prismclaw.schema.shared.sanitize_injection import sanitize_before_context_injection

logger = logging.getLogger(__name__)

# P3-FUS-01: L3 压缩摘要渲染前脱敏


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
            return [
                SystemReminderProjection.history_compaction_summary(
                    summary_text=self._render_summary_with_source_ref(entry), entry_id=entry.id
                )
            ]
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
                (
                    "compaction summary rendered without read-back "
                    "boundary: entry_id=%s head=%s tail=%s"
                ),
                entry.id,
                bool(head),
                bool(tail),
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
        return _find_latest_custom_model(
            self._tree.get_branch(), TASK_RUNTIME_CUSTOM_TYPE, TaskRuntimeState
        )

    def get_latest_task_spec(self) -> TaskSpec | None:
        return _find_latest_custom_model(self._tree.get_branch(), TASK_SPEC_CUSTOM_TYPE, TaskSpec)

    def get_evidence_ledger(self) -> list[EvidenceRecord]:
        return _collect_custom_models(
            self._tree.get_branch(), EVIDENCE_LEDGER_CUSTOM_TYPE, EvidenceRecord
        )

    def get_latest_review_decision(self) -> ReviewDecisionRecord | None:
        return _find_latest_custom_model(
            self._tree.get_branch(), REVIEW_DECISION_CUSTOM_TYPE, ReviewDecisionRecord
        )

    def get_task_mutations(self) -> list[TaskMutation]:
        return _collect_custom_models(
            self._tree.get_branch(), TASK_MUTATION_CUSTOM_TYPE, TaskMutation
        )


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


class CacheProjector:
    def __init__(self, tree: SessionTree) -> None:
        self._tree = tree

    def get_turn_metrics_snapshots(self) -> list[TurnRuntimeMetricsSnapshot]:
        return _collect_turn_runtime_metrics_snapshots(self._tree.get_branch())


class SearchProjector:
    def __init__(self, tree: SessionTree) -> None:
        self._tree = tree

    def get_search_entries(self) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for entry in self._tree.get_branch():
            if (
                not isinstance(entry, CustomEntry)
                or entry.custom_type != SEARCH_EVIDENCE_CUSTOM_TYPE
            ):
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
            if (
                not isinstance(entry, CustomEntry)
                or entry.custom_type != SEARCH_EVIDENCE_CUSTOM_TYPE
            ):
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
            if (
                not isinstance(entry, CustomEntry)
                or entry.custom_type != EXTRACT_EVIDENCE_CUSTOM_TYPE
            ):
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
            if (
                not isinstance(entry, CustomEntry)
                or entry.custom_type != EXTRACT_EVIDENCE_CUSTOM_TYPE
            ):
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
        from prismclaw.schema.content.replacement_store import collect_replacement_decision_state

        state = collect_replacement_decision_state(self._tree.get_branch())
        logger.debug(
            "ReplacementProjector.get_state: tree_id=%s decisions=%d digest=%s",
            getattr(self._tree, "session_id", "?"),
            state.decision_count(),
            state.state_digest(),
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
        "error_count": sum(1 for entry in effective_entries if entry.get("fork_status") == "error"),
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


def _effective_fork_task_projection_entries(
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    effective_reversed: list[dict[str, Any]] = []
    seen_task_ids: set[str] = set()
    for entry in reversed(entries):
        task_id = entry.get("task_id")
        if isinstance(task_id, str) and task_id:
            if task_id in seen_task_ids:
                continue
            seen_task_ids.add(task_id)
        effective_reversed.append(dict(entry))
    effective_reversed.reverse()
    return effective_reversed


def _message_entry_to_dict(
    entry: MessageEntry,
    *,
    content_override: str | list[ContentBlock] | None = None,
) -> AgentMessageDict:
    content: Any
    if content_override is not None:
        entry_content = content_override
    elif entry.enriched_content is not None:
        entry_content = entry.enriched_content
    else:
        entry_content = entry.content
    if entry.role == "tool" and isinstance(entry_content, str):
        content = [{"type": "text", "text": entry_content}]
    elif isinstance(entry_content, list):
        content = _content_blocks_to_llm(entry_content)
    else:
        content = entry_content
    msg: AgentMessageDict = {
        "role": entry.role,
        "content": content,
        "__entry_id__": entry.id,
    }
    if entry.tool_calls:
        msg["tool_calls"] = entry.tool_calls
    if entry.tool_call_id:
        msg["tool_call_id"] = entry.tool_call_id
    return msg


def _collect_custom_models[TModel: BaseModel](
    branch: list[SessionEntry],
    custom_type: str,
    model_cls: type[TModel],
) -> list[TModel]:
    models: list[TModel] = []
    for entry in branch:
        if not isinstance(entry, CustomEntry) or entry.custom_type != custom_type:
            continue
        if not isinstance(entry.data, dict):
            continue
        try:
            models.append(model_cls.model_validate(entry.data))
        except Exception:
            continue
    return models


def _find_latest_custom_model[TModel: BaseModel](
    branch: list[SessionEntry],
    custom_type: str,
    model_cls: type[TModel],
) -> TModel | None:
    for entry in reversed(branch):
        if not isinstance(entry, CustomEntry) or entry.custom_type != custom_type:
            continue
        if not isinstance(entry.data, dict):
            continue
        try:
            return model_cls.model_validate(entry.data)
        except Exception:
            continue
    return None


def _collect_turn_runtime_metrics_snapshots(
    branch: list[SessionEntry],
) -> list[TurnRuntimeMetricsSnapshot]:
    snapshots: list[TurnRuntimeMetricsSnapshot] = []
    for entry in branch:
        if (
            not isinstance(entry, CustomEntry)
            or entry.custom_type != TURN_RUNTIME_METRICS_CUSTOM_TYPE
        ):
            continue
        snapshot = _coerce_turn_runtime_metrics_snapshot(entry.data)
        if snapshot is not None:
            snapshots.append(snapshot)
    return snapshots


def _coerce_turn_runtime_metrics_snapshot(data: object) -> TurnRuntimeMetricsSnapshot | None:
    if not isinstance(data, dict):
        return None
    dynamic_payload = data.get("dynamic_payload")
    input_tokens = data.get("input_tokens")
    cache_read_tokens = data.get("cache_read_tokens")
    result_payload_reduction = data.get("result_payload_reduction")
    if not isinstance(dynamic_payload, int):
        return None
    if not isinstance(input_tokens, int):
        return None
    if not isinstance(cache_read_tokens, int):
        return None
    if not isinstance(result_payload_reduction, int):
        return None
    snapshot = TurnRuntimeMetricsSnapshot(
        dynamic_payload=dynamic_payload,
        input_tokens=input_tokens,
        cache_read_tokens=cache_read_tokens,
        result_payload_reduction=result_payload_reduction,
    )
    return snapshot


def _history_compaction_reduction(entry: CompactionEntry) -> int:
    tokens_before = entry.tokens_before if entry.tokens_before > 0 else 0
    tokens_after: int | None = None
    details = entry.details or {}
    raw_tokens_after = details.get("tokens_after")
    if isinstance(raw_tokens_after, int):
        tokens_after = raw_tokens_after
    elif isinstance(raw_tokens_after, float):
        tokens_after = int(raw_tokens_after)
    if tokens_after is None:
        return 0
    return max(0, tokens_before - max(0, tokens_after))


def _content_blocks_to_llm(blocks: list[ContentBlock]) -> list[dict[str, Any]]:
    from prismclaw.schema.content.content_types import FileBlock, ImageBlock, TextBlock
    from prismclaw.schema.shared.utils import file_reference_text

    result: list[dict[str, Any]] = []
    for block in blocks:
        if isinstance(block, ImageBlock):
            result.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{block.mime_type};base64,{block.data}"},
                }
            )
        elif isinstance(block, TextBlock):
            if not block.text or not block.text.strip():
                continue
            result.append({"type": "text", "text": block.text})
        elif isinstance(block, FileBlock):
            if not block.path or not block.path.strip():
                continue
            result.append(
                {
                    "type": "text",
                    "text": file_reference_text(
                        block.path.strip(),
                        name=block.name,
                        mime_type=block.mime_type,
                    ),
                }
            )
    return result
