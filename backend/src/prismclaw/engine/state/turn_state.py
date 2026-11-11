import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Literal, Set
from pydantic import BaseModel, Field, model_validator


class ProviderCapabilityProfile(BaseModel):
    schema_version: str = Field(default="provider_capability.v1")
    provider_name: str | None = None
    llm_provider: str | None = None
    supports_structured_context_extension: bool = False
    supports_tool_search: bool = False
    supports_deferred_tools: bool = False
    header_placement_policy: str = Field(default="inline")


class ToolGovernanceState(BaseModel):
    declared_tools: list[str] = Field(default_factory=list)
    visible_tools: list[str] = Field(default_factory=list)
    callable_tools: list[str] = Field(default_factory=list)
    gated_tools: list[str] = Field(default_factory=list)
    decision_digest: str = Field(default="")
    discovered_tool_names: list[str] = Field(default_factory=list)
    active_skill_name: str | None = None
    restricted: bool = False

    def model_post_init(self, __context: Any) -> None:
        callable_names = _sorted_unique_strings(self.callable_tools)
        self.callable_tools = callable_names

    @classmethod
    def from_contract(
        cls,
        *,
        declared_tools: list[str] | set[str] | tuple[str, ...] = (),
        visible_tools: list[str] | set[str] | tuple[str, ...] = (),
        callable_tools: list[str] | set[str] | tuple[str, ...] = (),
        gated_tools: list[str] | set[str] | tuple[str, ...] = (),
        discovered_tool_names: list[str] | set[str] | tuple[str, ...] = (),
        active_skill_name: str | None = None,
        restricted: bool = False,
    ) -> "ToolGovernanceState":
        declared = _sorted_unique_strings(declared_tools)
        visible = _sorted_unique_strings(visible_tools)
        callable_names = _sorted_unique_strings(callable_tools)
        gated = _sorted_unique_strings(gated_tools)
        discovered = _sorted_unique_strings(discovered_tool_names)
        return cls(
            declared_tools=declared,
            visible_tools=visible,
            callable_tools=callable_names,
            gated_tools=gated,
            decision_digest=_build_tool_governance_state_digest(
                declared_tools=declared,
                visible_tools=visible,
                callable_tools=callable_names,
                gated_tools=gated,
                discovered_tool_names=discovered,
                active_skill_name=active_skill_name,
                restricted=restricted,
            ),
            discovered_tool_names=discovered,
            active_skill_name=active_skill_name,
            restricted=restricted,
        )

    def callable_name_set(self) -> set[str]:
        return set(self.callable_tools)


class PermissionDecision(BaseModel):
    mode: str = Field(default="work")
    source: str = Field(default="default")


class SandboxPolicyDecision(BaseModel):
    backend: str = Field(default="")
    policy_kind: str = Field(default="")
    source: str = Field(default="default")

    @classmethod
    def from_policy(
        cls,
        policy: Any | None,
        *,
        source: str = "default",
    ) -> "SandboxPolicyDecision":
        if policy is None:
            return cls(source=source)
        return cls(
            backend=str(getattr(policy, "backend", "") or ""),
            policy_kind=policy.__class__.__name__,
            source=source,
        )


class MemoryHintPolicy(BaseModel):
    admitted_hints: list[str] = Field(default_factory=list)
    source: str = Field(default="request_assembly_manifest")
    digest: str = Field(default="")

    @classmethod
    def from_manifest(cls, manifest: "RequestAssemblyManifest") -> "MemoryHintPolicy":
        # When fork policy requires omit_parent_context, memory hints must
        # not be inherited from the parent regardless of what the manifest
        # records in memory_hints_used.  This enforces the memory hint
        # verification contract: unverified (parent) hints must not enter
        # the child's stable prefix.
        if manifest.fork_memory_visibility == "omit_parent_context":
            source = "fork_policy:omit_parent_context"
            return cls(
                admitted_hints=[],
                source=source,
                digest=_build_policy_digest(
                    {
                        "admitted_hints": [],
                        "source": source,
                    }
                ),
            )
        admitted_hints = _sorted_unique_strings(manifest.memory_hints_used)
        # Preserve the original source from the bundle for replay stability.
        source = manifest.memory_hint_policy_source or "request_assembly_manifest"
        if not admitted_hints and _manifest_encodes_fork_contract(manifest):
            source = (
                "fork_policy:omit_parent_context"
                if manifest.fork_memory_visibility == "omit_parent_context"
                else "fork_policy:inherit_parent_context"
            )
        return cls(
            admitted_hints=admitted_hints,
            source=source,
            digest=_build_policy_digest(
                {
                    "admitted_hints": admitted_hints,
                    "source": source,
                }
            ),
        )


