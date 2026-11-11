from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from prismclaw.engine.state.turn_state import (
    CapabilityDecisionBundle,
    CanonicalDecisionState,
    ForkPolicyDecision,
    MemoryHintPolicy,
    PermissionDecision,
    ProviderCapabilityProfile,
    ReplacementState,
    RequestAssemblyManifest,
    SandboxPolicyDecision,
    TailEventRange,
    ToolGovernanceState,
)

LEGACY_SCHEMA_VERSION = "__legacy__"
CURRENT_CAPABILITY_DECISION_BUNDLE_SCHEMA_VERSION = "capability_decision_bundle.v2"
CURRENT_CANONICAL_DECISION_SCHEMA_VERSION = "canonical_decision.v2"
CURRENT_REPLACEMENT_STATE_SCHEMA_VERSION = "replacement_state.v1"
CURRENT_REQUEST_ASSEMBLY_MANIFEST_SCHEMA_VERSION = "request_assembly_manifest.v1"
CURRENT_TURN_STATE_SCHEMA_VERSION = "turn_state.v4"
LEGACY_TURN_STATE_SCHEMA_VERSIONS = frozenset({"turn_state.v2"})

MigrationFn = Callable[[dict[str, Any]], dict[str, Any]]


class UnsupportedStateSchemaVersionError(ValueError):
    pass


def _coerce_string(value: Any, *, default: str = "") -> str:
    return value if isinstance(value, str) else default


def _coerce_optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _coerce_int(value: Any, *, default: int = 0) -> int:
    return value if isinstance(value, int) else default


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _coerce_string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        key: item
        for key, item in value.items()
        if isinstance(key, str) and isinstance(item, str)
    }


