from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CapabilityDecisionKind(str, Enum):
    PERMISSION = "permission"
    TOOL_GOVERNANCE = "tool_governance"
    TOOL_CALLABLE = "tool_callable"
    APPROVAL = "approval"
    DENY = "deny"
    SANDBOX = "sandbox"
    MEMORY = "memory"
    FORK = "fork"
    SKILL = "skill"


class FailureSeverity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"


@dataclass(frozen=True, slots=True)
class CapabilityDecisionEntry:
    entry_id: str
    file_path: str
    symbol: str
    summary: str
    decision_kinds: tuple[CapabilityDecisionKind, ...]
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    failure_source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CapabilityFailureSource:
    source_id: str
    severity: FailureSeverity
    summary: str
    impacted_entry_ids: tuple[str, ...]
    impacted_kinds: tuple[CapabilityDecisionKind, ...]


@dataclass(frozen=True, slots=True)
class CapabilityDecisionMap:
    schema_version: str
    entries: tuple[CapabilityDecisionEntry, ...]
    failure_sources: tuple[CapabilityFailureSource, ...]

    def entries_for_kind(self, kind: CapabilityDecisionKind) -> tuple[CapabilityDecisionEntry, ...]:
        return tuple(entry for entry in self.entries if kind in entry.decision_kinds)

    def find_entry(self, entry_id: str) -> CapabilityDecisionEntry | None:
        for entry in self.entries:
            if entry.entry_id == entry_id:
                return entry
        return None

    def find_failure_source(self, source_id: str) -> CapabilityFailureSource | None:
        for source in self.failure_sources:
            if source.source_id == source_id:
                return source
        return None

    def covered_file_paths(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(entry.file_path for entry in self.entries))