class ForkPolicyDecision(BaseModel):
    inheritance_source: str | None = None
    resume_rebuild_policy: str = Field(default="live_turn")
    clone_policy: str = Field(default="isolated_child_turn_state")
    capability_narrowing: str = Field(default="inherit_parent_bundle_and_narrow")
    memory_visibility: str = Field(default="inherit_parent_context")
    replacement_inheritance_mode: str = Field(default="inherit_frozen")
    digest: str = Field(default="")

    @classmethod
    def from_manifest(cls, manifest: "RequestAssemblyManifest") -> "ForkPolicyDecision":
        payload = {
            "inheritance_source": manifest.fork_inheritance_source,
            "resume_rebuild_policy": manifest.resume_rebuild_policy,
            "clone_policy": manifest.fork_clone_policy,
            "capability_narrowing": manifest.fork_capability_narrowing,
            "memory_visibility": manifest.fork_memory_visibility,
            "replacement_inheritance_mode": manifest.replacement_inheritance_mode,
        }
        return cls(
            inheritance_source=manifest.fork_inheritance_source,
            resume_rebuild_policy=manifest.resume_rebuild_policy,
            clone_policy=manifest.fork_clone_policy,
            capability_narrowing=manifest.fork_capability_narrowing,
            memory_visibility=manifest.fork_memory_visibility,
            replacement_inheritance_mode=manifest.replacement_inheritance_mode,
            digest=_build_policy_digest(payload),
        )


class FastPathEligibility(BaseModel):
    schema_version: str = Field(default="fast_path_eligibility.v1")
    eligible: bool = False
    decision: str = Field(default="ineligible")
    eligible_stages: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    explanation: str = Field(default="")
    baseline_valid: bool = False
    recovery_clean: bool = True
    request_mode_unchanged: bool = False
    static_inputs_unchanged: bool = False
    permission_unchanged: bool = False
    tool_pool_unchanged: bool = False
    provider_capability_unchanged: bool = False
    memory_admissibility_unchanged: bool = False
    fork_inheritance_unchanged: bool = False


def build_fork_memory_hint_policy(
    fork_policy: ForkPolicyDecision,
) -> MemoryHintPolicy:
    source = (
        "fork_policy:omit_parent_context"
        if fork_policy.memory_visibility == "omit_parent_context"
        else "fork_policy:inherit_parent_context"
    )
    return MemoryHintPolicy(
        admitted_hints=[],
        source=source,
        digest=_build_policy_digest(
            {
                "admitted_hints": [],
                "source": source,
            }
        ),
    )


def _manifest_encodes_fork_contract(manifest: "RequestAssemblyManifest") -> bool:
    return bool(
        manifest.fork_inheritance_source
        or manifest.fork_clone_policy != "isolated_child_turn_state"
        or manifest.fork_capability_narrowing != "inherit_parent_bundle_and_narrow"
        or manifest.fork_memory_visibility != "inherit_parent_context"
        or manifest.replacement_inheritance_mode != "inherit_frozen"
    )


def build_request_assembly_manifest_from_bundle(
    bundle: "CapabilityDecisionBundle",
) -> "RequestAssemblyManifest":
    return RequestAssemblyManifest(
        memory_hints_used=list(bundle.memory_hint_policy.admitted_hints),
        memory_hint_policy_source=bundle.memory_hint_policy.source,
        fork_inheritance_source=bundle.fork_policy.inheritance_source,
        resume_rebuild_policy=bundle.fork_policy.resume_rebuild_policy,
        fork_clone_policy=bundle.fork_policy.clone_policy,
        fork_capability_narrowing=bundle.fork_policy.capability_narrowing,
        fork_memory_visibility=bundle.fork_policy.memory_visibility,
        replacement_inheritance_mode=bundle.fork_policy.replacement_inheritance_mode,
    )