def _merge_defaults(defaults: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    merged = dict(defaults)
    merged.update(payload)
    return merged


def _normalize_tool_governance_payload(
    payload: dict[str, Any],
    *,
    current_key: str,
    legacy_key: str,
) -> dict[str, Any]:
    governance_defaults = ToolGovernanceState().model_dump(mode="json")
    governance_payload = payload.get(current_key)
    if not isinstance(governance_payload, dict):
        governance_payload = payload.get(legacy_key)
    if isinstance(governance_payload, dict):
        return _merge_defaults(governance_defaults, governance_payload)
    return dict(governance_defaults)


@dataclass(frozen=True)
class VersionedStateRegistry:
    contract_name: str
    current_version: str
    migrations: dict[str, MigrationFn] = field(default_factory=dict)

    def migrate_payload(self, payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload

        current_payload = dict(payload)
        version = current_payload.get("schema_version")
        if not isinstance(version, str) or not version:
            version = LEGACY_SCHEMA_VERSION

        seen_versions: set[str] = set()
        while version != self.current_version:
            if version in seen_versions:
                raise UnsupportedStateSchemaVersionError(
                    f"{self.contract_name} schema migration loop detected at {version!r}"
                )
            migrate = self.migrations.get(version)
            if migrate is None:
                raise UnsupportedStateSchemaVersionError(
                    f"{self.contract_name} schema version {version!r} is not supported"
                )
            seen_versions.add(version)
            current_payload = migrate(current_payload)
            version = current_payload.get("schema_version")
            if not isinstance(version, str) or not version:
                raise UnsupportedStateSchemaVersionError(
                    f"{self.contract_name} migration did not produce a valid schema_version"
                )

        return current_payload


def _migrate_legacy_canonical_decision_state(payload: dict[str, Any]) -> dict[str, Any]:
    provider_defaults = ProviderCapabilityProfile().model_dump(mode="json")
    provider_payload = payload.get("provider_capability_profile")
    if isinstance(provider_payload, dict):
        provider_profile = _merge_defaults(provider_defaults, provider_payload)
    else:
        provider_profile = {
            **provider_defaults,
            "provider_name": _coerce_optional_string(payload.get("provider_name")),
            "llm_provider": _coerce_optional_string(payload.get("llm_provider")),
            "supports_structured_context_extension": _coerce_bool(
                payload.get("supports_structured_context_extension")
            ),
            "supports_tool_search": _coerce_bool(payload.get("supports_tool_search")),
            "supports_deferred_tools": _coerce_bool(payload.get("supports_deferred_tools")),
            "header_placement_policy": _coerce_string(
                payload.get("header_placement_policy"),
                default=provider_defaults["header_placement_policy"],
            ),
        }

    tool_governance = _normalize_tool_governance_payload(
        payload,
        current_key="tool_governance_state",
        legacy_key="tool_visibility_decision",
    )

    permission_defaults = PermissionDecision().model_dump(mode="json")
    permission_payload = payload.get("permission_decision")
    if isinstance(permission_payload, dict):
        permission_decision = _merge_defaults(permission_defaults, permission_payload)
    else:
        permission_decision = {
            **permission_defaults,
            "permission_tier": _coerce_string(
                payload.get("permission_tier"),
                default=permission_defaults["permission_tier"],
            ),
            "source": _coerce_string(
                payload.get("permission_source"),
                default=permission_defaults["source"],
            ),
        }

    sandbox_defaults = SandboxPolicyDecision().model_dump(mode="json")
    sandbox_payload = payload.get("sandbox_policy_decision")
    if isinstance(sandbox_payload, dict):
        sandbox_policy_decision = _merge_defaults(sandbox_defaults, sandbox_payload)
    else:
        sandbox_policy_decision = dict(sandbox_defaults)

    return {
        "schema_version": CURRENT_CANONICAL_DECISION_SCHEMA_VERSION,
        "cache_baseline_id": _coerce_string(payload.get("cache_baseline_id")),
        "tool_pool_id": _coerce_string(payload.get("tool_pool_id")),
        "prepared_turn_digest": _coerce_string(payload.get("prepared_turn_digest")),
        "replacement_state_digest": _coerce_optional_string(
            payload.get("replacement_state_digest")
        ),
        "provider_capability_profile": provider_profile,
        "tool_governance_state": tool_governance,
        "permission_decision": permission_decision,
        "sandbox_policy_decision": sandbox_policy_decision,
        "policy_latches": payload.get("policy_latches")
        if isinstance(payload.get("policy_latches"), dict)
        else {},
    }


def _migrate_canonical_decision_state_v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(payload)
    migrated["schema_version"] = CURRENT_CANONICAL_DECISION_SCHEMA_VERSION
    migrated["tool_governance_state"] = _normalize_tool_governance_payload(
        payload,
        current_key="tool_governance_state",
        legacy_key="tool_visibility_decision",
    )
    migrated.pop("tool_visibility_decision", None)
    return migrated


def _migrate_legacy_request_assembly_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    tail_defaults = TailEventRange().model_dump(mode="json")
    tail_payload = payload.get("tail_event_range")
    if isinstance(tail_payload, dict):
        tail_event_range = _merge_defaults(tail_defaults, tail_payload)
    else:
        tail_event_range = {
            **tail_defaults,
            "first_entry_id": _coerce_optional_string(payload.get("tail_first_entry_id")),
            "last_entry_id": _coerce_optional_string(payload.get("tail_last_entry_id")),
            "event_count": _coerce_int(payload.get("tail_event_count")),
        }

    applied_compactions = payload.get("applied_compactions")
    if not isinstance(applied_compactions, list):
        applied_compactions = payload.get("applied_compaction_records")
    if not isinstance(applied_compactions, list):
        applied_compactions = []

    memory_hints = payload.get("memory_hints_used")
    if not isinstance(memory_hints, list):
        memory_hints = payload.get("memory_hints")

    defaults = RequestAssemblyManifest().model_dump(mode="json")
    return {
        "schema_version": CURRENT_REQUEST_ASSEMBLY_MANIFEST_SCHEMA_VERSION,
        "baseline_block_ids": _coerce_string_list(payload.get("baseline_block_ids")),
        "tail_event_range": tail_event_range,
        "projection_revision": _coerce_string(payload.get("projection_revision")),
        "applied_compactions": applied_compactions,
        "memory_hints_used": _coerce_string_list(memory_hints),
        "fork_inheritance_source": _coerce_optional_string(
            payload.get("fork_inheritance_source")
            if payload.get("fork_inheritance_source") is not None
            else payload.get("fork_source")
        ),
        "resume_rebuild_policy": _coerce_string(
            payload.get("resume_rebuild_policy")
            if payload.get("resume_rebuild_policy") is not None
            else payload.get("resume_strategy"),
            default=defaults["resume_rebuild_policy"],
        ),
        "fork_clone_policy": _coerce_string(
            payload.get("fork_clone_policy"),
            default=defaults["fork_clone_policy"],
        ),
        "fork_capability_narrowing": _coerce_string(
            payload.get("fork_capability_narrowing"),
            default=defaults["fork_capability_narrowing"],
        ),
        "fork_memory_visibility": _coerce_string(
            payload.get("fork_memory_visibility"),
            default=defaults["fork_memory_visibility"],
        ),
        "replacement_inheritance_mode": _coerce_string(
            payload.get("replacement_inheritance_mode"),
            default=defaults["replacement_inheritance_mode"],
        ),
    }


def _migrate_legacy_replacement_state(payload: dict[str, Any]) -> dict[str, Any]:
    defaults = ReplacementState().model_dump(mode="json")
    if "seen_ids" in payload or "replacements" in payload:
        seen_ids = set(_coerce_string_list(payload.get("seen_ids")))
        replacements = _coerce_string_map(payload.get("replacements"))
        seen_ids.update(replacements.keys())
        return {
            "schema_version": CURRENT_REPLACEMENT_STATE_SCHEMA_VERSION,
            "seen_ids": sorted(seen_ids),
            "replacements": replacements,
        }

    legacy_replacements = _coerce_string_map(payload)
    return {
        "schema_version": CURRENT_REPLACEMENT_STATE_SCHEMA_VERSION,
        "seen_ids": sorted(legacy_replacements.keys()),
        "replacements": legacy_replacements,
    } if legacy_replacements else defaults


def _migrate_legacy_capability_decision_bundle(payload: dict[str, Any]) -> dict[str, Any]:
    return _migrate_capability_decision_bundle_v1_to_v2(payload)


def _migrate_capability_decision_bundle_v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    migrated = dict(payload)
    migrated["schema_version"] = CURRENT_CAPABILITY_DECISION_BUNDLE_SCHEMA_VERSION
    migrated["tool_governance"] = _normalize_tool_governance_payload(
        payload,
        current_key="tool_governance",
        legacy_key="tool_visibility",
    )
    migrated.pop("tool_visibility", None)
    return migrated


CANONICAL_DECISION_STATE_REGISTRY = VersionedStateRegistry(
    contract_name="CanonicalDecisionState",
    current_version=CURRENT_CANONICAL_DECISION_SCHEMA_VERSION,
    migrations={
        LEGACY_SCHEMA_VERSION: _migrate_legacy_canonical_decision_state,
        "canonical_decision.v1": _migrate_canonical_decision_state_v1_to_v2,
    },
)

CAPABILITY_DECISION_BUNDLE_REGISTRY = VersionedStateRegistry(
    contract_name="CapabilityDecisionBundle",
    current_version=CURRENT_CAPABILITY_DECISION_BUNDLE_SCHEMA_VERSION,
    migrations={
        LEGACY_SCHEMA_VERSION: _migrate_legacy_capability_decision_bundle,
        "capability_decision_bundle.v1": _migrate_capability_decision_bundle_v1_to_v2,
    },
)

REQUEST_ASSEMBLY_MANIFEST_REGISTRY = VersionedStateRegistry(
    contract_name="RequestAssemblyManifest",
    current_version=CURRENT_REQUEST_ASSEMBLY_MANIFEST_SCHEMA_VERSION,
    migrations={
        LEGACY_SCHEMA_VERSION: _migrate_legacy_request_assembly_manifest,
    },
)

REPLACEMENT_STATE_REGISTRY = VersionedStateRegistry(
    contract_name="ReplacementState",
    current_version=CURRENT_REPLACEMENT_STATE_SCHEMA_VERSION,
    migrations={
        LEGACY_SCHEMA_VERSION: _migrate_legacy_replacement_state,
    },
)

TURN_STATE_MIGRATIONS: dict[str, MigrationFn] = {}


def _migrate_turn_state_to_current(payload: dict[str, Any]) -> dict[str, Any]:
    current_payload = dict(payload)
    schema_version = current_payload.get("schema_version")
    if not isinstance(schema_version, str):
        return current_payload
    if schema_version in LEGACY_TURN_STATE_SCHEMA_VERSIONS:
        return current_payload

    seen_versions: set[str] = set()
    while schema_version != CURRENT_TURN_STATE_SCHEMA_VERSION:
        if schema_version in seen_versions:
            raise UnsupportedStateSchemaVersionError(
                f"TurnState schema migration loop detected at {schema_version!r}"
            )
        migrate = TURN_STATE_MIGRATIONS.get(schema_version)
        if migrate is None:
            raise UnsupportedStateSchemaVersionError(
                f"TurnState schema version {schema_version!r} is not supported"
            )
        seen_versions.add(schema_version)
        current_payload = migrate(current_payload)
        schema_version = current_payload.get("schema_version")
        if not isinstance(schema_version, str) or not schema_version:
            raise UnsupportedStateSchemaVersionError(
                "TurnState migration did not produce a valid schema_version"
            )
    return current_payload


def migrate_turn_state_substates(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload

    migrated = _migrate_turn_state_to_current(payload)
    schema_version = migrated.get("schema_version")
    if not isinstance(schema_version, str):
        return migrated
    if schema_version in LEGACY_TURN_STATE_SCHEMA_VERSIONS:
        return migrated
    migrated = dict(migrated)
    if "canonical_decision_state" in migrated:
        migrated["canonical_decision_state"] = CANONICAL_DECISION_STATE_REGISTRY.migrate_payload(
            migrated.get("canonical_decision_state")
        )
    if "request_assembly_manifest" in migrated:
        migrated["request_assembly_manifest"] = REQUEST_ASSEMBLY_MANIFEST_REGISTRY.migrate_payload(
            migrated.get("request_assembly_manifest")
        )
    if "replacement_state" in migrated:
        migrated["replacement_state"] = REPLACEMENT_STATE_REGISTRY.migrate_payload(
            migrated.get("replacement_state")
        )
    migrated["capability_decision_bundle"] = _migrate_or_build_capability_decision_bundle(
        migrated.get("capability_decision_bundle"),
        canonical_decision_state_payload=migrated.get("canonical_decision_state"),
        request_assembly_manifest_payload=migrated.get("request_assembly_manifest"),
    )
    return migrated


def migrate_turn_state_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload

    current_payload = _migrate_turn_state_to_current(payload)
    try:
        return migrate_turn_state_substates(current_payload)
    except UnsupportedStateSchemaVersionError:
        migrated = dict(current_payload)
        if migrated.get("schema_version") != CURRENT_TURN_STATE_SCHEMA_VERSION:
            raise
        if "canonical_decision_state" in migrated:
            migrated["canonical_decision_state"] = _migrate_turn_state_substate_or_default(
                field_name="canonical_decision_state",
                payload=migrated.get("canonical_decision_state"),
                default_payload=CanonicalDecisionState().model_dump(mode="json"),
            )
        if "request_assembly_manifest" in migrated:
            migrated["request_assembly_manifest"] = _migrate_turn_state_substate_or_default(
                field_name="request_assembly_manifest",
                payload=migrated.get("request_assembly_manifest"),
                default_payload=RequestAssemblyManifest().model_dump(mode="json"),
            )
        if "replacement_state" in migrated:
            migrated["replacement_state"] = _migrate_turn_state_substate_or_default(
                field_name="replacement_state",
                payload=migrated.get("replacement_state"),
                default_payload=ReplacementState().model_dump(mode="json"),
            )
        migrated["capability_decision_bundle"] = _migrate_or_build_capability_decision_bundle(
            migrated.get("capability_decision_bundle"),
            canonical_decision_state_payload=migrated.get("canonical_decision_state"),
            request_assembly_manifest_payload=migrated.get("request_assembly_manifest"),
        )
        return migrated


def migrate_turn_state_substate_payload(
    *,
    field_name: str,
    payload: Any,
) -> Any:
    if field_name == "canonical_decision_state":
        return CANONICAL_DECISION_STATE_REGISTRY.migrate_payload(payload)
    if field_name == "request_assembly_manifest":
        return REQUEST_ASSEMBLY_MANIFEST_REGISTRY.migrate_payload(payload)
    if field_name == "replacement_state":
        return REPLACEMENT_STATE_REGISTRY.migrate_payload(payload)
    return payload


def _migrate_turn_state_substate_or_default(
    *,
    field_name: str,
    payload: Any,
    default_payload: dict[str, Any],
) -> dict[str, Any]:
    try:
        migrated = migrate_turn_state_substate_payload(field_name=field_name, payload=payload)
    except UnsupportedStateSchemaVersionError:
        return default_payload
    return migrated if isinstance(migrated, dict) else default_payload


def _migrate_or_build_capability_decision_bundle(
    payload: Any,
    *,
    canonical_decision_state_payload: Any,
    request_assembly_manifest_payload: Any,
) -> dict[str, Any]:
    canonical = CanonicalDecisionState.model_validate(
        canonical_decision_state_payload or CanonicalDecisionState().model_dump(mode="json")
    )
    manifest = RequestAssemblyManifest.model_validate(
        request_assembly_manifest_payload or RequestAssemblyManifest().model_dump(mode="json")
    )
    default_bundle = CapabilityDecisionBundle.from_contract(
        provider_capability_profile=canonical.provider_capability_profile,
        tool_governance=canonical.tool_governance_state,
        permission=canonical.permission_decision,
        sandbox_policy=canonical.sandbox_policy_decision,
        memory_hint_policy=MemoryHintPolicy.from_manifest(manifest),
        fork_policy=ForkPolicyDecision.from_manifest(manifest),
    )
    if not isinstance(payload, dict):
        return default_bundle.model_dump(mode="json")
    try:
        normalized_payload = CAPABILITY_DECISION_BUNDLE_REGISTRY.migrate_payload(payload)
    except UnsupportedStateSchemaVersionError:
        return default_bundle.model_dump(mode="json")
    if not isinstance(normalized_payload, dict):
        return default_bundle.model_dump(mode="json")
    merged = _merge_defaults(default_bundle.model_dump(mode="json"), normalized_payload)
    if not _coerce_string(merged.get("decision_digest")):
        return default_bundle.model_dump(mode="json")
    return merged


__all__ = [
    "CAPABILITY_DECISION_BUNDLE_REGISTRY",
    "CANONICAL_DECISION_STATE_REGISTRY",
    "CURRENT_CAPABILITY_DECISION_BUNDLE_SCHEMA_VERSION",
    "CURRENT_CANONICAL_DECISION_SCHEMA_VERSION",
    "CURRENT_REPLACEMENT_STATE_SCHEMA_VERSION",
    "CURRENT_REQUEST_ASSEMBLY_MANIFEST_SCHEMA_VERSION",
    "CURRENT_TURN_STATE_SCHEMA_VERSION",
    "LEGACY_SCHEMA_VERSION",
    "LEGACY_TURN_STATE_SCHEMA_VERSIONS",
    "REPLACEMENT_STATE_REGISTRY",
    "REQUEST_ASSEMBLY_MANIFEST_REGISTRY",
    "UnsupportedStateSchemaVersionError",
    "VersionedStateRegistry",
    "migrate_turn_state_payload",
    "migrate_turn_state_substate_payload",
    "migrate_turn_state_substates",
]
