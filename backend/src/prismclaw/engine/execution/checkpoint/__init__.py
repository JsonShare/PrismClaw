"""Run-state checkpoint — snapshot, persistence, state machine."""
from __future__ import annotations

from prismclaw.engine.execution.checkpoint.models import (
    BatchRuntimeRecord,
    CommitState,
    CompressionRuntimeState,
    CompressionState,
    EffectClass,
    InvokedSkillRecord,
    InvokedSkillState,
    LoopState,
    PromptCacheRuntimeState,
    RecoveryState,
    RequestAssemblySnapshot,
    ResumeBoundary,
    ResumeDisposition,
    RetryState,
    RunState,
    SystemPromptSectionRecord,
    SystemPromptSectionState,
    ToolEffectRecord,
)
from prismclaw.engine.execution.checkpoint.persistence import (
    CURRENT_RUN_SNAPSHOT_VERSION,
    RUN_SNAPSHOT_CUSTOM_TYPE,
    ActiveRunContextSnapshot,
    OwnershipRecord,
    ResumeDecision,
    RunSnapshot,
    build_empty_run_snapshot,
    collect_latest_run_snapshot,
    evaluate_resume_decision,
    is_recovery_probe_resumable,
    load_run_snapshot_from_branch,
    load_run_snapshot_payload,
    parse_ownership_record,
    parse_run_snapshot,
    parse_run_snapshot_json,
    recovery_probe_required,
)
from prismclaw.engine.execution.checkpoint.session_persistence import (
    load_run_snapshot_for_tree,
    persist_run_snapshot,
)
from prismclaw.engine.execution.checkpoint.state_machine import (
    RunStateMachine,
)

from prismclaw.schema.persistence.persistence import RunSnapshotStore

__all__ = [
    # models
    "BatchRuntimeRecord", "CommitState", "CompressionRuntimeState",
    "CompressionState", "EffectClass", "InvokedSkillRecord", "InvokedSkillState",
    "LoopState", "PromptCacheRuntimeState",
    "RecoveryState", "RequestAssemblySnapshot", "ResumeBoundary", "ResumeDisposition",
    "RetryState", "RunState", "SystemPromptSectionRecord",
    "SystemPromptSectionState", "ToolEffectRecord",
    # persistence
    "CURRENT_RUN_SNAPSHOT_VERSION", "RUN_SNAPSHOT_CUSTOM_TYPE",
    "ActiveRunContextSnapshot", "OwnershipRecord", "ResumeDecision", "RunSnapshot",
    "build_empty_run_snapshot", "collect_latest_run_snapshot",
    "evaluate_resume_decision", "is_recovery_probe_resumable",
    "load_run_snapshot_from_branch", "load_run_snapshot_payload",
    "parse_ownership_record", "parse_run_snapshot", "parse_run_snapshot_json",
    "recovery_probe_required",
    # session_persistence
    "load_run_snapshot_for_tree", "persist_run_snapshot",
    # state_machine
    "RunStateMachine",
    # re-export from schema
    "RunSnapshotStore",
]