def build_capability_decision_map() -> CapabilityDecisionMap:
    entries = (
        CapabilityDecisionEntry(
            entry_id="pre_request_pipeline.capability_surface",
            file_path="backend/src/prismclaw/core/agent/pre_request_pipeline.py",
            symbol="PreRequestPipeline.run",
            summary=(
                "Resolves per-turn permission tier, visible tool names, and the tool "
                "schema fingerprint for the LLM callable set before request assembly."
            ),
            decision_kinds=(
                CapabilityDecisionKind.PERMISSION,
                CapabilityDecisionKind.TOOL_GOVERNANCE,
                CapabilityDecisionKind.SKILL,
            ),
            inputs=(
                "request.mode_override",
                "tool_ctx.mode",
                'runtime fallback "work"',
                "skill_invocation.allowed_tools_override",
                "turn_state.discovered_tool_ledger",
            ),
            outputs=(
                "permission_tier",
                "permission_source",
                "callable_tools",
                "tools_for_llm",
                "tool_pool_id",
            ),
            failure_source_ids=(
                "permission-tier-derived-in-pipeline",
                "skill-runtime-tool-constraint-projection",
            ),
        ),
        CapabilityDecisionEntry(
            entry_id="preservation_runtime.project_active_skill_invocation",
            file_path="backend/src/prismclaw/core/agent/compression/preservation.py",
            symbol="project_active_skill_invocation",
            summary=(
                "Projects active skill runtime into structured invoked_skill_state "
                "and prompt input tool constraints."
            ),
            decision_kinds=(
                CapabilityDecisionKind.SKILL,
                CapabilityDecisionKind.TOOL_GOVERNANCE,
            ),
            inputs=(
                "skill_invocation.allowed_tools_override",
                "snapshot.invoked_skill_state",
                "prompt_inputs.allowed_tools_override",
            ),
            outputs=(
                "snapshot.invoked_skill_state.active_runtime",
                "tool_ctx.invoked_skill_state",
                "prompt_inputs.allowed_tools_override",
            ),
            failure_source_ids=(
                "skill-runtime-tool-constraint-projection",
                "runtime-projection-synchronization",
            ),
        ),
        CapabilityDecisionEntry(
            entry_id="runner.run_agent_stream.tool_context",
            file_path="backend/src/prismclaw/core/agent/runner.py",
            symbol="run_agent_stream",
            summary=(
                "Constructs ToolContext by injecting the already-resolved sandbox "
                "policy object and typed service fields before downstream execution."
            ),
            decision_kinds=(CapabilityDecisionKind.SANDBOX,),
            inputs=(
                "request.sandbox_policy",
                "request.tool_context_extras",
                "deps.hooks",
            ),
            outputs=("tool_ctx.sandbox_policy", "tool_ctx.typed_fields"),
            failure_source_ids=(),
        ),
        CapabilityDecisionEntry(
            entry_id="tool_governance.resolve_registry_tool_governance",
            file_path="backend/src/prismclaw/core/agent/tools/governance.py",
            symbol="resolve_registry_tool_governance",
            summary=(
                "Builds governance input from the runtime registry and resolves the "
                "declared/visible/callable/searchable tool sets plus decision trace."
            ),
            decision_kinds=(
                CapabilityDecisionKind.TOOL_GOVERNANCE,
                CapabilityDecisionKind.PERMISSION,
                CapabilityDecisionKind.SKILL,
            ),
            inputs=(
                "skill_invocation.allowed_tools_override",
                "discovered_tool_names_from_ledger",
                "permission_tier",
                "registry.deferred",
            ),
            outputs=("resolved_toolset",),
            failure_source_ids=(),
        ),
        CapabilityDecisionEntry(
            entry_id="tool_registry.list_tools",
            file_path="backend/src/prismclaw/core/agent/tools/registry.py",
            symbol="ToolRegistry.list_tools",
            summary=("Lists registered tools and applies the allowed-name filter."),
            decision_kinds=(
                CapabilityDecisionKind.TOOL_CALLABLE,
                CapabilityDecisionKind.FORK,
                CapabilityDecisionKind.SANDBOX,
            ),
            inputs=(
                "allowed_names",
                "subagent_depth",
                "ToolContext.sandbox_policy",
            ),
            outputs=("visible_tools",),
            failure_source_ids=(),
        ),
        CapabilityDecisionEntry(
            entry_id="safety_gate.evaluate",
            file_path="backend/src/prismclaw/core/agent/safety_gate.py",
            symbol="SafetyGate.evaluate",
            summary=(
                "SafetyGate is the sole gate replacing the old approval pipeline. "
                "Evaluates tool calls through RuleGate (L0) → RiskRouter (L1) → "
                "WorkspaceMode check → ConfirmGateway orchestration."
            ),
            decision_kinds=(
                CapabilityDecisionKind.PERMISSION,
                CapabilityDecisionKind.APPROVAL,
                CapabilityDecisionKind.DENY,
                CapabilityDecisionKind.SANDBOX,
            ),
            inputs=(
                "per_turn_tier",
                "tool.server_config.forced_chat_only",
                "sandbox_backend",
            ),
            outputs=("GateDecision.allowed", "GateDecision.sandbox_tier", "GateDecision.confirmation"),
            failure_source_ids=(
                "per-call-tier-rerderived",
                "forced-tier-server-sidecar",
            ),
        ),
        CapabilityDecisionEntry(
            entry_id="safety_gate.apply_no_hook_denials",
            file_path="backend/src/prismclaw/core/agent/safety_gate.py",
            symbol="SafetyGate._apply_denials",
            summary=(
                "Finalizes deny outcomes when gate evaluation determines a tool call "
                "should be denied based on policy rules."
            ),
            decision_kinds=(
                CapabilityDecisionKind.APPROVAL,
                CapabilityDecisionKind.DENY,
            ),
            inputs=(
                "gate_decision.allowed",
                "tool_ctx",
                "channel",
                "session_id",
            ),
            outputs=("denied_call_ids",),
            failure_source_ids=("approval-outcome-finalized-after-phase1",),
        ),
        CapabilityDecisionEntry(
            entry_id="safety_gate.confirm_gateway",
            file_path="backend/src/prismclaw/core/agent/safety_gate.py",
            symbol="SafetyGate._confirm_gateway",
            summary=(
                "Orchestrates user confirmation for tool calls that require explicit "
                "approval via the ConfirmGateway pattern."
            ),
            decision_kinds=(
                CapabilityDecisionKind.APPROVAL,
                CapabilityDecisionKind.DENY,
            ),
            inputs=(
                "gate_decision.confirmation",
                "confirmation_callback",
                "session_id",
            ),
            outputs=(
                "confirmed_call_ids",
                "denied_call_ids",
            ),
            failure_source_ids=("approval-outcome-finalized-after-phase1",),
        ),
        CapabilityDecisionEntry(
            entry_id="tool_execution.execute_with_handoff",
            file_path="backend/src/prismclaw/core/agent/tool_loop/execution.py",
            symbol="execute_tool_calls_with_handoff",
            summary=(
                "Executes the already-admitted tool calls and merges synthetic cap-exceeded "
                "results back into the tool result stream."
            ),
            decision_kinds=(
                CapabilityDecisionKind.TOOL_CALLABLE,
                CapabilityDecisionKind.DENY,
                CapabilityDecisionKind.FORK,
            ),
            inputs=(
                "tool_calls",
                "cap_exceeded_call_ids",
            ),
            outputs=("synthetic_results", "effective_tool_calls", "all_results"),
            failure_source_ids=("handoff-dispatch-transport",),
        ),
        CapabilityDecisionEntry(
            entry_id="tool_execution.postprocess_results",
            file_path="backend/src/prismclaw/core/agent/tool_loop/execution.py",
            symbol="postprocess_tool_results",
            summary=(
                "Applies post-execution result shaping including fatigue advisory, "
                "result compaction, and replacement-state freezing."
            ),
            decision_kinds=(
                CapabilityDecisionKind.DENY,
                CapabilityDecisionKind.FORK,
            ),
            inputs=(
                "execution_artifacts.tool_calls",
                "execution_artifacts.results",
                "tool_ctx.turn_state",
            ),
            outputs=(
                "execution_artifacts.results",
                "execution_artifacts.compaction_turn",
                "turn_state.replacement_state",
            ),
            failure_source_ids=("handoff-dispatch-transport",),
        ),
        CapabilityDecisionEntry(
            entry_id="handoff_supervisor.resolve_dispatch_extras",
            file_path="backend/src/prismclaw/core/agent/handoff/supervisor.py",
            symbol="HandoffSupervisor.resolve_dispatch_extras",
            summary=(
                "Resolves pending handoff chain metadata, merges compatible chains, "
                "and decides whether a sub-agent handoff exceeds the hop cap."
            ),
            decision_kinds=(
                CapabilityDecisionKind.FORK,
                CapabilityDecisionKind.DENY,
            ),
            inputs=(
                "pending_handoff_store entries",
                "sub_agent_calls",
                "max_chain_length",
            ),
            outputs=("HandoffDispatchExtras", "CapExceededInfo"),
            failure_source_ids=("handoff-dispatch-transport",),
        ),
        CapabilityDecisionEntry(
            entry_id="tool_execution.plan_handoff_dispatch",
            file_path="backend/src/prismclaw/core/agent/tool_loop/execution.py",
            symbol="plan_handoff_dispatch",
            summary=(
                "Derives fork dispatch extras and sub-agent handoff cap decisions via "
                "the handoff supervisor."
            ),
            decision_kinds=(CapabilityDecisionKind.FORK,),
            inputs=(
                "handoff_supervisor.resolve_dispatch_extras",
                "dispatch_extras.source_is_fork",
                "dispatch_extras.source_skill_origin",
            ),
            outputs=(
                "resolved_dispatch_extras_by_call_id",
                "cap_exceeded_call_ids",
                "cap_exceeded_info_by_call",
            ),
            failure_source_ids=(
                "handoff-dispatch-transport",
                "fork-inheritance-not-bundled",
            ),
        ),
        CapabilityDecisionEntry(
            entry_id="tool_execution.apply_handoff_dispatch_plan",
            file_path="backend/src/prismclaw/core/agent/tool_loop/execution.py",
            symbol="apply_handoff_dispatch_plan",
            summary=(
                "Projects handoff dispatch decisions into transport extras and emits "
                "cap-exceeded synthetic progress notifications."
            ),
            decision_kinds=(
                CapabilityDecisionKind.FORK,
                CapabilityDecisionKind.DENY,
            ),
            inputs=(
                "resolved_dispatch_extras_by_call_id",
                "cap_exceeded_info_by_call",
                "subagent_progress_sink",
            ),
            outputs=(
                "pending_handoff_dispatch_extras",
                "synthetic_cap_exceeded_progress",
            ),
            failure_source_ids=(
                "handoff-dispatch-transport",
                "fork-inheritance-not-bundled",
            ),
        ),
        CapabilityDecisionEntry(
            entry_id="skills.runtime.execute_skill_execution",
            file_path="backend/src/prismclaw/core/agent/skills/runtime.py",
            symbol="SkillRuntime.execute_skill_execution",
            summary=(
                "Converts a skill execution plan into structured runtime modifiers "
                "such as allowed_tools_override."
            ),
            decision_kinds=(
                CapabilityDecisionKind.SKILL,
                CapabilityDecisionKind.TOOL_GOVERNANCE,
            ),
            inputs=("plan.allowed_tools", "plan.execution_mode"),
            outputs=("SkillInvocationState.allowed_tools_override",),
            failure_source_ids=("skill-runtime-tool-constraint-projection",),
        ),
        CapabilityDecisionEntry(
            entry_id="skills.agent_adapter.build_manifest",
            file_path="backend/src/prismclaw/core/agent/skills/agent_adapter.py",
            symbol="build_agent_manifest_from_skill",
            summary=(
                "Projects skill agent config into a sub-agent manifest, including "
                "handoff permissions and fork mode."
            ),
            decision_kinds=(
                CapabilityDecisionKind.SKILL,
                CapabilityDecisionKind.MEMORY,
                CapabilityDecisionKind.FORK,
            ),
            inputs=(
                "agent.allowed_tools",
                "agent.can_handoff_to",
                "skill.execution_mode",
            ),
            outputs=("AgentManifest",),
            failure_source_ids=(
                "memory-policy-embedded-in-skill-manifest",
                "fork-mode-embedded-in-skill-manifest",
            ),
        ),
        CapabilityDecisionEntry(
            entry_id="subagent.executor.delegate_run",
            file_path="backend/src/prismclaw/core/agent/subagent/executor.py",
            symbol="SubAgentExecutor.run",
            summary=(
                "Production sub-agent entrypoint. Instantiates AgentInstance, "
                "registers optional runtime-persistence callbacks, and delegates "
                "execution to the canonical agent_instance implementation."
            ),
            decision_kinds=(
                CapabilityDecisionKind.FORK,
                CapabilityDecisionKind.TOOL_CALLABLE,
            ),
            inputs=(
                "manifest.execution_mode",
                "context_snapshot",
            ),
            outputs=("AgentRunResult",),
            failure_source_ids=(),
        ),
        CapabilityDecisionEntry(
            entry_id="subagent.executor.policy_inheritance",
            file_path="backend/src/prismclaw/core/agent/subagent/agent_instance.py",
            symbol="AgentInstance._build_sub_context",
            summary=(
                "Builds sub-agent ToolContext from the parent turn_state and "
                "capability_decision_bundle. Fork inheritance is bundle-owned: "
                "child callable scope is intersected with parent bundle scope, "
                "sandbox and permission are inherited from parent bundle, "
                "and memory visibility cannot widen beyond parent."
            ),
            decision_kinds=(
                CapabilityDecisionKind.FORK,
                CapabilityDecisionKind.SANDBOX,
                CapabilityDecisionKind.PERMISSION,
                CapabilityDecisionKind.TOOL_GOVERNANCE,
            ),
            inputs=(
                "parent_ctx.turn_state",
                "parent_turn_state.capability_decision_bundle",
                "build_isolated_turn_state",
                "manifest.is_fork",
                "manifest.allowed_tools",
            ),
            outputs=("sub_ctx", "child_capability_decision_bundle"),
            failure_source_ids=(),
        ),
        CapabilityDecisionEntry(
            entry_id="subagent.executor.resolve_preloaded_skill_context",
            file_path="backend/src/prismclaw/core/agent/subagent/agent_instance.py",
            symbol="AgentInstance._resolve_preloaded_skill_context",
            summary=(
                "Resolves preload skill manifests and derives preload_ctx.allowed_tools_limit "
                "from SkillManifest.allowed_tools before sub-agent callable scope is intersected."
            ),
            decision_kinds=(
                CapabilityDecisionKind.SKILL,
                CapabilityDecisionKind.TOOL_GOVERNANCE,
                CapabilityDecisionKind.TOOL_CALLABLE,
                CapabilityDecisionKind.FORK,
            ),
            inputs=(
                "manifest.declared_invoke_sequence",
                "skill_runtime.resolve_skill",
                "skill.allowed_tools",
            ),
            outputs=("PreloadedSkillContext.allowed_tools_limit",),
            failure_source_ids=("preloaded-skill-tool-limits-derived-in-executor",),
        ),
        CapabilityDecisionEntry(
            entry_id="subagent.executor.resolve_allowed_tools",
            file_path="backend/src/prismclaw/core/agent/subagent/agent_instance.py",
            symbol="AgentInstance._resolve_allowed_tools",
            summary=(
                "Intersects manifest allowed_tools with preload skill limits and "
                "parent bundle callable scope to derive the callable tool set for "
                "a sub-agent. The resulting scope is always intersected with "
                "parent bundle and restricted=True in the child bundle."
            ),
            decision_kinds=(
                CapabilityDecisionKind.TOOL_GOVERNANCE,
                CapabilityDecisionKind.TOOL_CALLABLE,
                CapabilityDecisionKind.SKILL,
                CapabilityDecisionKind.FORK,
            ),
            inputs=(
                "manifest.allowed_tools",
                "preload_ctx.allowed_tools_limit",
                "parent_bundle.tool_governance.callable_name_set",
            ),
            outputs=("allowed_tools",),
            failure_source_ids=(),
        ),
        CapabilityDecisionEntry(
            entry_id="subagent.executor.build_tool_schemas",
            file_path="backend/src/prismclaw/core/agent/subagent/agent_instance.py",
            symbol="AgentInstance._build_tool_schemas",
            summary=(
                "Builds the sub-agent tool schema list by consuming the governance "
                "runtime entrypoint and the derived allowed_tools scope, without local "
                "blocklists."
            ),
            decision_kinds=(
                CapabilityDecisionKind.TOOL_GOVERNANCE,
                CapabilityDecisionKind.TOOL_CALLABLE,
                CapabilityDecisionKind.FORK,
                CapabilityDecisionKind.MEMORY,
            ),
            inputs=(
                "allowed_tools",
                "resolve_registry_tool_governance",
            ),
            outputs=("tool_schemas",),
            failure_source_ids=(),
        ),
        CapabilityDecisionEntry(
            entry_id="subagent.executor.dispatch_tool_calls",
            file_path="backend/src/prismclaw/core/agent/subagent/agent_instance.py",
            symbol="AgentInstance._dispatch_tool_calls",
            summary=(
                "Hard-denies tool calls outside the governed callable scope, then dispatches "
                "surviving tool calls directly (SafetyGate handles gating at the parent runner level)."
            ),
            decision_kinds=(
                CapabilityDecisionKind.TOOL_CALLABLE,
                CapabilityDecisionKind.DENY,
                CapabilityDecisionKind.FORK,
                CapabilityDecisionKind.MEMORY,
            ),
            inputs=(
                "allowed_tools",
                "tool_calls",
                "resolve_registry_tool_governance",
                "parent_ctx.mode",
            ),
            outputs=(
                "tool_results",
            ),
            failure_source_ids=(),
        ),
        CapabilityDecisionEntry(
            entry_id="subagent.executor.memory_admission",
            file_path="backend/src/prismclaw/core/agent/subagent/agent_instance.py",
            symbol="AgentInstance._collect_memory_injection",
            summary=(
                "Admits memory search results into the sub-agent system prompt "
                "when parent context is inherited."
            ),
            decision_kinds=(
                CapabilityDecisionKind.MEMORY,
                CapabilityDecisionKind.FORK,
            ),
            inputs=("memory_store.search", "task"),
            outputs=("memory_block",),
            failure_source_ids=(
                "memory-policy-embedded-in-skill-manifest",
                "memory-admission-at-executor",
            ),
        ),
    )

    failure_sources = (
        CapabilityFailureSource(
            source_id="permission-tier-derived-in-pipeline",
            severity=FailureSeverity.HIGH,
            summary=(
                "Per-turn permission tier is still decided in PreRequestPipeline "
                "instead of a single capability resolver."
            ),
            impacted_entry_ids=("pre_request_pipeline.capability_surface",),
            impacted_kinds=(CapabilityDecisionKind.PERMISSION,),
        ),
        CapabilityFailureSource(
            source_id="skill-runtime-tool-constraint-projection",
            severity=FailureSeverity.HIGH,
            summary=(
                "Skill tool limits still flow through SkillInvocationState "
                "projection instead of a standalone capability decision object."
            ),
            impacted_entry_ids=(
                "pre_request_pipeline.capability_surface",
                "skills.runtime.execute_skill_execution",
            ),
            impacted_kinds=(
                CapabilityDecisionKind.SKILL,
                CapabilityDecisionKind.TOOL_GOVERNANCE,
            ),
        ),
        CapabilityFailureSource(
            source_id="runtime-projection-synchronization",
            severity=FailureSeverity.HIGH,
            summary=(
                "Preservation runtime mirrors structured skill state into request "
                "and ToolContext transport fields, so capability state still "
                "depends on explicit projection synchronization."
            ),
            impacted_entry_ids=("preservation_runtime.project_active_skill_invocation",),
            impacted_kinds=(
                CapabilityDecisionKind.SKILL,
                CapabilityDecisionKind.TOOL_GOVERNANCE,
            ),
        ),
        CapabilityFailureSource(
            source_id="per-call-tier-rerderived",
            severity=FailureSeverity.HIGH,
            summary=(
                "Per-turn permission tier is still recomputed per call inside "
                "SafetyGate instead of a single capability resolver."
            ),
            impacted_entry_ids=("safety_gate.evaluate",),
            impacted_kinds=(
                CapabilityDecisionKind.PERMISSION,
                CapabilityDecisionKind.APPROVAL,
            ),
        ),
        CapabilityFailureSource(
            source_id="forced-tier-server-sidecar",
            severity=FailureSeverity.MEDIUM,
            summary=(
                "MCP forced_chat_only flags live in server_config sidecars and are merged "
                "at SafetyGate evaluation time instead of in a first-class capability model."
            ),
            impacted_entry_ids=("safety_gate.evaluate",),
            impacted_kinds=(
                CapabilityDecisionKind.PERMISSION,
                CapabilityDecisionKind.APPROVAL,
            ),
        ),
        CapabilityFailureSource(
            source_id="approval-outcome-finalized-after-phase1",
            severity=FailureSeverity.HIGH,
            summary=(
                "Approval/deny decisions are finalized in follow-up helpers after "
                "SafetyGate.evaluate, so the authority to approve or deny is split across "
                "multiple functions."
            ),
            impacted_entry_ids=(
                "safety_gate.evaluate",
                "safety_gate.apply_no_hook_denials",
                "safety_gate.confirm_gateway",
            ),
            impacted_kinds=(
                CapabilityDecisionKind.APPROVAL,
                CapabilityDecisionKind.DENY,
            ),
        ),
        CapabilityFailureSource(
            source_id="handoff-dispatch-transport",
            severity=FailureSeverity.HIGH,
            summary=(
                "Fork handoff caps and dispatch extras still move through transport "
                "fields instead of a formal fork decision object."
            ),
            impacted_entry_ids=(
                "handoff_supervisor.resolve_dispatch_extras",
                "tool_execution.execute_with_handoff",
                "tool_execution.postprocess_results",
                "tool_execution.plan_handoff_dispatch",
                "tool_execution.apply_handoff_dispatch_plan",
            ),
            impacted_kinds=(
                CapabilityDecisionKind.FORK,
                CapabilityDecisionKind.DENY,
            ),
        ),
        CapabilityFailureSource(
            source_id="fork-inheritance-not-bundled",
            severity=FailureSeverity.MEDIUM,
            summary=(
                "Fork inheritance was historically spread across handoff preparation "
                "and SubAgentExecutor context construction. After Track E, fork "
                "inheritance is bundle-owned: child capability_decision_bundle is "
                "derived from parent bundle with restricted=True and parent scope "
                "intersection. Residual risk remains if prepare_handoff path bypasses "
                "the bundle."
            ),
            impacted_entry_ids=(
                "tool_execution.plan_handoff_dispatch",
                "tool_execution.apply_handoff_dispatch_plan",
            ),
            impacted_kinds=(CapabilityDecisionKind.FORK,),
        ),
        CapabilityFailureSource(
            source_id="preloaded-skill-tool-limits-derived-in-executor",
            severity=FailureSeverity.MEDIUM,
            summary=(
                "Preloaded skill allowed_tools limits are derived inside the sub-agent "
                "executor from resolved SkillManifest payloads. After Track E, the "
                "resulting callable scope is intersected with parent bundle scope "
                "before constructing the child bundle. Residual risk remains if "
                "skill resolution bypasses the bundle."
            ),
            impacted_entry_ids=("subagent.executor.resolve_preloaded_skill_context",),
            impacted_kinds=(
                CapabilityDecisionKind.SKILL,
                CapabilityDecisionKind.TOOL_GOVERNANCE,
                CapabilityDecisionKind.TOOL_CALLABLE,
                CapabilityDecisionKind.FORK,
            ),
        ),
        CapabilityFailureSource(
            source_id="subagent-allowed-tools-derived-in-executor",
            severity=FailureSeverity.MEDIUM,
            summary=(
                "Sub-agent allowed_tools are derived inside the executor by "
                "intersecting manifest, preload, and parent bundle scope. After "
                "Track E, the child bundle is always restricted=True and declared "
                "tools reflect the intersection. Residual risk remains if a new "
                "code path bypasses the intersection logic."
            ),
            impacted_entry_ids=("subagent.executor.resolve_allowed_tools",),
            impacted_kinds=(
                CapabilityDecisionKind.TOOL_GOVERNANCE,
                CapabilityDecisionKind.TOOL_CALLABLE,
                CapabilityDecisionKind.SKILL,
                CapabilityDecisionKind.FORK,
            ),
        ),
        CapabilityFailureSource(
            source_id="memory-policy-embedded-in-skill-manifest",
            severity=FailureSeverity.HIGH,
            summary=(
                "Skill manifests embed memory admissibility toggles before memory "
                "admissibility is formalized."
            ),
            impacted_entry_ids=(
                "skills.agent_adapter.build_manifest",
                "subagent.executor.memory_admission",
            ),
            impacted_kinds=(
                CapabilityDecisionKind.SKILL,
                CapabilityDecisionKind.MEMORY,
            ),
        ),
        CapabilityFailureSource(
            source_id="fork-mode-embedded-in-skill-manifest",
            severity=FailureSeverity.MEDIUM,
            summary=(
                "Whether a skill runs as a fork is still encoded in AgentManifest "
                "instead of a dedicated fork policy."
            ),
            impacted_entry_ids=("skills.agent_adapter.build_manifest",),
            impacted_kinds=(
                CapabilityDecisionKind.SKILL,
                CapabilityDecisionKind.FORK,
            ),
        ),
        CapabilityFailureSource(
            source_id="memory-admission-at-executor",
            severity=FailureSeverity.HIGH,
            summary=(
                "Memory search admissibility is still decided inside the sub-agent "
                "executor, so memory remains close to prompt assembly."
            ),
            impacted_entry_ids=("subagent.executor.memory_admission",),
            impacted_kinds=(CapabilityDecisionKind.MEMORY,),
        ),
    )
    return CapabilityDecisionMap(
        schema_version="capability_decision_map.v1",
        entries=entries,
        failure_sources=failure_sources,
    )