def build_canonical_decision_state_from_bundle(
    bundle: "CapabilityDecisionBundle",
) -> "CanonicalDecisionState":
    return CanonicalDecisionState(
        provider_capability_profile=bundle.provider_capability_profile.model_copy(deep=True),
        tool_governance_state=bundle.tool_governance.model_copy(deep=True),
        permission_decision=bundle.permission.model_copy(deep=True),
        sandbox_policy_decision=bundle.sandbox_policy.model_copy(deep=True),
    )


def synchronize_turn_state_capability_contract(
    turn_state: "TurnState",
    bundle: "CapabilityDecisionBundle",
) -> "TurnState":
    turn_state.capability_decision_bundle = bundle.model_copy(deep=True)
    turn_state.canonical_decision_state = build_canonical_decision_state_from_bundle(bundle)
    turn_state.request_assembly_manifest = build_request_assembly_manifest_from_bundle(bundle)
    return turn_state


class TailEventRange(BaseModel):
    first_entry_id: str | None = None
    last_entry_id: str | None = None
    event_count: int = 0


class AppliedCompactionRecord(BaseModel):
    kind: str
    block_id: str = Field(default="")
    artifact_id: str | None = None
    source_id: str | None = None
    reference_id: str | None = None
    strategies: list[str] = Field(default_factory=list)


class RequestAssemblyManifest(BaseModel):
    schema_version: str = Field(default="request_assembly_manifest.v1")
    baseline_block_ids: list[str] = Field(default_factory=list)
    tail_event_range: TailEventRange = Field(default_factory=TailEventRange)
    projection_revision: str = Field(default="")
    applied_compactions: list[AppliedCompactionRecord] = Field(default_factory=list)
    memory_hints_used: list[str] = Field(default_factory=list)
    memory_hint_policy_source: str = Field(default="request_assembly_manifest")
    fork_inheritance_source: str | None = None
    resume_rebuild_policy: str = Field(default="live_turn")
    fork_clone_policy: str = Field(default="isolated_child_turn_state")
    fork_capability_narrowing: str = Field(default="inherit_parent_bundle_and_narrow")
    fork_memory_visibility: str = Field(default="inherit_parent_context")
    replacement_inheritance_mode: str = Field(default="inherit_frozen")


class CapabilityDecisionBundle(BaseModel):
    schema_version: str = Field(default="capability_decision_bundle.v2")
    provider_capability_profile: ProviderCapabilityProfile = Field(
        default_factory=ProviderCapabilityProfile
    )
    tool_governance: ToolGovernanceState = Field(default_factory=ToolGovernanceState)
    permission: PermissionDecision = Field(default_factory=PermissionDecision)
    sandbox_policy: SandboxPolicyDecision = Field(default_factory=SandboxPolicyDecision)
    memory_hint_policy: MemoryHintPolicy = Field(default_factory=MemoryHintPolicy)
    fork_policy: ForkPolicyDecision = Field(default_factory=ForkPolicyDecision)
    decision_digest: str = Field(default="")

    @classmethod
    def from_contract(
        cls,
        *,
        provider_capability_profile: ProviderCapabilityProfile,
        tool_governance: ToolGovernanceState,
        permission: PermissionDecision,
        sandbox_policy: SandboxPolicyDecision,
        memory_hint_policy: MemoryHintPolicy,
        fork_policy: ForkPolicyDecision,
    ) -> "CapabilityDecisionBundle":
        normalized_memory_hint_policy = _normalize_memory_hint_policy(memory_hint_policy)
        normalized_fork_policy = _normalize_fork_policy(fork_policy)
        bundle = cls(
            provider_capability_profile=provider_capability_profile.model_copy(deep=True),
            tool_governance=tool_governance.model_copy(deep=True),
            permission=permission.model_copy(deep=True),
            sandbox_policy=sandbox_policy.model_copy(deep=True),
            memory_hint_policy=normalized_memory_hint_policy,
            fork_policy=normalized_fork_policy,
        )
        bundle.decision_digest = _build_policy_digest(
            {
                "provider_capability_profile": bundle.provider_capability_profile.model_dump(
                    mode="json"
                ),
                "tool_governance": bundle.tool_governance.model_dump(mode="json"),
                "permission": bundle.permission.model_dump(mode="json"),
                "sandbox_policy": bundle.sandbox_policy.model_dump(mode="json"),
                "memory_hint_policy": bundle.memory_hint_policy.model_dump(mode="json"),
                "fork_policy": bundle.fork_policy.model_dump(mode="json"),
            }
        )
        return bundle


