"""Content types: tool calls, compaction, evidence, preservation, workspace."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prismclaw.schema.content.compaction import (
        AssembleResult as AssembleResult,
    )
    from prismclaw.schema.content.compaction import (
        CompactionArtifact as CompactionArtifact,
    )
    from prismclaw.schema.content.compaction import (
        CompactionArtifactType as CompactionArtifactType,
    )
    from prismclaw.schema.content.compaction import (
        CompactionBoundary as CompactionBoundary,
    )
    from prismclaw.schema.content.compaction import (
        CompactionReasonCode as CompactionReasonCode,
    )
    from prismclaw.schema.content.compaction import (
        CompactResult as CompactResult,
    )
    from prismclaw.schema.content.compaction import (
        DecisionLedgerEvent as DecisionLedgerEvent,
    )
    from prismclaw.schema.content.content_types import (
        FileChangeEntry as FileChangeEntry,
    )
    from prismclaw.schema.content.content_types import (
        PersistenceArtifact as PersistenceArtifact,
    )
    from prismclaw.schema.content.content_types import (
        ReferenceArtifact as ReferenceArtifact,
    )
    from prismclaw.schema.content.content_types import (
        StateDeltaArtifact as StateDeltaArtifact,
    )
    from prismclaw.schema.content.content_types import (
        ToolCall as ToolCall,
    )
    from prismclaw.schema.content.content_types import (
        ToolContextModifier as ToolContextModifier,
    )
    from prismclaw.schema.content.content_types import (
        ToolResult as ToolResult,
    )
    from prismclaw.schema.content.content_types import (
        ToolSkillInvocation as ToolSkillInvocation,
    )
    from prismclaw.schema.content.content_types import (
        UiArtifact as UiArtifact,
    )
    from prismclaw.schema.content.context import (
        ContextBlock as ContextBlock,
    )
    from prismclaw.schema.content.context import (
        ContextPackLayout as ContextPackLayout,
    )
    from prismclaw.schema.content.context import (
        ContextPriority as ContextPriority,
    )
    from prismclaw.schema.content.context import (
        ContextStability as ContextStability,
    )
    from prismclaw.schema.content.context import (
        ContextZone as ContextZone,
    )
    from prismclaw.schema.content.context import (
        TaskMaterialType as TaskMaterialType,
    )
    from prismclaw.schema.content.context import (
        TaskProfile as TaskProfile,
    )
    from prismclaw.schema.content.evidence import (
        ContextGraph as ContextGraph,
    )
    from prismclaw.schema.content.evidence import (
        ContextGraphEdge as ContextGraphEdge,
    )
    from prismclaw.schema.content.evidence import (
        EvidencePacket as EvidencePacket,
    )
    from prismclaw.schema.content.evidence import (
        EvidencePacketKind as EvidencePacketKind,
    )
    from prismclaw.schema.content.evidence import (
        EvidenceRelation as EvidenceRelation,
    )
    from prismclaw.schema.content.evidence import (
        EvidenceStrategy as EvidenceStrategy,
    )
    from prismclaw.schema.content.evidence import (
        EvidenceTemporalMode as EvidenceTemporalMode,
    )
    from prismclaw.schema.content.preservation import (
        LossClassification as LossClassification,
    )
    from prismclaw.schema.content.preservation import (
        PreservationContract as PreservationContract,
    )
    from prismclaw.schema.content.preservation import (
        PreservationItem as PreservationItem,
    )
    from prismclaw.schema.content.preservation import (
        PreservationItemKind as PreservationItemKind,
    )
    from prismclaw.schema.content.preservation import (
        SemanticCapsule as SemanticCapsule,
    )
    from prismclaw.schema.content.replacement_state import (
        ReplacementScope as ReplacementScope,
    )
    from prismclaw.schema.content.streaming import (
        AgentEvent as AgentEvent,
    )
    from prismclaw.schema.content.streaming import (
        Done as Done,
    )
    from prismclaw.schema.content.streaming import (
        ErrorEvent as ErrorEvent,
    )
    from prismclaw.schema.content.streaming import (
        HeartbeatDetail as HeartbeatDetail,
    )
    from prismclaw.schema.content.streaming import (
        HeartbeatEvent as HeartbeatEvent,
    )
    from prismclaw.schema.content.streaming import (
        IterationBoundary as IterationBoundary,
    )
    from prismclaw.schema.content.streaming import (
        ReasoningChunk as ReasoningChunk,
    )
    from prismclaw.schema.content.streaming import (
        SubagentPartialStatus as SubagentPartialStatus,
    )
    from prismclaw.schema.content.streaming import (
        SubagentProgressBatch as SubagentProgressBatch,
    )
    from prismclaw.schema.content.streaming import (
        SubagentProgressItem as SubagentProgressItem,
    )
    from prismclaw.schema.content.streaming import (
        TextChunk as TextChunk,
    )
    from prismclaw.schema.content.streaming import (
        ToolCallEnd as ToolCallEnd,
    )
    from prismclaw.schema.content.streaming import (
        ToolCallStart as ToolCallStart,
    )
    from prismclaw.schema.content.streaming import (
        ToolClarifyRequest as ToolClarifyRequest,
    )
    from prismclaw.schema.content.subagent import (
        SubagentContextSnapshot as SubagentContextSnapshot,
    )
    from prismclaw.schema.content.subagent import (
        SubagentResultEnvelope as SubagentResultEnvelope,
    )
    from prismclaw.schema.content.workspace import (
        SandboxTier as SandboxTier,
    )


def __getattr__(name: str) -> object:
    """Lazy re-export to avoid circular imports."""
    _lazy = {
        # Content-defined types
        "AgentEvent": ("prismclaw.schema.content.streaming", "AgentEvent"),
        "AssembleResult": ("prismclaw.schema.content.compaction", "AssembleResult"),
        "CompactResult": ("prismclaw.schema.content.compaction", "CompactResult"),
        "CompactionArtifact": ("prismclaw.schema.content.compaction", "CompactionArtifact"),
        "CompactionArtifactScope": (
            "prismclaw.schema.content.compaction",
            "CompactionArtifactScope",
        ),
        "CompactionArtifactType": ("prismclaw.schema.content.compaction", "CompactionArtifactType"),
        "CompactionBoundary": ("prismclaw.schema.content.compaction", "CompactionBoundary"),
        "CompactionPreservedSegment": (
            "prismclaw.schema.content.compaction",
            "CompactionPreservedSegment",
        ),
        "CompactionReasonCode": ("prismclaw.schema.content.compaction", "CompactionReasonCode"),
        "CompressionValidationResult": (
            "prismclaw.schema.content.compaction",
            "CompressionValidationResult",
        ),
        "ContextBlock": ("prismclaw.schema.content.context", "ContextBlock"),
        "ContextGraph": ("prismclaw.schema.content.evidence", "ContextGraph"),
        "ContextGraphEdge": ("prismclaw.schema.content.evidence", "ContextGraphEdge"),
        "ContextPackLayout": ("prismclaw.schema.content.context", "ContextPackLayout"),
        "ContextPriority": ("prismclaw.schema.content.context", "ContextPriority"),
        "ContextStability": ("prismclaw.schema.content.context", "ContextStability"),
        "ContextZone": ("prismclaw.schema.content.context", "ContextZone"),
        "DecisionLedgerEvent": ("prismclaw.schema.content.compaction", "DecisionLedgerEvent"),
        "Done": ("prismclaw.schema.content.streaming", "Done"),
        "ErrorEvent": ("prismclaw.schema.content.streaming", "ErrorEvent"),
        "EvidencePacket": ("prismclaw.schema.content.evidence", "EvidencePacket"),
        "EvidencePacketKind": ("prismclaw.schema.content.evidence", "EvidencePacketKind"),
        "EvidenceRelation": ("prismclaw.schema.content.evidence", "EvidenceRelation"),
        "EvidenceStrategy": ("prismclaw.schema.content.evidence", "EvidenceStrategy"),
        "EvidenceTemporalMode": ("prismclaw.schema.content.evidence", "EvidenceTemporalMode"),
        "FileChangeEntry": ("prismclaw.schema.content.content_types", "FileChangeEntry"),
        "HeartbeatDetail": ("prismclaw.schema.content.streaming", "HeartbeatDetail"),
        "HeartbeatEvent": ("prismclaw.schema.content.streaming", "HeartbeatEvent"),
        "IterationBoundary": ("prismclaw.schema.content.streaming", "IterationBoundary"),
        "LossClassification": ("prismclaw.schema.content.preservation", "LossClassification"),
        "PersistenceArtifact": ("prismclaw.schema.content.content_types", "PersistenceArtifact"),
        "PreservationContract": ("prismclaw.schema.content.preservation", "PreservationContract"),
        "PreservationItem": ("prismclaw.schema.content.preservation", "PreservationItem"),
        "PreservationItemKind": ("prismclaw.schema.content.preservation", "PreservationItemKind"),
        "ReasoningChunk": ("prismclaw.schema.content.streaming", "ReasoningChunk"),
        "ReferenceArtifact": ("prismclaw.schema.content.content_types", "ReferenceArtifact"),
        "ReplacementDecision": (
            "prismclaw.schema.content.replacement_state",
            "ReplacementDecision",
        ),
        "ReplacementDecisionState": (
            "prismclaw.schema.content.replacement_state",
            "ReplacementDecisionState",
        ),
        "ReplacementScope": ("prismclaw.schema.content.replacement_state", "ReplacementScope"),
        "RuntimeControlArtifact": (
            "prismclaw.schema.content.content_types",
            "RuntimeControlArtifact",
        ),
        "SandboxTier": ("prismclaw.schema.content.workspace", "SandboxTier"),
        "SemanticCapsule": ("prismclaw.schema.content.preservation", "SemanticCapsule"),
        "StateDeltaArtifact": ("prismclaw.schema.content.content_types", "StateDeltaArtifact"),
        "SubagentContextSnapshot": ("prismclaw.schema.content.subagent", "SubagentContextSnapshot"),
        "SubagentEnvelopeArtifact": (
            "prismclaw.schema.content.content_types",
            "SubagentEnvelopeArtifact",
        ),
        "SubagentPartialStatus": ("prismclaw.schema.content.streaming", "SubagentPartialStatus"),
        "SubagentProgressBatch": ("prismclaw.schema.content.streaming", "SubagentProgressBatch"),
        "SubagentProgressItem": ("prismclaw.schema.content.streaming", "SubagentProgressItem"),
        "SubagentResultEnvelope": ("prismclaw.schema.content.subagent", "SubagentResultEnvelope"),
        "TaskMaterialType": ("prismclaw.schema.content.context", "TaskMaterialType"),
        "TaskProfile": ("prismclaw.schema.content.context", "TaskProfile"),
        "TextChunk": ("prismclaw.schema.content.streaming", "TextChunk"),
        "ToolCall": ("prismclaw.schema.content.content_types", "ToolCall"),
        "ToolCallEnd": ("prismclaw.schema.content.streaming", "ToolCallEnd"),
        "ToolCallStart": ("prismclaw.schema.content.streaming", "ToolCallStart"),
        "ToolClarifyRequest": ("prismclaw.schema.content.streaming", "ToolClarifyRequest"),
        "ToolContextModifier": ("prismclaw.schema.content.content_types", "ToolContextModifier"),
        "ToolResult": ("prismclaw.schema.content.content_types", "ToolResult"),
        "ToolSkillExecutionStrategy": (
            "prismclaw.schema.content.content_types",
            "ToolSkillExecutionStrategy",
        ),
        "ToolSkillInvocation": ("prismclaw.schema.content.content_types", "ToolSkillInvocation"),
        "UiArtifact": ("prismclaw.schema.content.content_types", "UiArtifact"),
        "append_replacement_decisions": (
            "prismclaw.schema.content.replacement_store",
            "append_replacement_decisions",
        ),
        "coerce_compaction_artifact": (
            "prismclaw.schema.content.compaction",
            "coerce_compaction_artifact",
        ),
        "collect_replacement_decision_state": (
            "prismclaw.schema.content.replacement_store",
            "collect_replacement_decision_state",
        ),
        "extract_replacement_decisions_from_blocks": (
            "prismclaw.schema.content.replacement_store",
            "extract_replacement_decisions_from_blocks",
        ),
        "parse_compaction_artifact": (
            "prismclaw.schema.content.compaction",
            "parse_compaction_artifact",
        ),
        "parse_replacement_decision": (
            "prismclaw.schema.content.replacement_store",
            "parse_replacement_decision",
        ),
        "replacement_decision_from_artifact": (
            "prismclaw.schema.content.replacement_store",
            "replacement_decision_from_artifact",
        ),
    }
    if name in _lazy:
        import importlib

        mod = importlib.import_module(_lazy[name][0])
        return getattr(mod, _lazy[name][1])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
