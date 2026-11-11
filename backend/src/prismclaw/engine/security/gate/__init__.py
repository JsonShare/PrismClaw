"""gate 子域 — 核心门控基础设施。"""
from prismclaw.engine.security.gate.types import (
    ActorIdentity,
    AllowedToolsPolicy,
    RuntimeMode,
    TaskRuntimeContext,
    SubagentProgressSink,
    ParallelTaskExtras,
    ConsentStoreProtocol,
    AggregateStoreProtocol,
    BreakGlassStoreProtocol,
    WormAuditProtocol,
    ConfirmationChannelProtocol,
    InMemoryAggregateStore,
    NullBreakGlassStore,
    InMemoryWormAudit,
    StubConfirmationChannel,
)
from prismclaw.engine.security.gate.policies import (
    DataResidency,
    WorkspaceTag,
    WorkspaceTagValidator,
    MfaProvider,
    NullMfaProvider,
    WebAuthnMfaProvider,
    build_mfa_provider,
    TenantPolicyOverride,
    TenantPolicyStore,
    AggregateAction,
    AggregateThreshold,
    AggregateDecision,
    SessionAggregateGuard,
    RedisAggregateStore,
    create_aggregate_store,
)
from prismclaw.engine.security.gate.routing.risk_router import (
    SideEffectScope,
    DataClassification,
    Reversibility,
    ToolSafetyMeta,
    get_tool_safety_meta,
    RiskAssessment,
    RiskRouter,
    _TIER_ORDER,
)
from prismclaw.engine.security.gate.routing.rule_gate import RuleGate, RuleGateResult
from prismclaw.engine.security.gate.routing.confirm_gateway import (
    ConfirmationLevel,
    ConfirmationRequest,
    ConfirmationResponse,
    ConfirmationResult,
    compute_signed_payload,
    verify_signed_payload,
    ConfirmGateway,
)
from prismclaw.engine.security.gate.routing.four_eyes_router import (
    FourEyesAssignment,
    FourEyesRouter,
)
from prismclaw.engine.security.gate.gate import GateDecision, SafetyGate
from prismclaw.engine.security.gate.constraint_guard import ConstraintBreach
from prismclaw.engine.security.gate.probes import run_preflight_probe, run_recovery_probe

__all__ = [
    # types (protocols + stubs + actor_identity + runtime_context)
    "ActorIdentity",
    "AllowedToolsPolicy",
    "RuntimeMode",
    "TaskRuntimeContext",
    "SubagentProgressSink",
    "ParallelTaskExtras",
    "ConsentStoreProtocol",
    "AggregateStoreProtocol",
    "BreakGlassStoreProtocol",
    "WormAuditProtocol",
    "ConfirmationChannelProtocol",
    "InMemoryAggregateStore",
    "NullBreakGlassStore",
    "InMemoryWormAudit",
    "StubConfirmationChannel",
    # policies (workspace_tag + mfa + tenant_policy + aggregate_guard)
    "DataResidency",
    "WorkspaceTag",
    "WorkspaceTagValidator",
    "MfaProvider",
    "NullMfaProvider",
    "WebAuthnMfaProvider",
    "build_mfa_provider",
    "TenantPolicyOverride",
    "TenantPolicyStore",
    "AggregateAction",
    "AggregateThreshold",
    "AggregateDecision",
    "SessionAggregateGuard",
    "RedisAggregateStore",
    "create_aggregate_store",
    # risk_router
    "SideEffectScope",
    "DataClassification",
    "Reversibility",
    "ToolSafetyMeta",
    "get_tool_safety_meta",
    "RiskAssessment",
    "RiskRouter",
    "_TIER_ORDER",
    # rule_gate
    "RuleGate",
    "RuleGateResult",
    # confirm_gateway
    "ConfirmationLevel",
    "ConfirmationRequest",
    "ConfirmationResponse",
    "ConfirmationResult",
    "compute_signed_payload",
    "verify_signed_payload",
    "ConfirmGateway",
    # four_eyes_router
    "FourEyesAssignment",
    "FourEyesRouter",
    # gate
    "GateDecision",
    "SafetyGate",
    # constraint_guard
    "ConstraintBreach",
    # probes
    "run_preflight_probe",
    "run_recovery_probe",
]