class CanonicalDecisionState(BaseModel):
    schema_version: str = Field(default="canonical_decision.v2")
    cache_baseline_id: str = Field(default="")
    tool_pool_id: str = Field(
        default="",
        description="Legacy field name; stores the canonical tool schema fingerprint.",
    )
    prepared_turn_digest: str = Field(default="")
    replacement_state_digest: str | None = None
    provider_capability_profile: ProviderCapabilityProfile = Field(
        default_factory=ProviderCapabilityProfile
    )
    tool_governance_state: ToolGovernanceState = Field(default_factory=ToolGovernanceState)
    permission_decision: PermissionDecision = Field(default_factory=PermissionDecision)
    sandbox_policy_decision: SandboxPolicyDecision = Field(default_factory=SandboxPolicyDecision)
    policy_latches: Dict[str, Any] = Field(default_factory=dict)


class ReplacementState(BaseModel):
    schema_version: str = Field(default="replacement_state.v1")
    seen_ids: Set[str] = Field(default_factory=set)
    replacements: Dict[str, str] = Field(default_factory=dict)

    def has_entries(self) -> bool:
        return bool(self.seen_ids or self.replacements)

    def mark_seen(self, source_id: str) -> None:
        if source_id:
            self.seen_ids.add(source_id)

    def freeze_replacement(self, source_id: str, replacement_text: str) -> None:
        if not source_id or not replacement_text:
            return
        self.seen_ids.add(source_id)
        self.replacements[source_id] = replacement_text

    def get_frozen_replacement(self, source_id: str) -> str | None:
        return self.replacements.get(source_id)

    def retain_only(self, active_ids: Set[str]) -> list[str]:
        removed = sorted(
            {
                *[source_id for source_id in self.seen_ids if source_id not in active_ids],
                *[source_id for source_id in self.replacements if source_id not in active_ids],
            }
        )
        if not removed:
            return []
        self.seen_ids.intersection_update(active_ids)
        for source_id in removed:
            self.replacements.pop(source_id, None)
        return removed


ReplacementInheritanceMode = Literal["inherit_frozen", "reset"]


@dataclass(frozen=True)
class ReplacementForkInheritancePolicy:
    mode: ReplacementInheritanceMode = "inherit_frozen"
    source: str = "parent_turn_state"


class ToolDiscoveryRecord(BaseModel):
    schema_version: str = Field(default="tool_discovery_record.v1")
    canonical_name: str = Field(default="")
    query: str = Field(default="")
    discovered_via: str = Field(default="tool_search")
    source_tool_name: str = Field(default="tool_search")
    tool_call_id: str | None = None
    discovery_order: int = 0


class ToolDiscoveryLedger(BaseModel):
    schema_version: str = Field(default="tool_discovery_ledger.v1")
    records: list[ToolDiscoveryRecord] = Field(default_factory=list)

    def model_post_init(self, __context: Any) -> None:
        next_order = 1
        deduped: list[ToolDiscoveryRecord] = []
        seen: set[str] = set()
        for record in self.records:
            if not isinstance(record.canonical_name, str):
                continue
            canonical_name = record.canonical_name.strip()
            if not canonical_name or canonical_name in seen:
                continue
            seen.add(canonical_name)
            deduped.append(
                ToolDiscoveryRecord(
                    canonical_name=canonical_name,
                    query=record.query.strip() if isinstance(record.query, str) else "",
                    discovered_via=(
                        record.discovered_via.strip()
                        if isinstance(record.discovered_via, str) and record.discovered_via.strip()
                        else "tool_search"
                    ),
                    source_tool_name=(
                        record.source_tool_name.strip()
                        if isinstance(record.source_tool_name, str)
                        and record.source_tool_name.strip()
                        else "tool_search"
                    ),
                    tool_call_id=record.tool_call_id
                    if isinstance(record.tool_call_id, str)
                    else None,
                    discovery_order=record.discovery_order or next_order,
                )
            )
            next_order += 1
        self.records = deduped

    def canonical_name_set(self) -> set[str]:
        return {record.canonical_name for record in self.records}

    def add_records(self, records: list["ToolDiscoveryRecord | dict[str, Any]"]) -> None:
        existing = self.canonical_name_set()
        next_order = max((record.discovery_order for record in self.records), default=0) + 1
        for raw_record in records:
            record = (
                raw_record
                if isinstance(raw_record, ToolDiscoveryRecord)
                else ToolDiscoveryRecord.model_validate(raw_record)
            )
            canonical_name = record.canonical_name.strip()
            if not canonical_name or canonical_name in existing:
                continue
            existing.add(canonical_name)
            self.records.append(
                ToolDiscoveryRecord(
                    canonical_name=canonical_name,
                    query=record.query.strip(),
                    discovered_via=record.discovered_via.strip() or "tool_search",
                    source_tool_name=record.source_tool_name.strip() or "tool_search",
                    tool_call_id=record.tool_call_id,
                    discovery_order=record.discovery_order or next_order,
                )
            )
            next_order += 1


