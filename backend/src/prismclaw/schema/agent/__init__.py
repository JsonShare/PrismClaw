"""Agent sub-contracts: types, context, memory, dream, session routing, security, review."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prismclaw.schema.agent.capability import (
        CapabilityKind as CapabilityKind,
    )
    from prismclaw.schema.agent.capability import (
        CapabilityStatus as CapabilityStatus,
    )
    from prismclaw.schema.agent.context import (
        BootstrapContextProvider as BootstrapContextProvider,
    )
    from prismclaw.schema.agent.context import (
        ContextAssembler as ContextAssembler,
    )
    from prismclaw.schema.agent.context import (
        ContextCompactor as ContextCompactor,
    )
    from prismclaw.schema.agent.context import (
        ContextEngine as ContextEngine,
    )
    from prismclaw.schema.agent.context import (
        ContextIngestor as ContextIngestor,
    )
    from prismclaw.schema.agent.context import (
        ContextTurnFinalizer as ContextTurnFinalizer,
    )
    from prismclaw.schema.agent.context import (
        HasBootstrapProvider as HasBootstrapProvider,
    )
    from prismclaw.schema.agent.context import (
        HasCompactionSettings as HasCompactionSettings,
    )
    from prismclaw.schema.agent.context import (
        HasMemoryProvider as HasMemoryProvider,
    )
    from prismclaw.schema.agent.context import (
        MemoryAftermathBoundary as MemoryAftermathBoundary,
    )
    from prismclaw.schema.agent.context import (
        MemoryContextProvider as MemoryContextProvider,
    )
    from prismclaw.schema.agent.curator import (
        CuratorSopRepository as CuratorSopRepository,
    )
    from prismclaw.schema.agent.curator_types import (
        ArchivedSopRow as ArchivedSopRow,
    )
    from prismclaw.schema.agent.curator_types import (
        ReviewDecision as ReviewDecision,
    )
    from prismclaw.schema.agent.curator_types import (
        ReviewOutcome as ReviewOutcome,
    )
    from prismclaw.schema.agent.curator_types import (
        SopRow as SopRow,
    )
    from prismclaw.schema.agent.domains import (
        InvocationPolicy as InvocationPolicy,
    )
    from prismclaw.schema.agent.domains import (
        PluginErrorType as PluginErrorType,
    )
    from prismclaw.schema.agent.domains import (
        SkillSource as SkillSource,
    )
    from prismclaw.schema.agent.dream import (
        DreamDispatchProtocol as DreamDispatchProtocol,
    )
    from prismclaw.schema.agent.memory import (
        MemoryGatewayProtocol as MemoryGatewayProtocol,
    )
    from prismclaw.schema.agent.review import (
        ContentRef as ContentRef,
    )
    from prismclaw.schema.agent.review import (
        ContentStore as ContentStore,
    )
    from prismclaw.schema.agent.review import (
        ReviewServiceProtocol as ReviewServiceProtocol,
    )
    from prismclaw.schema.agent.run_control_types import (
        RunControl as RunControl,
    )
    from prismclaw.schema.agent.run_control_types import (
        SteerMessage as SteerMessage,
    )
    from prismclaw.schema.agent.security import (
        AllowedToolsPolicy as AllowedToolsPolicy,
    )
    from prismclaw.schema.agent.security import (
        ConsentProtocol as ConsentProtocol,
    )
    from prismclaw.schema.agent.security import (
        RuntimeMode as RuntimeMode,
    )
    from prismclaw.schema.agent.security import (
        SignerProtocol as SignerProtocol,
    )
    from prismclaw.schema.agent.security_store import (
        ConsentStoreProtocol as ConsentStoreProtocol,
    )
    from prismclaw.schema.agent.session import (
        GoalRecord as GoalRecord,
    )
    from prismclaw.schema.agent.session import (
        SessionContextItem as SessionContextItem,
    )
    from prismclaw.schema.agent.session import (
        SessionContextState as SessionContextState,
    )
    from prismclaw.schema.agent.session_router import (
        SessionRouterProtocol as SessionRouterProtocol,
    )
    from prismclaw.schema.agent.task_runtime import (
        AcceptanceEvaluation as AcceptanceEvaluation,
    )
    from prismclaw.schema.agent.task_runtime import (
        EvidenceRecord as EvidenceRecord,
    )
    from prismclaw.schema.agent.task_runtime import (
        ReviewDecisionRecord as ReviewDecisionRecord,
    )
    from prismclaw.schema.agent.task_runtime import (
        TaskRuntimeState as TaskRuntimeState,
    )
    from prismclaw.schema.agent.task_types import (
        TaskSpec as TaskSpec,
    )
    from prismclaw.schema.agent.types import (
        AgentDeps as AgentDeps,
    )
    from prismclaw.schema.agent.types import (
        RunRequest as RunRequest,
    )
    from prismclaw.schema.content.compaction import (
        DecisionLedgerEvent as DecisionLedgerEvent,
    )
    from prismclaw.schema.content.streaming import (
        Done as Done,
    )
    from prismclaw.schema.content.streaming import (
        ErrorEvent as ErrorEvent,
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
        SubagentProgressBatch as SubagentProgressBatch,
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


def __getattr__(name: str) -> object:
    """Lazy re-export to avoid circular imports."""
    _lazy = {
        "AgentDeps": ("prismclaw.schema.agent.types", "AgentDeps"),
        "RunRequest": ("prismclaw.schema.agent.types", "RunRequest"),
        "RunControl": ("prismclaw.schema.agent.run_control_types", "RunControl"),
        "SteerMessage": ("prismclaw.schema.agent.run_control_types", "SteerMessage"),
        "BootstrapContextProvider": ("prismclaw.schema.agent.context", "BootstrapContextProvider"),
        "ContextAssembler": ("prismclaw.schema.agent.context", "ContextAssembler"),
        "ContextCompactor": ("prismclaw.schema.agent.context", "ContextCompactor"),
        "ContextEngine": ("prismclaw.schema.agent.context", "ContextEngine"),
        "ContextIngestor": ("prismclaw.schema.agent.context", "ContextIngestor"),
        "ContextTurnFinalizer": ("prismclaw.schema.agent.context", "ContextTurnFinalizer"),
        "HasBootstrapProvider": ("prismclaw.schema.agent.context", "HasBootstrapProvider"),
        "HasCompactionSettings": ("prismclaw.schema.agent.context", "HasCompactionSettings"),
        "HasMemoryProvider": ("prismclaw.schema.agent.context", "HasMemoryProvider"),
        "MemoryAftermathBoundary": ("prismclaw.schema.agent.context", "MemoryAftermathBoundary"),
        "MemoryContextProvider": ("prismclaw.schema.agent.context", "MemoryContextProvider"),
        "MemoryGatewayProtocol": ("prismclaw.schema.agent.memory", "MemoryGatewayProtocol"),
        "DreamDispatchProtocol": ("prismclaw.schema.agent.dream", "DreamDispatchProtocol"),
        "SessionRouterProtocol": ("prismclaw.schema.agent.session_router", "SessionRouterProtocol"),
        "ConsentProtocol": ("prismclaw.schema.agent.security", "ConsentProtocol"),
        "AllowedToolsPolicy": ("prismclaw.schema.agent.security", "AllowedToolsPolicy"),
        "RuntimeMode": ("prismclaw.schema.agent.security", "RuntimeMode"),
        "SignerProtocol": ("prismclaw.schema.agent.security", "SignerProtocol"),
        "AggregateStoreProtocol": (
            "prismclaw.schema.agent.security_store",
            "AggregateStoreProtocol",
        ),
        "BreakGlassStoreProtocol": (
            "prismclaw.schema.agent.security_store",
            "BreakGlassStoreProtocol",
        ),
        "ConsentStoreProtocol": ("prismclaw.schema.agent.security_store", "ConsentStoreProtocol"),
        "ContentRef": ("prismclaw.schema.agent.review", "ContentRef"),
        "ContentStore": ("prismclaw.schema.agent.review", "ContentStore"),
        "ReviewServiceProtocol": ("prismclaw.schema.agent.review", "ReviewServiceProtocol"),
        "CapabilityKind": ("prismclaw.schema.agent.capability", "CapabilityKind"),
        "CapabilityStatus": ("prismclaw.schema.agent.capability", "CapabilityStatus"),
        "TaskRuntimeState": ("prismclaw.schema.agent.task_runtime", "TaskRuntimeState"),
        "TaskSpec": ("prismclaw.schema.agent.task_types", "TaskSpec"),
        "SkillSource": ("prismclaw.schema.agent.domains", "SkillSource"),
        "InvocationPolicy": ("prismclaw.schema.agent.domains", "InvocationPolicy"),
        "PluginErrorType": ("prismclaw.schema.agent.domains", "PluginErrorType"),
        "SessionContextItem": ("prismclaw.schema.agent.session", "SessionContextItem"),
        "SessionContextState": ("prismclaw.schema.agent.session", "SessionContextState"),
        "ReviewDecisionRecord": ("prismclaw.schema.agent.task_runtime", "ReviewDecisionRecord"),
        "EvidenceRecord": ("prismclaw.schema.agent.task_runtime", "EvidenceRecord"),
        "AcceptanceEvaluation": ("prismclaw.schema.agent.task_runtime", "AcceptanceEvaluation"),
        "GoalRecord": ("prismclaw.schema.agent.session", "GoalRecord"),
        "CuratorSopRepository": ("prismclaw.schema.agent.curator", "CuratorSopRepository"),
        "CuratorSopEntry": ("prismclaw.schema.agent.curator_types", "SopRow"),
        "ArchivedSopRow": ("prismclaw.schema.agent.curator_types", "ArchivedSopRow"),
        "DecisionLedgerEvent": ("prismclaw.schema.content.compaction", "DecisionLedgerEvent"),
        "ReviewDecision": ("prismclaw.schema.agent.curator_types", "ReviewDecision"),
        "ReviewOutcome": ("prismclaw.schema.agent.curator_types", "ReviewOutcome"),
        "Done": ("prismclaw.schema.content.streaming", "Done"),
        "ErrorEvent": ("prismclaw.schema.content.streaming", "ErrorEvent"),
        "SopRow": ("prismclaw.schema.agent.curator_types", "SopRow"),
        "HeartbeatEvent": ("prismclaw.schema.content.streaming", "HeartbeatEvent"),
        "IterationBoundary": ("prismclaw.schema.content.streaming", "IterationBoundary"),
        "ReasoningChunk": ("prismclaw.schema.content.streaming", "ReasoningChunk"),
        "SubagentProgressBatch": ("prismclaw.schema.content.streaming", "SubagentProgressBatch"),
        "TextChunk": ("prismclaw.schema.content.streaming", "TextChunk"),
        "ToolClarifyRequest": ("prismclaw.schema.content.streaming", "ToolClarifyRequest"),
        "ToolCallEnd": ("prismclaw.schema.content.streaming", "ToolCallEnd"),
        "ToolCallStart": ("prismclaw.schema.content.streaming", "ToolCallStart"),
    }
    if name in _lazy:
        import importlib

        mod = importlib.import_module(_lazy[name][0])
        return getattr(mod, _lazy[name][1])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