class TurnState(BaseModel):
    """
    Canonical turn state for PrismClaw.
    Any state affecting request construction (discovered tools, replacement freezes,
    projection logs, policy latches) must be included here.
    """

    schema_version: str = Field(default="turn_state.v4")
    tool_pool_id: str = Field(
        default="",
        description="Legacy field name; stores the canonical tool schema fingerprint.",
    )
    discovered_tool_ledger: ToolDiscoveryLedger = Field(default_factory=ToolDiscoveryLedger)
    replacement_state: ReplacementState = Field(default_factory=ReplacementState)
    policy_latches: Dict[str, Any] = Field(default_factory=dict)
    canonical_decision_state: CanonicalDecisionState = Field(default_factory=CanonicalDecisionState)
    capability_decision_bundle: CapabilityDecisionBundle = Field(
        default_factory=CapabilityDecisionBundle
    )
    request_assembly_manifest: RequestAssemblyManifest = Field(
        default_factory=RequestAssemblyManifest
    )
    # projection_log could be added here in the future

    @model_validator(mode="before")
    @classmethod
    def _migrate_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        from prismclaw.engine.state.versioning import migrate_turn_state_payload

        migrated = migrate_turn_state_payload(value)
        return migrated if isinstance(migrated, dict) else value

    def discovered_tool_names(self) -> set[str]:
        return self.discovered_tool_ledger.canonical_name_set()


def _sorted_unique_strings(values: list[str] | set[str] | tuple[str, ...]) -> list[str]:
    return sorted({item for item in values if isinstance(item, str) and item})


def _build_policy_digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "utf-8"
        )
    ).hexdigest()


def _normalize_memory_hint_policy(policy: MemoryHintPolicy) -> MemoryHintPolicy:
    admitted_hints = _sorted_unique_strings(policy.admitted_hints)
    source = policy.source or "request_assembly_manifest"
    digest = policy.digest or _build_policy_digest(
        {
            "admitted_hints": admitted_hints,
            "source": source,
        }
    )
    return MemoryHintPolicy(
        admitted_hints=admitted_hints,
        source=source,
        digest=digest,
    )


def _normalize_fork_policy(policy: ForkPolicyDecision) -> ForkPolicyDecision:
    payload = {
        "inheritance_source": policy.inheritance_source,
        "resume_rebuild_policy": policy.resume_rebuild_policy,
        "clone_policy": policy.clone_policy,
        "capability_narrowing": policy.capability_narrowing,
        "memory_visibility": policy.memory_visibility,
        "replacement_inheritance_mode": policy.replacement_inheritance_mode,
    }
    return ForkPolicyDecision(
        inheritance_source=policy.inheritance_source,
        resume_rebuild_policy=policy.resume_rebuild_policy,
        clone_policy=policy.clone_policy,
        capability_narrowing=policy.capability_narrowing,
        memory_visibility=policy.memory_visibility,
        replacement_inheritance_mode=policy.replacement_inheritance_mode,
        digest=policy.digest or _build_policy_digest(payload),
    )


def _build_tool_governance_state_digest(
    *,
    declared_tools: list[str],
    visible_tools: list[str],
    callable_tools: list[str],
    gated_tools: list[str],
    discovered_tool_names: list[str],
    active_skill_name: str | None,
    restricted: bool,
) -> str:
    digest = hashlib.sha256()
    for label, values in (
        ("declared_tools", declared_tools),
        ("visible_tools", visible_tools),
        ("callable_tools", callable_tools),
        ("gated_tools", gated_tools),
        ("discovered_tool_names", discovered_tool_names),
    ):
        digest.update(label.encode("utf-8"))
        digest.update(b"\0")
        for value in values:
            digest.update(value.encode("utf-8"))
            digest.update(b"\0")
    digest.update((active_skill_name or "").encode("utf-8"))
    digest.update(b"\0")
    digest.update(b"1" if restricted else b"0")
    return digest.hexdigest()


def resolve_capability_decision_bundle(
    state: "TurnState | Dict[str, Any] | None",
) -> CapabilityDecisionBundle:
    if isinstance(state, TurnState):
        bundle = state.capability_decision_bundle
        if bundle.decision_digest:
            return bundle.model_copy(deep=True)
        return CapabilityDecisionBundle.from_contract(
            provider_capability_profile=state.canonical_decision_state.provider_capability_profile,
            tool_governance=state.canonical_decision_state.tool_governance_state,
            permission=state.canonical_decision_state.permission_decision,
            sandbox_policy=state.canonical_decision_state.sandbox_policy_decision,
            memory_hint_policy=MemoryHintPolicy.from_manifest(state.request_assembly_manifest),
            fork_policy=ForkPolicyDecision.from_manifest(state.request_assembly_manifest),
        )
    if isinstance(state, dict):
        try:
            restored = TurnState.model_validate(state)
        except Exception:
            return CapabilityDecisionBundle()
        return resolve_capability_decision_bundle(restored)
    return CapabilityDecisionBundle()


def restore_replacement_state(
    state: ReplacementState | Dict[str, Any] | None,
) -> ReplacementState:
    if isinstance(state, ReplacementState):
        return ReplacementState.model_validate(state.model_dump(mode="json"))
    if isinstance(state, dict):
        if not any(key in state for key in ("schema_version", "seen_ids", "replacements")):
            legacy_replacements = {
                source_id: replacement_text
                for source_id, replacement_text in state.items()
                if isinstance(source_id, str)
                and source_id
                and isinstance(replacement_text, str)
                and replacement_text
            }
            if legacy_replacements:
                return ReplacementState(
                    seen_ids=set(legacy_replacements),
                    replacements=legacy_replacements,
                )
        return ReplacementState.model_validate(state)
    return ReplacementState()


def clone_replacement_state(
    state: ReplacementState | Dict[str, Any] | None,
) -> ReplacementState:
    return restore_replacement_state(state)


def fork_replacement_inheritance_policy(
    state: ReplacementState | Dict[str, Any] | None,
    *,
    fork_policy: ForkPolicyDecision | None = None,
    source_is_fork: bool = False,
) -> ReplacementForkInheritancePolicy:
    restored = restore_replacement_state(state)
    if not restored.has_entries():
        return ReplacementForkInheritancePolicy(
            mode="reset",
            source="empty_parent_replacement_state",
        )
    if fork_policy is not None:
        return ReplacementForkInheritancePolicy(
            mode=(
                "reset" if fork_policy.replacement_inheritance_mode == "reset" else "inherit_frozen"
            ),
            source=fork_policy.inheritance_source or "fork_policy",
        )
    return ReplacementForkInheritancePolicy(
        mode="inherit_frozen",
        source=("fork_parent_turn_state" if source_is_fork else "subagent_parent_turn_state"),
    )


def build_isolated_turn_state(
    state: TurnState | Dict[str, Any] | None,
    *,
    fork_policy: ForkPolicyDecision | None = None,
    source_is_fork: bool = False,
) -> TurnState:
    if isinstance(state, TurnState):
        parent_state = state.model_copy(deep=True)
    elif isinstance(state, dict):
        parent_state = TurnState.model_validate(state)
    else:
        return TurnState()

    child_state = TurnState()
    policy = fork_replacement_inheritance_policy(
        parent_state.replacement_state,
        fork_policy=fork_policy,
        source_is_fork=source_is_fork,
    )
    if policy.mode == "inherit_frozen":
        child_state.replacement_state = clone_replacement_state(parent_state.replacement_state)
    return child_state
